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
from core.storage import get_file_bytes, upload_file
from services import model_elements
from services.floor_parser import floor_of_drawing, parse_floor

logger = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=2)

RENDER_DPI = 110
MAX_TEXTURE_PX = 1600
DXF_FIG_SIZE = (16, 12)     # 英寸
DXF_FIG_DPI = 100
MARKER_TITLE_MAX = 80
COORD_MIN, COORD_MAX = 0.1, 0.9
CLUSTER_STEP = 0.02
SEVERITY_KEYS = ("critical", "major", "minor", "info")
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
        asset = await loop.run_in_executor(
            _executor, _render_and_upload_sync, project_id, drawing_id, file_key, ext
        )
        return asset, None, False
    except Exception as exc:  # noqa: BLE001 — 单图失败必须降级
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
    for index, drawing in enumerate(drawings):
        await _notify(progress_cb, _progress_payload(
            "render", "读取图纸内容并渲染贴图",
            str(drawing.get("drawing_no") or ""), index, total,
        ))
        asset, gltf_key, skipped = await _build_one_asset(loop, project_id, drawing)
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


def _drawing_entry(drawing: dict, issues: list[dict], asset: dict) -> dict:
    """楼层内单张图纸条目（蓝图第 4 节 floors[].drawings[] 契约）。"""
    return {
        "drawing_id": str(drawing["id"]),
        "drawing_no": drawing.get("drawing_no") or "",
        "title": drawing.get("title") or "",
        "discipline": drawing.get("discipline") or "",
        "status": drawing.get("status") or "",
        "current_stage": drawing.get("current_stage") or "",
        "image_key": asset.get("image_key", ""),
        "issue_count": len(issues),
        "critical_count": sum(1 for i in issues if i.get("severity") == "critical"),
    }


def _build_floors(
    drawings: list[dict], issues_by_drawing: dict[str, list[dict]], assets: dict
) -> tuple[list[dict], dict[str, str]]:
    """楼层堆叠：按 order 排序的 floors 列表 + drawing_id → floor_key 映射。"""
    floors: dict[str, dict] = {}
    floor_of: dict[str, str] = {}
    for drawing in drawings:
        drawing_id = str(drawing["id"])
        issues = issues_by_drawing.get(drawing_id, [])
        key, label, order = floor_of_drawing(drawing, _issue_levels(issues))
        floor = floors.setdefault(
            key,
            {"key": key, "label": label, "elevation": order, "order": order, "drawings": []},
        )
        floor["drawings"].append(
            _drawing_entry(drawing, issues, assets.get(drawing_id, _empty_asset("none")))
        )
        floor_of[drawing_id] = key
    return sorted(floors.values(), key=lambda f: f["order"]), floor_of


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
    """成果标记层：issues.location_json(levels) → floor_key + 稳定坐标。"""
    markers: list[dict] = []
    cluster_counters: dict[tuple[str, str], int] = {}
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
        floor_drawings = drawings_by_floor.get(floor["key"], [])
        try:
            elements, yolo_count = await model_elements.build_floor_elements(
                _executor, floor_drawings, get_file_bytes
            )
        except Exception as exc:  # noqa: BLE001 — 构件层失败回退贴图
            logger.warning("[ModelBuilder] 楼层构件识别失败 %s: %s", floor["key"], exc)
            elements, yolo_count = {k: [] for k in model_elements.EMPTY_ELEMENTS}, 0
        floor["elements"] = elements
        floor["element_stats"] = model_elements.element_stats(elements)
        yolo_total += yolo_count
    return yolo_total


def _marker_building_keys(markers: list[dict], drawings: list[dict]) -> None:
    """markers 补 building_key（按所属图纸的单体）。"""
    building_by_drawing = {
        str(d["id"]): model_elements.building_of(d)[0] for d in drawings
    }
    for marker in markers:
        drawing_id = str((marker.get("ref") or {}).get("drawing_id") or "")
        marker["building_key"] = building_by_drawing.get(drawing_id, "main")


async def build_scene(db, project_id: str, progress_cb=None) -> tuple[dict, dict]:
    """构建 scene（V1 契约全保留 + schema_version=2 构件层），返回 (scene, assets)。

    ``progress_cb``：可选 async 回调，接收 {stage, stage_label, current, done, total,
    updated_at}，供构建任务实时写库展示进度。
    """
    await _notify(progress_cb, _progress_payload("fetch", "读取项目图纸与审图数据", "", 0, 1))
    project, drawings, issues_by_drawing, cross = await _fetch_inputs(db, project_id)
    assets, ifc_models, ifc_skipped = await _build_assets(project_id, drawings, progress_cb)
    floors, floor_of = _build_floors(drawings, issues_by_drawing, assets)
    yolo_total = await _attach_floor_elements(floors, drawings, floor_of, progress_cb)
    await _notify(progress_cb, _progress_payload("assemble", "组装场景与统计", "", 0, 1))

    ids_by_no: dict[str, list[str]] = {}
    floor_by_no: dict[str, str] = {}
    for drawing in drawings:
        no = str(drawing.get("drawing_no") or "")
        ids_by_no.setdefault(no, []).append(str(drawing["id"]))
        floor_by_no.setdefault(no, floor_of[str(drawing["id"])])

    markers = _build_markers(issues_by_drawing, floor_of)
    _marker_building_keys(markers, drawings)

    stats = _build_stats(drawings, issues_by_drawing, floors, ifc_skipped)
    stats["elements_total"] = model_elements.totals(floors)
    stats["reconstruction"] = model_elements.reconstruction_mode(floors)
    stats["buildings"] = 0  # 占位，下方 buildings 组装后回填
    if yolo_total:
        stats["yolo_equipment"] = yolo_total

    project_name = project.get("name") or ""
    buildings = model_elements.group_buildings(floors, drawings, project_name)
    stats["buildings"] = len(buildings)

    scene = {
        "schema_version": 2,
        "project": {"id": str(project["id"]), "name": project_name},
        "buildings": buildings,
        "floors": floors,
        "markers": markers,
        "cross_links": _build_cross_links(cross, ids_by_no, floor_by_no),
        "ifc_models": ifc_models,
        "stats": stats,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    return scene, assets
