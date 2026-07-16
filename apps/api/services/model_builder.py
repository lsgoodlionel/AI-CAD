"""工程 3D 模型基座场景构建（楼层堆叠骨架 + 贴图渲染 + 成果标记层）。

build_scene 聚合 drawings + 每图最新 ai_review_issues + 最近一次
review_batches.cross_findings，组装蓝图第 4 节 scene 契约与 assets 索引：

- 贴图渲染：PDF 首页 fitz dpi≈110（最长边 ≤1600px PNG）；DXF 走
  ezdxf.addons.drawing matplotlib 后端；DWG 先经 dwg_support.ensure_dxf；
  IFC → glb（ifcopenshell，缺失时 stats 记 ifc_skipped）。
- 任何单图渲染失败 → image_key=""（前端线框占位），绝不中断整体。
- 渲染/上传均放线程池执行（与 vision_engine 同模式）。
- markers 坐标：hash(axes 文本 or issue_id) → [0.1,0.9]²，
  同 axes 簇 0.02 步进偏移，保证重建后位置稳定。

蓝图：docs/MODEL_BASE_BLUEPRINT.md 第 4/7 节。
"""
import asyncio
import hashlib
import io
import json
import logging
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

from core.ai_review.dwg_support import ensure_dxf
from core.config import settings
from core.storage import get_file_bytes, upload_file
from services import (
    model_annotations,
    model_component_sections,
    model_elements,
    model_ifc_integration,
    model_semantics,
    model_story,
    model_story_manual,
    model_topology,
    section_z_recovery,
    vlm_semantics,
)
from services.drawing_semantics import extract_semantic_candidates
from services.drawing_view_classifier import classify_view_type
from services.floor_parser import parse_floor
from services.model_lod import ModelScopeEvidence, aggregate_lod_modes, evaluate_lod_capability

logger = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=2)
# 单张剖面标高抽取超时：矢量路径秒级；OCR 兜底路径（矢量文本抽不到标高时）
# 首图含模型加载 ~10s + 大图分块识别 ~25s，须给足余量
_SECTION_TIMEOUT_SEC = 90

# ── 剖面标高 VLM 第二源兜底（后续工作 item1，灰度开关）───────────
# 默认关闭：远程 VLM 单图推理 ~25s，绝不能拖慢常规 7 分钟建模主链。显式设
# 1/true/yes 开启；仅结构专业剖面 + 矢量/OCR 标高不足时才会触发。
_VLM_SECTION_Z_ENV = "VLM_SECTION_Z_ENABLED"
# 与 section_z_recovery._MIN_PRIMARY_MARKS 同口径：主源标高数低于此才需要 VLM 补充
_VLM_SECTION_Z_MIN_MARKS = 2
# 单图 VLM 读图超时（秒）：留够 qwen3.5 ~25s 推理余量，但不能无界拖慢构建
_VLM_SECTION_Z_TIMEOUT_SEC = 45
# 单次构建最多送 VLM 的剖面数（保护主链：大套图不会因大量薄弱剖面拖垮构建，
# 实测歌剧院仅 2 张真建筑结构剖面，此上限留足余量）
_VLM_SECTION_Z_MAX_DRAWINGS = 6
# VLM 自报置信度低于此律绝不采信（铁律：绝不虚高，宁缺毋滥）
_VLM_SECTION_Z_MIN_CONFIDENCE = 0.6
# VLM 读图渲染 PDF 首页的 dpi：只需看清标高标注文字，读图内部还会二次缩放，
# 给个够用的中等值即可（对齐 scripts/model3d/vlm_read_drawing.py 默认值）
_VLM_SECTION_Z_RENDER_DPI = 150


def _vlm_section_z_enabled() -> bool:
    """灰度开关读取（env `VLM_SECTION_Z_ENABLED`，缺省关）。"""
    return os.environ.get(_VLM_SECTION_Z_ENV, "").strip().lower() in ("1", "true", "yes")

RENDER_DPI = 110
MAX_TEXTURE_PX = 1600
# 全量套图保护：超大矢量 PDF 渲染可卡死线程（无法强杀），从源头限制
MAX_RENDER_FILE_MB = 25          # 超此大小跳过贴图（保留线框面板）
RENDER_TIMEOUT_SEC = 90          # 单张渲染超时（超时线程自然结束，主流程继续）
MAX_TEXTURES_PER_PROJECT = 400   # 全项目贴图渲染上限（其余线框，控构建时长与前端负载）
DXF_FIG_SIZE = (16, 12)     # 英寸
DXF_FIG_DPI = 100
MARKER_TITLE_MAX = 80
COORD_MIN, COORD_MAX = 0.1, 0.9
CLUSTER_STEP = 0.02
SEVERITY_KEYS = ("critical", "major", "minor", "info")
# 成果标记按严重度封顶:避免上万条问题全部布点导致视口红点堆叠、可读性差。
# 各级保留代表性样本(总量 ≤ ~1500),完整问题数仍在 stats.by_severity 与 AI 报告中。
MAX_MARKERS_PER_SEVERITY = {"critical": 500, "major": 500, "minor": 300, "info": 200}
CROSS_LINK_KINDS = ("接口缺图", "问题聚类", "版本冲突", "重复图号")

_PROJECT_SQL = "SELECT id, name FROM projects WHERE id=:project_id"

_DRAWINGS_SQL = """
SELECT id, drawing_no, title, discipline, status, current_stage, file_key
FROM drawings
WHERE project_id=:project_id
ORDER BY drawing_no, created_at
"""

_ISSUES_SQL = """
SELECT r.drawing_id, i.id AS issue_id, i.severity, i.description,
       i.discipline_code, i.location_json
FROM ai_review_issues i
JOIN (
    SELECT DISTINCT ON (drawing_id) id, drawing_id
    FROM ai_review_reports
    WHERE drawing_id::text = ANY(:drawing_ids)
    ORDER BY drawing_id, created_at DESC
) r ON r.id = i.report_id
ORDER BY i.created_at
"""

_CROSS_SQL = """
SELECT cross_findings FROM review_batches
WHERE project_id=:project_id AND cross_findings IS NOT NULL
ORDER BY created_at DESC LIMIT 1
"""


def _safe_json(value: Any, default: Any) -> Any:
    """JSONB 字段经驱动可能返回 str，安全解析；类型不符时返回默认值。"""
    if value is None:
        return default
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return default
    if not isinstance(value, type(default)):
        return default
    return value


async def _load_annotation_overrides(db, project_id: str) -> dict[str, dict[str, Any]]:
    """最小可测试 hook：默认尝试读取人工标注，失败时回退为空。"""
    try:
        return await model_annotations.load_annotation_overrides(db, project_id)
    except Exception as exc:  # noqa: BLE001 - 标注表未部署时不阻断模型构建
        logger.info("[ModelBuilder] 标注覆盖读取失败，回退自动识别: %s", exc)
        return {}


# ── 贴图渲染（线程池内同步执行）───────────────────────────────

def _render_pdf_sync(data: bytes) -> tuple[bytes, int, int]:
    """PDF 首页 → PNG（dpi≈110，最长边不超过 1600px）。"""
    import fitz

    doc = fitz.open(stream=data, filetype="pdf")
    try:
        page = doc[0]
        zoom = RENDER_DPI / 72.0
        longest = max(page.rect.width, page.rect.height) * zoom
        if longest > MAX_TEXTURE_PX:
            zoom *= MAX_TEXTURE_PX / longest
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        return pix.tobytes("png"), pix.width, pix.height
    finally:
        doc.close()


def _render_dxf_sync(data: bytes) -> tuple[bytes, int, int]:
    """DXF → PNG（ezdxf.addons.drawing matplotlib 后端）。"""
    import matplotlib
    matplotlib.use("Agg")
    import ezdxf
    import matplotlib.pyplot as plt
    from ezdxf.addons.drawing import Frontend, RenderContext
    from ezdxf.addons.drawing.matplotlib import MatplotlibBackend

    with tempfile.NamedTemporaryFile(suffix=".dxf", delete=False) as tmp:
        tmp.write(data)
        path = tmp.name
    try:
        doc = ezdxf.readfile(path)
        fig = plt.figure(figsize=DXF_FIG_SIZE)
        ax = fig.add_axes([0, 0, 1, 1])
        Frontend(RenderContext(doc), MatplotlibBackend(ax)).draw_layout(doc.modelspace())
        buffer = io.BytesIO()
        fig.savefig(buffer, format="png", dpi=DXF_FIG_DPI)
        plt.close(fig)
        width = int(DXF_FIG_SIZE[0] * DXF_FIG_DPI)
        height = int(DXF_FIG_SIZE[1] * DXF_FIG_DPI)
        return buffer.getvalue(), width, height
    finally:
        _unlink_quiet(path)


def _unlink_quiet(*paths: str) -> None:
    """静默删除临时文件（清理失败不影响主流程）。"""
    for path in paths:
        try:
            os.unlink(path)
        except OSError:
            pass


def _render_and_upload_sync(
    project_id: str, drawing_id: str, file_key: str, file_ext: str
) -> dict:
    """下载 → 渲染 → 上传 MinIO，返回 asset 描述；失败由上层降级。"""
    data = get_file_bytes(file_key)
    if len(data) > MAX_RENDER_FILE_MB * 1024 * 1024:
        raise RuntimeError(f"RENDER_SKIPPED_TOO_LARGE:{len(data) >> 20}MB")
    ext = file_ext
    if ext == "dwg":
        data, ext, warning = ensure_dxf(data, ext)
        if warning:
            raise RuntimeError(warning)
    if ext == "pdf":
        png, width, height = _render_pdf_sync(data)
        parser = "pdf"
    elif ext == "dxf":
        png, width, height = _render_dxf_sync(data)
        parser = "dxf"
    else:
        raise ValueError(f"UNSUPPORTED_RENDER_EXT:{ext}")
    image_key = f"projects/{project_id}/model_assets/{drawing_id}.png"
    upload_file(png, image_key, "image/png")
    return {"image_key": image_key, "width": width, "height": height, "parser": parser}


def _ifc_to_glb_sync(project_id: str, drawing_id: str, file_key: str) -> str:
    """IFC → glb（依赖 ifcopenshell；缺失抛 ImportError 由上层记 ifc_skipped）。"""
    import ifcopenshell
    import ifcopenshell.geom

    if not hasattr(ifcopenshell.geom, "serializers"):
        raise ImportError("ifcopenshell serializers 不可用")
    data = get_file_bytes(file_key)
    with tempfile.NamedTemporaryFile(suffix=".ifc", delete=False) as src:
        src.write(data)
        src_path = src.name
    dst_path = src_path.replace(".ifc", ".glb")
    try:
        model = ifcopenshell.open(src_path)
        geom_settings = ifcopenshell.geom.settings()
        geom_settings.set(geom_settings.USE_WORLD_COORDS, True)
        serialiser = ifcopenshell.geom.serializers.gltf(dst_path, geom_settings)
        serialiser.setFile(model)
        iterator = ifcopenshell.geom.iterator(geom_settings, model)
        if iterator.initialize():
            while True:
                serialiser.write(iterator.get())
                if not iterator.next():
                    break
        serialiser.finalize()
        with open(dst_path, "rb") as handle:
            glb = handle.read()
        gltf_key = f"projects/{project_id}/model_assets/{drawing_id}.glb"
        upload_file(glb, gltf_key, "model/gltf-binary")
        return gltf_key
    finally:
        _unlink_quiet(src_path, dst_path)


# ── 资产构建（渲染降级 + IFC 可选）───────────────────────────

def _empty_asset(parser: str) -> dict:
    """无贴图占位（前端渲染线框）。"""
    return {"image_key": "", "width": 0, "height": 0, "parser": parser}


async def _build_one_asset(
    loop: asyncio.AbstractEventLoop, project_id: str, drawing: dict
) -> tuple[dict, str | None, bool]:
    """单图资产：返回 (asset, gltf_key, ifc_skipped)；任何失败降级不抛出。"""
    drawing_id = str(drawing["id"])
    file_key = drawing.get("file_key") or ""
    ext = file_key.rsplit(".", 1)[-1].lower() if "." in file_key else ""
    if not file_key:
        return _empty_asset("none"), None, False
    if ext == "ifc":
        try:
            gltf_key = await loop.run_in_executor(
                _executor, _ifc_to_glb_sync, project_id, drawing_id, file_key
            )
            return _empty_asset("ifc"), gltf_key, False
        except ImportError:
            logger.warning("[ModelBuilder] ifcopenshell 不可用，跳过 IFC: %s", drawing_id)
            return _empty_asset("ifc"), None, True
        except Exception as exc:  # noqa: BLE001 — 单图失败必须降级
            logger.warning("[ModelBuilder] IFC 转换失败 %s: %s", drawing_id, exc)
            return _empty_asset("ifc"), None, False
    try:
        asset = await asyncio.wait_for(
            loop.run_in_executor(
                _executor, _render_and_upload_sync, project_id, drawing_id, file_key, ext
            ),
            timeout=RENDER_TIMEOUT_SEC,
        )
        return asset, None, False
    except Exception as exc:  # noqa: BLE001 — 单图失败/超时必须降级
        logger.warning("[ModelBuilder] 贴图渲染失败 %s: %s", drawing_id, exc)
        return _empty_asset(ext or "none"), None, False


def _progress_payload(
    stage: str, stage_label: str, current: str, done: int, total: int
) -> dict:
    return {
        "stage": stage,
        "stage_label": stage_label,
        "current": current,
        "done": done,
        "total": total,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


async def _notify(progress_cb, payload: dict) -> None:
    """进度回调（可选）；回调异常绝不影响构建。"""
    if progress_cb is None:
        return
    try:
        await progress_cb(payload)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[ModelBuilder] 进度回调失败: %s", exc)


async def _build_assets(
    project_id: str, drawings: list[dict], progress_cb=None
) -> tuple[dict, list[dict], bool]:
    """全部图纸资产：assets 索引 + ifc_models 列表 + ifc_skipped 标记。"""
    loop = asyncio.get_event_loop()
    assets: dict[str, dict] = {}
    ifc_models: list[dict] = []
    ifc_skipped = False
    total = len(drawings)
    rendered = 0
    for index, drawing in enumerate(drawings):
        await _notify(progress_cb, _progress_payload(
            "render", "读取图纸内容并渲染贴图",
            str(drawing.get("drawing_no") or ""), index, total,
        ))
        if rendered >= MAX_TEXTURES_PER_PROJECT:
            # 超过全项目贴图上限：其余图纸线框占位（保构建时长与前端负载）
            assets[str(drawing["id"])] = _empty_asset("capped")
            continue
        asset, gltf_key, skipped = await _build_one_asset(loop, project_id, drawing)
        if asset.get("image_key"):
            rendered += 1
        assets[str(drawing["id"])] = asset
        if gltf_key:
            ifc_models.append({"drawing_id": str(drawing["id"]), "gltf_key": gltf_key})
        ifc_skipped = ifc_skipped or skipped
    return assets, ifc_models, ifc_skipped


# ── 楼层与标记 ────────────────────────────────────────────────

def _issue_levels(issues: list[dict]) -> list[str]:
    """收集问题定位中的楼层文本列表。"""
    levels: list[str] = []
    for issue in issues:
        location = _safe_json(issue.get("location_json"), {})
        levels.extend(str(level) for level in location.get("levels") or [])
    return levels


def _drawing_entry(
    drawing: dict,
    issues: list[dict],
    asset: dict,
    assignment: dict[str, Any] | None = None,
) -> dict:
    """楼层内单张图纸条目（蓝图第 4 节 floors[].drawings[] 契约）。"""
    assignment = assignment or {}
    entry = {
        "drawing_id": str(drawing["id"]),
        "drawing_no": drawing.get("drawing_no") or "",
        "title": drawing.get("title") or "",
        "discipline": drawing.get("discipline") or "",
        "status": drawing.get("status") or "",
        "current_stage": drawing.get("current_stage") or "",
        "image_key": asset.get("image_key", ""),
        "issue_count": len(issues),
        "critical_count": sum(1 for i in issues if i.get("severity") == "critical"),
        "_lod_evidence": dict(drawing.get("lod_evidence") or {}),
    }
    if assignment:
        entry.update(
            {
                "building_unit_key": assignment.get("building_unit_key") or "main",
                "building_unit_display_name": assignment.get("building_unit_display_name") or "主体",
                "story_key": assignment.get("story_key") or "UNZONED",
                "story_display_name": assignment.get("story_display_name") or "未分层",
                "assignment_source": assignment.get("assignment_source") or "detected",
                "assignment_confidence": assignment.get("story_confidence")
                or assignment.get("building_unit_confidence")
                or 0.0,
            }
        )
    return entry


def _trusted_floor_keys(drawings: list[dict]) -> set[str]:
    """可信楼层集：由图名/图号直接解析出的楼层（约束 issue levels 弱信号）。"""
    trusted: set[str] = set()
    for drawing in drawings:
        for text in (drawing.get("title"), drawing.get("drawing_no")):
            parsed = parse_floor(str(text or ""))
            if parsed is not None:
                trusted.add(parsed[0])
    return trusted


def _build_floors(
    drawings: list[dict],
    issues_by_drawing: dict[str, list[dict]],
    assets: dict,
    normalization: model_story.StoryNormalizationResult,
) -> tuple[list[dict], dict[str, str]]:
    """楼层堆叠：使用 normalized story assignment 组装 floors。"""
    floors: dict[str, dict] = {}
    floor_of: dict[str, str] = {}
    for drawing in drawings:
        drawing_id = str(drawing["id"])
        issues = issues_by_drawing.get(drawing_id, [])
        assignment = normalization.drawing_assignments.get(drawing_id) or {}
        key = str(assignment.get("story_key") or "UNZONED")
        label = str(assignment.get("story_display_name") or "未分层")
        order = int(assignment.get("story_order") or 0)
        floor = floors.setdefault(
            key,
            {
                "key": key,
                "label": label,
                "elevation": order,
                "order": order,
                "elevation_m": assignment.get("normalized_elevation_m"),
                "drawings": [],
                "building_units": set(),
            },
        )
        if floor.get("elevation_m") is None and assignment.get("normalized_elevation_m") is not None:
            floor["elevation_m"] = assignment["normalized_elevation_m"]
        floor["building_units"].add(assignment.get("building_unit_key") or "main")
        floor["drawings"].append(
            _drawing_entry(
                drawing,
                issues,
                assets.get(drawing_id, _empty_asset("none")),
                assignment,
            )
        )
        floor_of[drawing_id] = key
    ordered = sorted(floors.values(), key=lambda f: f["order"])
    for floor in ordered:
        floor["building_units"] = sorted(floor["building_units"])
    return ordered, floor_of


def _stable_point(basis: str) -> tuple[float, float]:
    """稳定伪随机布点：hash(basis) → (x,y)∈[0.1,0.9]²。"""
    digest = hashlib.md5(basis.encode("utf-8")).hexdigest()
    span = COORD_MAX - COORD_MIN
    x = COORD_MIN + int(digest[:8], 16) / 0xFFFFFFFF * span
    y = COORD_MIN + int(digest[8:16], 16) / 0xFFFFFFFF * span
    return round(x, 4), round(y, 4)


def _marker_of_issue(
    issue: dict, floor_key: str, cluster_counters: dict[tuple[str, str], int]
) -> dict:
    """单条问题 → 标记（同 axes 簇按 0.02 步进偏移）。"""
    issue_id = str(issue["issue_id"])
    location = _safe_json(issue.get("location_json"), {})
    axes = [str(a) for a in location.get("axes") or []]
    basis = axes[0] if axes else issue_id
    x, y = _stable_point(basis)
    cluster_key = (floor_key, basis)
    offset = cluster_counters.get(cluster_key, 0) * CLUSTER_STEP
    cluster_counters[cluster_key] = cluster_counters.get(cluster_key, 0) + 1
    return {
        "id": f"issue:{issue_id}",
        "type": "issue",
        "severity": issue.get("severity") or "info",
        "floor_key": floor_key,
        "x": round(min(x + offset, COORD_MAX), 4),
        "y": round(min(y + offset, COORD_MAX), 4),
        "title": str(issue.get("description") or "")[:MARKER_TITLE_MAX],
        "discipline_code": issue.get("discipline_code") or "",
        "ref": {"drawing_id": str(issue["drawing_id"]), "issue_id": issue_id},
    }


def _build_markers(
    issues_by_drawing: dict[str, list[dict]], floor_of: dict[str, str]
) -> list[dict]:
    """成果标记层：issues.location_json(levels) → floor_key + 稳定坐标。

    按严重度分组并各自封顶(见 MAX_MARKERS_PER_SEVERITY),避免上万条问题全部
    布点造成红点堆叠;严重/较大优先,各级保留代表性样本。
    """
    by_severity: dict[str, list[tuple[dict, str]]] = {key: [] for key in SEVERITY_KEYS}
    for drawing_id, issues in issues_by_drawing.items():
        fallback_key = floor_of.get(drawing_id, "UNZONED")
        for issue in issues:
            location = _safe_json(issue.get("location_json"), {})
            parsed = next(
                (parse_floor(str(lv)) for lv in location.get("levels") or []
                 if parse_floor(str(lv)) is not None),
                None,
            )
            floor_key = parsed[0] if parsed else fallback_key
            severity = str(issue.get("severity") or "info")
            by_severity.setdefault(severity, []).append((issue, floor_key))

    markers: list[dict] = []
    cluster_counters: dict[tuple[str, str], int] = {}
    for severity in SEVERITY_KEYS:
        cap = MAX_MARKERS_PER_SEVERITY.get(severity, 200)
        for issue, floor_key in by_severity.get(severity, [])[:cap]:
            markers.append(_marker_of_issue(issue, floor_key, cluster_counters))
    return markers


# ── 跨图发现 → cross_links ───────────────────────────────────

def _floor_keys_of_nos(nos: list[str], floor_by_no: dict[str, str]) -> list[str]:
    """图号列表 → 去重楼层 key 列表（保持稳定顺序）。"""
    seen: list[str] = []
    for no in nos:
        key = floor_by_no.get(str(no))
        if key and key not in seen:
            seen.append(key)
    return seen


def _build_cross_links(
    cross: dict, ids_by_no: dict[str, list[str]], floor_by_no: dict[str, str]
) -> list[dict]:
    """最近一次批次 cross_findings → 蓝图 cross_links 契约。"""
    links: list[dict] = []
    for item in cross.get("重复图号") or []:
        no = str(item.get("drawing_no") or "")
        links.append({"kind": "重复图号", "label": no,
                      "floor_keys": _floor_keys_of_nos([no], floor_by_no),
                      "drawing_ids": [str(x) for x in item.get("drawing_ids") or []]})
    for item in cross.get("版本冲突") or []:
        no = str(item.get("drawing_no") or "")
        versions = "/".join(str(v) for v in item.get("versions") or [])
        links.append({"kind": "版本冲突", "label": f"{no} 版本 {versions}",
                      "floor_keys": _floor_keys_of_nos([no], floor_by_no),
                      "drawing_ids": ids_by_no.get(no, [])})
    for item in cross.get("接口缺图") or []:
        nos = [str(r.get("drawing_no") or "") for r in item.get("referenced_by") or []]
        links.append({"kind": "接口缺图",
                      "label": f"套图缺少 {item.get('missing_discipline', '')} 图纸",
                      "floor_keys": _floor_keys_of_nos(nos, floor_by_no),
                      "drawing_ids": [i for no in nos for i in ids_by_no.get(no, [])]})
    for item in cross.get("问题聚类") or []:
        nos = [str(no) for no in item.get("drawings") or []]
        links.append({"kind": "问题聚类", "label": str(item.get("location_key") or ""),
                      "floor_keys": _floor_keys_of_nos(nos, floor_by_no),
                      "drawing_ids": [i for no in nos for i in ids_by_no.get(no, [])]})
    return links


# ── 统计与主流程 ──────────────────────────────────────────────

def _build_stats(
    drawings: list[dict],
    issues_by_drawing: dict[str, list[dict]],
    floors: list[dict],
    ifc_skipped: bool,
) -> dict:
    """场景统计：图纸/问题总量 + 严重度/专业分布 + 楼层数。"""
    by_severity = {key: 0 for key in SEVERITY_KEYS}
    by_discipline: dict[str, int] = {}
    total_issues = 0
    discipline_of = {str(d["id"]): d.get("discipline") or "" for d in drawings}
    for drawing_id, issues in issues_by_drawing.items():
        discipline = discipline_of.get(drawing_id, "")
        for issue in issues:
            total_issues += 1
            severity = issue.get("severity")
            if severity in by_severity:
                by_severity[severity] += 1
            if discipline:
                by_discipline[discipline] = by_discipline.get(discipline, 0) + 1
    stats = {
        "total_drawings": len(drawings),
        "total_issues": total_issues,
        "by_severity": by_severity,
        "by_discipline": by_discipline,
        "floors": len(floors),
    }
    if ifc_skipped:
        stats["ifc_skipped"] = True
    return stats


def _serialize_story_level(level: model_story.StoryLevel) -> dict[str, Any]:
    return {
        "building_unit_key": level.building_unit_key,
        "display_building_name": level.display_building_name,
        "story_key": level.story_key,
        "display_name": level.display_name,
        "story_order": level.story_order,
        "elevation_m": level.elevation_m,
        "height_m": level.height_m,
        "source": level.source,
        "confidence": level.confidence,
        "height_source": level.height_source,
        "height_confidence": level.height_confidence,
        "height_estimated": level.height_estimated,
        "height_note": level.height_note,
    }


def _serialize_quality_issue(issue: model_story.ModelQualityIssue) -> dict[str, Any]:
    return {
        "issue_type": issue.issue_type,
        "severity": issue.severity,
        "message": issue.message,
        "drawing_id": issue.drawing_id,
        "building_unit_key": issue.building_unit_key,
        "story_key": issue.story_key,
        "payload": issue.payload,
    }


def _quality_payload(
    normalization: model_story.StoryNormalizationResult,
    extra_issues: list | None = None,
) -> dict[str, Any]:
    # extra_issues：normalization 之外链路的质量问题（如剖面 z 恢复的
    # z_story_count_mismatch / z_anchor_mismatch），此前被静默丢弃。
    all_issues = [*normalization.issues, *(extra_issues or [])]
    story_conflicts = [
        _serialize_quality_issue(issue)
        for issue in all_issues
        if issue.issue_type == "story_spacing_too_small"
    ]
    low_confidence_units = [
        unit for unit in normalization.building_units
        if float(unit.get("confidence") or 0) < 0.6
    ]
    return {
        "building_units": normalization.building_units,
        "story_tables": {
            key: [_serialize_story_level(level) for level in levels]
            for key, levels in normalization.stories_by_building.items()
        },
        "unclassified_drawings": normalization.unclassified_drawings,
        "unassigned_story_count": len(normalization.unclassified_drawings),
        "pending_manual_count": len(normalization.unclassified_drawings),
        "story_conflict_count": len(story_conflicts),
        "story_conflicts": story_conflicts,
        "low_confidence_building_units": low_confidence_units,
        "issues": [_serialize_quality_issue(issue) for issue in all_issues],
    }


def _semantic_scene_payload(drawings: list[dict]) -> dict[str, Any]:
    candidates = []
    unassigned: list[dict[str, Any]] = []
    for drawing in drawings:
        drawing_candidates = extract_semantic_candidates(drawing)
        if not drawing_candidates:
            unassigned.append(
                {
                    "drawing_id": str(drawing.get("id") or ""),
                    "drawing_no": str(drawing.get("drawing_no") or ""),
                    "title": str(drawing.get("title") or ""),
                    "reason": "semantic_unassigned",
                }
            )
        candidates.extend(drawing_candidates)
    graph = model_semantics.resolve_candidates(candidates)
    return {
        "semantic_tree": graph.as_dict(),
        "unassigned_drawings": unassigned,
        "semantic_version": graph.version,
    }


async def _fetch_inputs(db, project_id: str) -> tuple[dict, list[dict], dict, dict]:
    """聚合查询：项目 + 图纸 + 每图最新报告问题 + 最近一次跨图发现。"""
    project = await db.fetch_one(_PROJECT_SQL, {"project_id": project_id})
    if project is None:
        raise ValueError(f"项目不存在: {project_id}")
    drawings = [dict(row) for row in await db.fetch_all(_DRAWINGS_SQL, {"project_id": project_id})]
    issues_by_drawing: dict[str, list[dict]] = {}
    if drawings:
        rows = await db.fetch_all(
            _ISSUES_SQL, {"drawing_ids": [str(d["id"]) for d in drawings]}
        )
        for row in rows:
            issues_by_drawing.setdefault(str(row["drawing_id"]), []).append(dict(row))
    batch = await db.fetch_one(_CROSS_SQL, {"project_id": project_id})
    cross = _safe_json(batch["cross_findings"], {}) if batch is not None else {}
    return dict(project), drawings, issues_by_drawing, cross


async def _attach_floor_elements(
    floors: list[dict], drawings: list[dict], floor_of: dict[str, str],
    progress_cb=None,
) -> int:
    """为每楼层识别构件（V2）：floor 增 elements/element_stats；返回 YOLO 设备数。"""
    drawings_by_floor: dict[str, list[dict]] = {}
    for drawing in drawings:
        key = floor_of.get(str(drawing["id"]), "UNZONED")
        drawings_by_floor.setdefault(key, []).append(drawing)

    yolo_total = 0
    for index, floor in enumerate(floors):
        await _notify(progress_cb, _progress_payload(
            "recognize", "识别楼层构件（柱/墙/梁/板/管线/设备）",
            str(floor.get("label") or floor["key"]), index, len(floors),
        ))
        if floor["key"] == "UNZONED":
            # 未分层图纸(多为详图/系统图/说明,非平面图)未定位到楼层,不注入楼层
            # 构件几何——否则其数千个"构件"会在基座平面堆叠成噪声(挤在一起)。
            # 这些图在待人工识别队列中,人工归层后重建即可正确落层。
            floor["elements"] = {k: [] for k in model_elements.EMPTY_ELEMENTS}
            floor["element_stats"] = model_elements.element_stats(floor["elements"])
            floor["_elevation_candidates"] = []
            floor["_lod_registered_drawings"] = 0
            floor["_lod_evidence"] = {}
            continue
        floor_drawings = drawings_by_floor.get(floor["key"], [])
        try:
            elements, yolo_count, meta = await model_elements.build_floor_elements(
                _executor, floor_drawings, get_file_bytes
            )
        except Exception as exc:  # noqa: BLE001 — 构件层失败回退贴图
            logger.warning("[ModelBuilder] 楼层构件识别失败 %s: %s", floor["key"], exc)
            elements, yolo_count, meta = (
                {k: [] for k in model_elements.EMPTY_ELEMENTS}, 0, {},
            )
        floor["elements"] = elements
        floor["element_stats"] = model_elements.element_stats(elements)
        # E2 轴网层：楼层配准参考轴网入 scene（前端「轴网」构件图层数据源）
        floor["axes"] = meta.get("axes") or None
        floor["_elevation_candidates"] = meta.get("elevations") or []
        floor["_lod_registered_drawings"] = int(meta.get("registered") or 0)
        floor["_lod_evidence"] = (
            dict(meta["lod_evidence"])
            if isinstance(meta.get("lod_evidence"), dict)
            else {}
        )
        yolo_total += yolo_count
    _apply_real_elevations(floors)
    return yolo_total


# 建筑包络裁剪参数:柱定义可信包络,构件质心超出即视为比例/配准离群
_ENVELOPE_MIN_COLUMNS = 20        # 柱点少于此值不足以定包络,跳过裁剪
_ENVELOPE_MARGIN_RATIO = 0.35     # 包络在柱 p2~p98 范围外再放宽的比例
_ENVELOPE_MARGIN_MIN_M = 30.0     # 放宽下限(米)
_MAX_ELEMENT_SPAN_M = 300.0       # 单构件自身跨度上限(超出=比例错误的离群构件)


def _robust_bounds(values: list[float]) -> tuple[float, float]:
    """分位数抗离群边界(p2~p98)+ 放宽边距。"""
    ordered = sorted(values)
    count = len(ordered)
    low = ordered[int(count * 0.02)]
    high = ordered[min(int(count * 0.98), count - 1)]
    margin = max((high - low) * _ENVELOPE_MARGIN_RATIO, _ENVELOPE_MARGIN_MIN_M)
    return low - margin, high + margin


def _clip_elements_to_envelope(floors: list[dict]) -> None:
    """裁掉质心落在建筑包络外的离群构件。

    机电图比例检测出错时,管线/设备坐标会冲到数千米(实测 2416m),把模型撑成
    巨大的扁平 sprawl。以可靠的**柱**坐标(分位数抗离群)建立建筑包络,凡质心
    远超包络的构件视为比例/配准离群并剔除。柱本身定义包络,全部保留。
    """
    col_x: list[float] = []
    col_y: list[float] = []
    for floor in floors:
        for column in (floor.get("elements") or {}).get("columns") or []:
            for point in column.get("outline") or []:
                if len(point) >= 2:
                    col_x.append(float(point[0]))
                    col_y.append(float(point[1]))
    if len(col_x) < _ENVELOPE_MIN_COLUMNS:
        return
    x0, x1 = _robust_bounds(col_x)
    y0, y1 = _robust_bounds(col_y)

    for floor in floors:
        elements = floor.get("elements") or {}
        for kind, items in list(elements.items()):
            kept = []
            for element in items:
                pts = [
                    p for key in ("outline", "path")
                    for p in (element.get(key) or []) if len(p) >= 2
                ]
                if not pts:
                    kept.append(element)
                    continue
                xs = [float(p[0]) for p in pts]
                ys = [float(p[1]) for p in pts]
                cx = sum(xs) / len(xs)
                cy = sum(ys) / len(ys)
                span = max(max(xs) - min(xs), max(ys) - min(ys))
                if x0 <= cx <= x1 and y0 <= cy <= y1 and span <= _MAX_ELEMENT_SPAN_M:
                    kept.append(element)
            elements[kind] = kept
        floor["element_stats"] = model_elements.element_stats(elements)


def _accumulate_manual_elevations(normalization, overrides: dict) -> dict:
    """按累加层高计算每层真实底标高,补全 override 的 elevation_bottom_m。

    标高默认按 order 独立算((order-1)×层高),改层高不会抬升上层。此处对含
    override 的单体,以 ±0.000(order=1)为锚点,用各层层高(override 优先,否则
    现层高)向上/向下累加真实底标高,写入 override,使人工层高真正生效。
    """
    default_h = model_story.DEFAULT_STORY_HEIGHT_M
    result = dict(overrides)
    affected_units = {unit for (unit, _story) in overrides}

    def height_of(unit_key: str, level) -> float:
        override = overrides.get((unit_key, level.story_key))
        if override and override.get("height_m"):
            return float(override["height_m"])
        return float(getattr(level, "height_m", None) or default_h)

    for unit_key, levels in normalization.stories_by_building.items():
        if unit_key not in affected_units:
            continue
        ordered = sorted(levels, key=lambda lv: lv.story_order)
        if not ordered:
            continue
        elevations = [0.0] * len(ordered)
        anchor = next((i for i, lv in enumerate(ordered) if lv.story_order == 1), None)
        if anchor is None:
            elevations[0] = float(getattr(ordered[0], "elevation_m", None) or 0.0)
            for i in range(1, len(ordered)):
                elevations[i] = round(elevations[i - 1] + height_of(unit_key, ordered[i - 1]), 3)
        else:
            elevations[anchor] = 0.0
            for i in range(anchor + 1, len(ordered)):
                elevations[i] = round(elevations[i - 1] + height_of(unit_key, ordered[i - 1]), 3)
            for i in range(anchor - 1, -1, -1):
                elevations[i] = round(elevations[i + 1] - height_of(unit_key, ordered[i]), 3)
        for level, elevation in zip(ordered, elevations):
            existing = result.get((unit_key, level.story_key), {})
            result[(unit_key, level.story_key)] = {
                "height_m": height_of(unit_key, level),
                "elevation_bottom_m": elevation,
                "source": existing.get("source", "manual"),
                "confidence": existing.get("confidence", 1.0),
            }
    return result


def _apply_real_elevations(floors: list[dict]) -> None:
    """由图纸标高文本推导楼层真实标高（米）→ floor.elevation_m。

    贪心单调选择：楼层按 order 升序，逐层从候选标高中选取大于下层标高的最小值；
    最低层取候选最小值。UNZONED/无候选层为 None（前端回退层序高度）。
    """
    ordered = sorted(
        (f for f in floors if f["key"] != "UNZONED"), key=lambda f: f["order"]
    )
    previous: float | None = None
    for floor in ordered:
        if floor.get("elevation_m") is not None:
            previous = float(floor["elevation_m"])
            floor.pop("_elevation_candidates", None)
            continue
        raw = floor.pop("_elevation_candidates", []) or []
        # 符号约束：地下层候选 ≤0.5（含 ±0.000 顶板），地上层候选 ≥-0.5
        order = int(floor.get("order", 0))
        candidates = sorted(
            v for v in raw
            if (v <= 0.5 if order < 0 else v >= -0.5)
        )
        chosen: float | None = None
        if candidates:
            if previous is None:
                chosen = candidates[0]
            else:
                chosen = next((v for v in candidates if v > previous + 0.5), None)
        floor["elevation_m"] = chosen
        if chosen is not None:
            previous = chosen
    for floor in floors:
        floor.pop("_elevation_candidates", None)
        floor.setdefault("elevation_m", None)


def _marker_building_keys(
    markers: list[dict],
    drawings: list[dict],
    assignments: dict[str, dict[str, Any]] | None = None,
) -> None:
    """markers 补 building_key（按所属图纸的单体）。"""
    assignments = assignments or {}
    building_by_drawing = {
        str(d["id"]): model_elements.building_of(d, assignments.get(str(d["id"])))[0]
        for d in drawings
    }
    for marker in markers:
        drawing_id = str((marker.get("ref") or {}).get("drawing_id") or "")
        marker["building_key"] = building_by_drawing.get(drawing_id, "main")


def _section_levels_sync(data: bytes, ext: str):
    """同步：字节 → 几何 → 剖面标高序列（任何失败返回 None，绝不抛）。

    PDF 且矢量文本抽不到标高时走 OCR 兜底：CAD 导出 PDF 的正文标高多为
    矢量字形（get_text 取不到），把高置信 OCR 标高 token 合成几何文本后
    重抽，完整复用标高线绑定/线性标定/置信逻辑。OCR 不可用时行为不变。
    """
    try:
        from core.model3d import geometry_extractor
        from core.model3d.section_level_extractor import extract_section_levels

        geom = (
            geometry_extractor.extract_pdf_geometry(data)
            if ext == "pdf"
            else geometry_extractor.extract_dxf_geometry(data)
        )
        levels = extract_section_levels(geom)
        if levels.marks or ext != "pdf":
            return levels
        return _section_levels_ocr_fallback(data, geom, levels)
    except Exception as exc:  # noqa: BLE001 — 剖面识别失败跳过
        logger.warning("[ModelBuilder] 剖面标高抽取跳过: %s", exc)
        return None


def _section_levels_ocr_fallback(data: bytes, geom, vector_levels):
    """OCR 标高 token → 合成几何文本 → 重抽标高。失败一律回退矢量结果。"""
    try:
        from dataclasses import replace

        from core.model3d.ocr import run_ocr
        from core.model3d.ocr.consume import as_geometry_texts
        from core.model3d.section_level_extractor import (
            SectionLevels,
            extract_section_levels,
        )

        ocr_result = run_ocr(data, "pdf")
        if not ocr_result.available:
            return vector_levels
        ocr_texts = as_geometry_texts(ocr_result)  # 仅 ≥0.8 置信标高 token
        if not ocr_texts:
            return vector_levels
        merged = replace(geom, texts=[*geom.texts, *ocr_texts])
        ocr_levels = extract_section_levels(merged)
        if not ocr_levels.marks:
            return vector_levels
        logger.info(
            "[ModelBuilder] 剖面标高 OCR 兜底命中: %d 个标高（backend=%s）",
            len(ocr_levels.marks), ocr_result.backend,
        )
        # fit 标注来源，供下游/评测追溯（marks 的置信仍由 extractor 标定逻辑给出）
        return SectionLevels(
            marks=ocr_levels.marks,
            reason=None,
            fit={**ocr_levels.fit, "ocr_fallback": True, "ocr_backend": ocr_result.backend},
        )
    except Exception as exc:  # noqa: BLE001 — OCR 兜底失败不影响矢量结果
        logger.warning("[ModelBuilder] 剖面标高 OCR 兜底跳过: %s", exc)
        return vector_levels


async def _recover_section_z(
    drawings: list[dict],
    normalization: model_story.StoryNormalizationResult,
) -> section_z_recovery.SectionZRecovery:
    """识别剖面图 → 抽标高 → 对齐平面楼层序（B-05）。无剖面时 no-op。"""
    section_drawings = [
        drawing for drawing in drawings
        if classify_view_type(drawing).view_type == "section"
    ]
    if not section_drawings:
        return section_z_recovery.SectionZRecovery()

    loop = asyncio.get_event_loop()
    levels_by_drawing: dict[str, Any] = {}
    for drawing in section_drawings:
        file_key = drawing.get("file_key") or ""
        ext = file_key.rsplit(".", 1)[-1].lower() if "." in file_key else ""
        if not file_key or ext not in ("pdf", "dxf", "dwg"):
            continue
        try:
            data = await loop.run_in_executor(_executor, get_file_bytes, file_key)
            levels = await asyncio.wait_for(
                loop.run_in_executor(_executor, _section_levels_sync, data, ext),
                timeout=_SECTION_TIMEOUT_SEC,
            )
        except Exception as exc:  # noqa: BLE001 — 单图失败不阻断整链
            logger.warning("[ModelBuilder] 剖面 z 恢复跳过 %s: %s", drawing.get("id"), exc)
            continue
        if levels is not None and getattr(levels, "marks", ()):
            levels_by_drawing[str(drawing["id"])] = levels

    vlm_elevations_by_drawing: dict[str, Any] = {}
    if _vlm_section_z_enabled():
        vlm_elevations_by_drawing = await _vlm_section_z_fallback(
            section_drawings, levels_by_drawing
        )

    return section_z_recovery.recover_section_z(
        drawings,
        levels_by_drawing,
        normalization,
        vlm_elevations_by_drawing=vlm_elevations_by_drawing,
    )


def _needs_vlm_elevation(drawing: dict, levels_by_drawing: dict[str, Any]) -> bool:
    """仅结构专业 + 主源（矢量/OCR）标高数不足才需要 VLM 补充（绝不逢图必调）。"""
    if str(drawing.get("discipline") or "").strip().lower() != "structure":
        return False
    levels = levels_by_drawing.get(str(drawing.get("id") or ""))
    marks = getattr(levels, "marks", ()) if levels is not None else ()
    return len(marks) < _VLM_SECTION_Z_MIN_MARKS


async def _vlm_section_z_fallback(
    section_drawings: list[dict],
    levels_by_drawing: dict[str, Any],
) -> dict[str, tuple]:
    """结构剖面 + 矢量/OCR 标高不足时，VLM 读标高补第二源（灰度开关，慢，~25s/图）。

    仅 PDF、仅 discipline=='structure'、仅主源 marks 数不足的剖面才会调用；单图
    渲染/调用失败或超时一律跳过，绝不阻断整链（与 `_section_levels_sync` 同一
    降级纪律）。`read_drawing_vlm` 本身已优雅降级（端点未配置/网络失败一律返回
    ``backend="none"``，不抛异常），这里额外做超时与置信度双重把关。

    返回 {drawing_id: (ElevationCandidate, ...)}，已按 VLM 自报置信度过滤
    （< `_VLM_SECTION_Z_MIN_CONFIDENCE` 的候选丢弃——宁缺毋滥，绝不虚高）。
    """
    targets = [
        drawing for drawing in section_drawings
        if _needs_vlm_elevation(drawing, levels_by_drawing)
    ][:_VLM_SECTION_Z_MAX_DRAWINGS]
    candidates: dict[str, tuple] = {}
    if not targets:
        return candidates

    from core.model3d.vlm_read import read_drawing_vlm

    loop = asyncio.get_event_loop()
    for drawing in targets:
        drawing_id = str(drawing.get("id") or "")
        file_key = str(drawing.get("file_key") or "")
        if not file_key or not file_key.lower().endswith(".pdf"):
            continue  # DXF/DWG 渲染成本高、场景稀少，最小改动不纳入本轮兜底
        try:
            data = await loop.run_in_executor(_executor, get_file_bytes, file_key)
            image_bytes = await asyncio.wait_for(
                loop.run_in_executor(_executor, _pdf_first_page_png_sync, data),
                timeout=_SECTION_TIMEOUT_SEC,
            )
            if not image_bytes:
                continue
            result = await read_drawing_vlm(
                image_bytes, timeout=_VLM_SECTION_Z_TIMEOUT_SEC
            )
        except Exception as exc:  # noqa: BLE001 — 单图 VLM 兜底失败跳过，不阻断整链
            logger.warning("[ModelBuilder] 剖面标高 VLM 兜底跳过 %s: %s", drawing_id, exc)
            continue
        filtered = result.filter_confidence(_VLM_SECTION_Z_MIN_CONFIDENCE)
        if filtered.elevations:
            candidates[drawing_id] = filtered.elevations
            logger.info(
                "[ModelBuilder] 剖面标高 VLM 兜底命中: drawing=%s %d 个候选（backend=%s）",
                drawing_id, len(filtered.elevations), result.backend,
            )
    return candidates


def _pdf_first_page_png_sync(data: bytes) -> bytes:
    """PDF 首页 → PNG 字节（供 VLM 读图）。失败返回空字节，调用方据此跳过。"""
    try:
        import fitz

        doc = fitz.open(stream=data, filetype="pdf")
        try:
            if len(doc) == 0:
                return b""
            pix = doc[0].get_pixmap(dpi=_VLM_SECTION_Z_RENDER_DPI)
            return pix.tobytes("png")
        finally:
            doc.close()
    except Exception as exc:  # noqa: BLE001 — 渲染失败降级空字节，VLM 兜底跳过
        logger.warning("[ModelBuilder] 剖面 PDF 渲染失败（VLM 兜底）: %s", exc)
        return b""


def _section_texts_sync(data: bytes, ext: str) -> list[str]:
    """同步：字节 → 几何 → 文本内容列表（供截面标注解析）。失败返回 []。"""
    try:
        from core.model3d import geometry_extractor

        geom = (
            geometry_extractor.extract_pdf_geometry(data)
            if ext == "pdf"
            else geometry_extractor.extract_dxf_geometry(data)
        )
        return [t[2] for t in geom.texts if len(t) >= 3 and isinstance(t[2], str)]
    except Exception as exc:  # noqa: BLE001 — 截面文本抽取失败跳过
        logger.warning("[ModelBuilder] 截面文本抽取跳过: %s", exc)
        return []


async def _recover_component_sections(drawings: list[dict]) -> dict:
    """从剖面/详图标注构建构件截面表（B-07）。无剖面/详图时回落全默认。"""
    targets = [
        drawing for drawing in drawings
        if classify_view_type(drawing).view_type in ("section", "detail")
    ]
    texts: list[str] = []
    loop = asyncio.get_event_loop()
    for drawing in targets:
        # 图纸自带文本字段（标题/OCR）先入料，零额外 IO
        for field_key in ("title", "ocr_text", "drawing_no"):
            value = drawing.get(field_key)
            if isinstance(value, str) and value:
                texts.append(value)
        file_key = drawing.get("file_key") or ""
        ext = file_key.rsplit(".", 1)[-1].lower() if "." in file_key else ""
        if not file_key or ext not in ("pdf", "dxf", "dwg"):
            continue
        try:
            data = await loop.run_in_executor(_executor, get_file_bytes, file_key)
            geom_texts = await asyncio.wait_for(
                loop.run_in_executor(_executor, _section_texts_sync, data, ext),
                timeout=_SECTION_TIMEOUT_SEC,
            )
        except Exception as exc:  # noqa: BLE001 — 单图失败不阻断整链
            logger.warning("[ModelBuilder] 截面表跳过 %s: %s", drawing.get("id"), exc)
            continue
        texts.extend(geom_texts)
    return model_component_sections.build_component_sections(texts)


def _build_model_scopes(
    buildings: list[dict],
    floors: list[dict],
    ifc_models: list[dict],
    matched_units: set[str] | None = None,
) -> list[ModelScopeEvidence]:
    matched_units = matched_units or set()
    if not buildings:
        return [_scope_evidence_for("scene", "总体", floors, ifc_models, matched_units)]
    return [
        _scope_evidence_for(
            str(building.get("key") or "scene"),
            str(building.get("label") or building.get("key") or "总体"),
            building.get("floors") or floors,
            ifc_models,
            matched_units,
        )
        for building in buildings
    ]


def _scope_evidence_for(
    scope_key: str,
    scope_label: str,
    floors: list[dict],
    ifc_models: list[dict],
    matched_units: set[str] | None = None,
) -> ModelScopeEvidence:
    relevant_floors = [floor for floor in floors if floor.get("key") != "UNZONED"] or list(floors)
    drawings = [drawing for floor in relevant_floors for drawing in floor.get("drawings") or []]
    drawing_ids = {str(drawing.get("drawing_id") or "") for drawing in drawings}
    ifc_scope_models = [
        model for model in ifc_models
        if str(model.get("drawing_id") or "") in drawing_ids
    ]
    explicit_evidence = _collect_scope_lod_evidence(relevant_floors, drawings, ifc_scope_models)
    cross_view = explicit_evidence["cross_view_match"] or _scope_cross_view_matched(
        relevant_floors, matched_units or set()
    )

    return ModelScopeEvidence(
        scope_key=scope_key,
        scope_label=scope_label,
        has_plan_boundary=bool(drawings),
        has_story_order=all(floor.get("order") is not None for floor in relevant_floors),
        has_scale=explicit_evidence["scale"],
        has_coordinates=bool(drawings),
        has_registered_grid=explicit_evidence["registered_grid"],
        has_dimensions=explicit_evidence["dimensions"],
        has_cross_view_match=cross_view,
        has_stable_component_boundaries=explicit_evidence["stable_component_boundaries"],
        geometry_consistent=explicit_evidence["geometry_consistent"],
    )


def _scope_cross_view_matched(floors: list[dict], matched_units: set[str]) -> bool:
    """本 scope 是否达成跨视图对齐：其楼层所属单体命中剖面 z 恢复的匹配单体。"""
    if not matched_units:
        return False
    scope_units = {
        unit for floor in floors for unit in (floor.get("building_units") or [])
    }
    if not scope_units:
        return True  # 无单体细分（单体项目）→ 有匹配即算命中
    return bool(scope_units & matched_units)


def _collect_scope_lod_evidence(
    floors: list[dict],
    drawings: list[dict],
    ifc_models: list[dict],
) -> dict[str, bool]:
    evidence = {
        "scale": False,
        "registered_grid": False,
        "dimensions": False,
        "cross_view_match": False,
        "stable_component_boundaries": False,
        "geometry_consistent": False,
    }
    for item in [*floors, *drawings]:
        raw = item.get("_lod_evidence") or {}
        if isinstance(raw, dict):
            for key in evidence:
                evidence[key] = evidence[key] or bool(raw.get(key))

    for floor in floors:
        for items in (floor.get("elements") or {}).values():
            for element in items or []:
                raw = element.get("lod_evidence") if isinstance(element, dict) else None
                if isinstance(raw, dict):
                    for key in evidence:
                        evidence[key] = evidence[key] or bool(raw.get(key))

    if ifc_models:
        evidence["dimensions"] = True
        evidence["stable_component_boundaries"] = True
        evidence["geometry_consistent"] = True

    # B-15：构件拓扑闭合驱动几何一致性证据（与既有来源 OR，无拓扑时无副作用）。
    topology_evidence = _topology_lod_evidence(floors)
    evidence["stable_component_boundaries"] = (
        evidence["stable_component_boundaries"] or topology_evidence["stable_component_boundaries"]
    )
    evidence["geometry_consistent"] = (
        evidence["geometry_consistent"] or topology_evidence["geometry_consistent"]
    )
    return evidence


def _topology_lod_evidence(floors: list[dict]) -> dict[str, bool]:
    """跨楼层汇构件 → 拓扑图 → LOD 证据（梁柱/板梁闭合）。"""
    walls, columns, beams, slabs = [], [], [], []
    for floor in floors:
        elements = floor.get("elements") or {}
        walls.extend(elements.get("walls") or [])
        columns.extend(elements.get("columns") or [])
        beams.extend(elements.get("beams") or [])
        slabs.extend(elements.get("slabs") or [])
    graph = model_topology.build_topology_graph(walls, columns, beams, slabs, [])
    return graph.lod_evidence()


def _strip_private_lod_fields(floors: list[dict], buildings: list[dict]) -> None:
    for floor in floors:
        floor.pop("_lod_registered_drawings", None)
        floor.pop("_lod_evidence", None)
        for drawing in floor.get("drawings") or []:
            drawing.pop("_lod_evidence", None)
    for building in buildings:
        for floor in building.get("floors") or []:
            floor.pop("_lod_registered_drawings", None)
            floor.pop("_lod_evidence", None)
            for drawing in floor.get("drawings") or []:
                drawing.pop("_lod_evidence", None)


async def build_scene(db, project_id: str, progress_cb=None) -> tuple[dict, dict]:
    """构建 scene（V1 契约全保留 + schema_version=2 构件层），返回 (scene, assets)。

    ``progress_cb``：可选 async 回调，接收 {stage, stage_label, current, done, total,
    updated_at}，供构建任务实时写库展示进度。
    """
    await _notify(progress_cb, _progress_payload("fetch", "读取项目图纸与审图数据", "", 0, 1))
    project, drawings, issues_by_drawing, cross = await _fetch_inputs(db, project_id)
    # A-13：VLM 语义融合（灰度 vlm_semantic_enabled；关闭时下列三步均为恒等无副作用）。
    vlm_by_drawing = await vlm_semantics.collect_scene_vlm(db, drawings)
    drawings = vlm_semantics.apply_vlm_discipline(drawings, vlm_by_drawing)
    annotation_overrides = await _load_annotation_overrides(db, project_id)
    normalization = model_story.normalize_story_table(drawings, annotation_overrides)
    # B-05：跨视图 z 恢复（仅剖面标高）——有剖面则以实测层高重归一化并点亮 gate；无剖面 no-op。
    section_z = await _recover_section_z(drawings, normalization)
    # Task 3：人工录入层高作为最高优先级 override（覆盖剖面/估算），消除均匀默认层高。
    manual_overrides = await model_story_manual.fetch_manual_overrides(db, project_id)
    combined_overrides = {**(section_z.z_overrides or {}), **manual_overrides}
    if combined_overrides:
        # 按累加层高补全每层真实底标高（锚定 ±0.000），使人工层高真正抬升上层楼层。
        combined_overrides = _accumulate_manual_elevations(normalization, combined_overrides)
        normalization = model_story.normalize_story_table(
            drawings, annotation_overrides, z_overrides=combined_overrides
        )
    semantic_payload = vlm_semantics.merge_vlm_into_semantic_payload(
        _semantic_scene_payload(drawings), vlm_by_drawing
    )
    assets, ifc_models, ifc_skipped = await _build_assets(project_id, drawings, progress_cb)
    floors, floor_of = _build_floors(drawings, issues_by_drawing, assets, normalization)
    yolo_total = await _attach_floor_elements(floors, drawings, floor_of, progress_cb)
    _clip_elements_to_envelope(floors)  # 裁掉离群构件(机电比例错误致管线冲到数千米)
    # B-07：剖面/详图截面回填构件（实测覆盖硬编码默认；无标注时全默认→无副作用）。
    component_sections = await _recover_component_sections(drawings)
    model_component_sections.apply_component_sections(floors, component_sections)
    await _notify(progress_cb, _progress_payload("assemble", "组装场景与统计", "", 0, 1))

    ids_by_no: dict[str, list[str]] = {}
    floor_by_no: dict[str, str] = {}
    for drawing in drawings:
        no = str(drawing.get("drawing_no") or "")
        ids_by_no.setdefault(no, []).append(str(drawing["id"]))
        floor_by_no.setdefault(no, floor_of[str(drawing["id"])])

    markers = _build_markers(issues_by_drawing, floor_of)
    _marker_building_keys(markers, drawings, normalization.drawing_assignments)

    cross_links = _build_cross_links(cross, ids_by_no, floor_by_no)
    cross_links.extend(
        vlm_semantics.vlm_cross_link_candidates(vlm_by_drawing, ids_by_no, floor_by_no)
    )

    stats = _build_stats(drawings, issues_by_drawing, floors, ifc_skipped)
    stats["elements_total"] = model_elements.totals(floors)
    stats["reconstruction"] = model_elements.reconstruction_mode(floors)
    stats["buildings"] = 0  # 占位，下方 buildings 组装后回填
    stats["unclassified_drawings"] = len(normalization.unclassified_drawings)
    stats["quality_issues"] = len(normalization.issues)
    if yolo_total:
        stats["yolo_equipment"] = yolo_total

    project_name = project.get("name") or ""
    buildings = model_elements.group_buildings(
        floors,
        drawings,
        project_name,
        normalized_assignments=normalization.drawing_assignments,
        building_units=normalization.building_units,
    )
    stats["buildings"] = len(buildings)
    model_scopes = _build_model_scopes(buildings, floors, ifc_models, section_z.matched_units)
    lod_capabilities = {
        scope.scope_key: evaluate_lod_capability(scope).as_dict()
        for scope in model_scopes
    }
    _strip_private_lod_fields(floors, buildings)

    scene = {
        "schema_version": 2,
        "project": {"id": str(project["id"]), "name": project_name},
        "buildings": buildings,
        "floors": floors,
        "semantic_tree": semantic_payload["semantic_tree"],
        "unassigned_drawings": semantic_payload["unassigned_drawings"],
        "semantic_version": semantic_payload["semantic_version"],
        "quality": _quality_payload(normalization, extra_issues=section_z.issues),
        "annotation_queue": normalization.unclassified_drawings,
        "building_units": {
            "detected": normalization.building_units,
            "manual": [
                unit for unit in normalization.building_units
                if unit.get("source") == "manual"
            ],
        },
        "markers": markers,
        "cross_links": cross_links,
        "ifc_models": ifc_models,
        "lod_capabilities": lod_capabilities,
        "lod_modes": aggregate_lod_modes(lod_capabilities),
        "stats": stats,
        "lod": {
            "default_mode": stats["reconstruction"],
            "supported_modes": ["texture", "elements", "mixed"],
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    model_ifc = await model_ifc_integration.maybe_build_programmatic_ifc(
        project_id, project_name, buildings, floors, stats["reconstruction"]
    )
    if model_ifc:
        scene["model_ifc"] = model_ifc
        scene["lod"]["supported_modes"] = ["ifc", "texture", "elements", "mixed"]

    return scene, assets
