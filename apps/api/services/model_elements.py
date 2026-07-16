"""scene V2 构件层组装（Phase 7 蓝图第 4 节）：单体分组 + 构件识别接线 + YOLO 设备补充。

- core.model3d 延迟 import：ImportError → 楼层回退贴图（调用方据空 elements 判断）；
- 每楼层每类构件按「最适图纸」选择并限量识别，单图异常跳过；
- YOLO 检测框（归一化）按楼层包络映射为米坐标设备块。
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import replace
from functools import lru_cache
from typing import Any, Callable

from services.model_story import detect_building_unit

logger = logging.getLogger(__name__)

# 每楼层每类参与识别的图纸上限（控制构建时长）
_MAX_STRUCTURE_PLANS = 2
_MAX_BEAM_PLANS = 2
_MAX_MEP_PLANS = 3
_RECOGNIZE_TIMEOUT_SEC = 20

_STRUCTURE_TITLE_RE = re.compile(r"墙柱|结构平面|模板|基础|筏板|底板|承台|地下室|桩")
_BEAM_TITLE_RE = re.compile(r"梁")

# 单体识别：图名/标题正则 → building key
_BUILDING_PATTERNS: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"南区"), "south"),
    (re.compile(r"北区"), "north"),
    (re.compile(r"东区"), "east"),
    (re.compile(r"西区"), "west"),
)
_BUILDING_UNIT_RE = re.compile(r"([A-Z]\d?)栋|(\d+)#楼")

EMPTY_ELEMENTS: dict[str, list] = {
    "columns": [], "walls": [], "beams": [], "slabs": [], "pipes": [], "equipment": [],
}

# YOLO 设备缺省楼层包络（米，无识别构件可参照时）
_DEFAULT_FLOOR_EXTENT = (60.0, 40.0)
_YOLO_MIN_CONFIDENCE = 0.4


def building_of(drawing: dict, normalized_assignment: dict[str, Any] | None = None) -> tuple[str, str]:
    """图纸 → (building_key, label)；优先 normalized assignment，回退动态识别。"""
    normalized_assignment = normalized_assignment or {}
    unit_key = str(normalized_assignment.get("building_unit_key") or "").strip()
    display_name = str(normalized_assignment.get("building_unit_display_name") or "").strip()
    if unit_key:
        return unit_key, display_name

    detected = detect_building_unit(drawing)
    if detected.unit_key != "main":
        return detected.unit_key, detected.display_name

    text = f"{drawing.get('title') or ''} {drawing.get('drawing_no') or ''}"
    for pattern, key in _BUILDING_PATTERNS:
        match = pattern.search(text)
        if match:
            return key, match.group(0)
    unit = _BUILDING_UNIT_RE.search(text)
    if unit:
        label = unit.group(0)
        key = "building_" + (unit.group(1) or unit.group(2) or "x")
        return key, label
    return "main", detected.display_name


def pick_element_drawings(floor_drawings: list[dict]) -> dict[str, list[dict]]:
    """楼层图纸 → 各构件类的「最适图纸」清单（蓝图 4 节规则）。"""
    structure: list[dict] = []
    beams: list[dict] = []
    mep: list[dict] = []
    for drawing in floor_drawings:
        title = str(drawing.get("title") or "")
        discipline = str(drawing.get("discipline") or "")
        if discipline == "mep":
            mep.append(drawing)
        elif _BEAM_TITLE_RE.search(title):
            beams.append(drawing)
        elif _STRUCTURE_TITLE_RE.search(title) or discipline == "structure":
            structure.append(drawing)
    return {
        "structure": structure[:_MAX_STRUCTURE_PLANS],
        "beam": beams[:_MAX_BEAM_PLANS],
        "mep": mep[:_MAX_MEP_PLANS],
    }


def _recognize_sync(data: bytes, ext: str, discipline: str, drawing_id: str) -> dict | None:
    """线程池内执行：几何提取 + 构件识别 + spotting 融合回灌 → {elements, axes}；失败返回 None。"""
    from core.model3d import extract_dxf_geometry, extract_pdf_geometry, recognize

    if ext == "pdf":
        geom = extract_pdf_geometry(data)
    elif ext in ("dxf", "dwg"):
        geom = extract_dxf_geometry(data)
    else:
        return None
    if geom.primitive_count() == 0:
        return None
    result = recognize(geom, discipline, drawing_id)
    elements = _reinject_fusion(result.as_dict(), geom, drawing_id)
    return {"elements": elements, "axes": result.axes}


# ── D-09：符号 spotting 融合回灌（fusion 引擎补规则漏召回，强规则不被覆盖）──
#
# 坐标系落差处理：element_recognizer 输出为米坐标（经比例尺+轴网原点变换），
# spotting 候选为页面点坐标（PrimitiveDoc pt 空间，未变换）。两者无法共享同一
# 精确变换（origin 仅存于 element_recognizer 内部，不对外暴露，且该文件按边界
# 约定不可修改）。本模块改为把两侧 bbox 各自归一化到所属坐标系的 [0,1] 域
# （规则侧用本图已识别构件整体包络，模型侧用页面 page_w/page_h）再做 IoU 配对
# ——这是尽力而为的空间对齐近似，不是像素级精确匹配；类别仲裁与强规则保护
# （fusion_policy）不受此近似影响，因为仲裁只依赖 IoU 是否达到「同处」阈值。
_RULE_CONFIDENCE = 0.92  # 几何规则识别默认置信（≥ fusion_policy.rule_strong_confidence=0.85）
_KIND_TO_CATEGORY = {
    "columns": "column", "walls": "wall", "beams": "beam",
    "slabs": "slab", "pipes": "pipe", "equipment": "equipment",
}
_CATEGORY_TO_KIND = {category: kind for kind, category in _KIND_TO_CATEGORY.items()}


def _item_bbox_m(item: dict) -> tuple[float, float, float, float] | None:
    """构件条目（outline 或 path，米坐标）→ bbox；无坐标点返回 None。"""
    points = item.get("outline") or item.get("path") or []
    if not points:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


def _elements_bbox_m(elements: dict[str, list]) -> tuple[float, float, float, float] | None:
    """本图已识别构件的整体包络（米坐标）；无构件时返回 None（融合回灌无坐标系可用）。"""
    xs: list[float] = []
    ys: list[float] = []
    for kind in EMPTY_ELEMENTS:
        for item in elements.get(kind) or []:
            for point in (item.get("outline") or item.get("path") or []):
                xs.append(point[0])
                ys.append(point[1])
    if not xs or not ys:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def _normalize_bbox(
    bbox: tuple[float, float, float, float], envelope: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    """bbox 归一化到 [0,1]（以 envelope 为坐标系）——跨坐标系（米 vs pt）IoU 配对的公共底座。"""
    ex0, ey0, ex1, ey1 = envelope
    w, h = max(ex1 - ex0, 1e-6), max(ey1 - ey0, 1e-6)
    x0, y0, x1, y1 = bbox
    return ((x0 - ex0) / w, (y0 - ey0) / h, (x1 - ex0) / w, (y1 - ey0) / h)


def _denormalize_bbox(
    bbox_norm: tuple[float, float, float, float], envelope: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    """``_normalize_bbox`` 的逆运算：[0,1] 归一 bbox → envelope 坐标系下的真实 bbox。"""
    ex0, ey0, ex1, ey1 = envelope
    w, h = max(ex1 - ex0, 1e-6), max(ey1 - ey0, 1e-6)
    nx0, ny0, nx1, ny1 = bbox_norm
    return (ex0 + nx0 * w, ey0 + ny0 * h, ex0 + nx1 * w, ey0 + ny1 * h)


def _rule_candidates_from_elements(
    elements: dict[str, list], envelope: tuple[float, float, float, float],
) -> tuple[tuple, dict[int, tuple[str, int]]]:
    """规则构件 → SymbolCandidate（bbox 归一化 + evidence 携带回填索引）。

    confidence 固定为 ``_RULE_CONFIDENCE``（几何确定性识别，达到 fusion_policy 的
    强命中门槛）——契合「规则强命中不被模型覆盖」，融合时模型只补规则漏召回。
    ``_rule_index`` 写入 evidence：fusion 引擎的 ``replace()`` 链路（consensus /
    rule_protected / weak_conflict）与「未配对规则原样保留」均会透传该字段的原始
    evidence 字典，使融合后每个规则来源候选都能精确映射回原构件条目。
    """
    from core.model3d.spotting.types import SymbolCandidate

    candidates: list[SymbolCandidate] = []
    index_map: dict[int, tuple[str, int]] = {}
    idx = 0
    for kind, category in _KIND_TO_CATEGORY.items():
        for pos, item in enumerate(elements.get(kind) or []):
            bbox_m = _item_bbox_m(item)
            if bbox_m is None:
                continue
            candidates.append(
                SymbolCandidate(
                    category=category,
                    confidence=_RULE_CONFIDENCE,
                    bbox=_normalize_bbox(bbox_m, envelope),
                    source="rule",
                    evidence={"_rule_index": idx},
                )
            )
            index_map[idx] = (kind, pos)
            idx += 1
    return tuple(candidates), index_map


@lru_cache(maxsize=1)
def _spotting_service():
    """惰性单例 SpottingService：避免每张图纸重复探测后端可用性。"""
    from core.model3d.spotting.service import SpottingService

    return SpottingService()


def _spot_model_candidates(geom, drawing_id: str) -> tuple:
    """真实 spotting 后端候选（bbox 归一化到页面 [0,1]）。

    仅 mock 兜底可用（无真实权重/后端）时视为「无模型信号」返回空元组——保证
    「无 spotting 后端/无权重」场景下融合回灌为空操作，纯规则路径行为不变。
    任何异常同样降级为空（spotting 为可插拔增强位，绝不影响主构件识别）。
    """
    try:
        service = _spotting_service()
        backend = service.select_backend()
        if backend.name == "mock":
            return ()
        from core.model3d.preprocess import preprocess_geometry

        pre = preprocess_geometry(geom)
        if not pre.doc.primitives:
            return ()
        page_w = pre.doc.page_w or geom.page_w
        page_h = pre.doc.page_h or geom.page_h
        if not page_w or not page_h:
            return ()
        spot_result = service.spot_doc(pre.doc, drawing_id=drawing_id)
        return tuple(
            replace(cand, bbox=_normalize_bbox(cand.bbox, (0.0, 0.0, page_w, page_h)))
            for cand in spot_result.candidates
        )
    except Exception as exc:  # noqa: BLE001 — spotting 为可插拔增强位，异常不影响纯规则路径
        logger.debug("[ModelElements] spotting 候选获取跳过 %s: %s", drawing_id, exc)
        return ()


def _tag_rule_source(elements: dict[str, list]) -> dict[str, list]:
    """无模型候选时的兜底：仅补 source/confidence 标注，几何/数量完全不变（回退路径）。"""
    return {
        kind: [{**item, "source": "rule", "confidence": _RULE_CONFIDENCE} for item in items]
        for kind, items in elements.items()
    }


def _element_from_model_candidate(cand, envelope, drawing_id: str) -> dict | None:
    """模型补召回候选 → 构件条目（bbox 反归一化为近似矩形，source="model" 标注真实性）。

    door/window/axis 等尚未纳入 scene 构件类别（v1 沿用 6 类），此类候选跳过，
    不臆造缺少几何契约的构件类型。
    """
    kind = _CATEGORY_TO_KIND.get(cand.category)
    if kind is None:
        return None
    x0, y0, x1, y1 = (round(v, 3) for v in _denormalize_bbox(cand.bbox, envelope))
    base = {"source": "model", "confidence": round(cand.confidence, 3), "src": drawing_id}
    if kind in ("walls", "beams", "pipes"):
        item = {**base, "path": [[x0, y0], [x1, y1]]}
        if kind == "pipes":
            item.update(dia=0.1, system="其他")
        else:
            item["width"] = round(max(x1 - x0, y1 - y0, 0.1), 3)
            if kind == "beams":
                item["depth"] = 0.6
        return item
    outline = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
    item = {**base, "outline": outline}
    if kind == "slabs":
        item["thickness"] = 0.12
    elif kind == "equipment":
        item.update(height=1.5, label=f"model:{cand.category}")
    return item


def _apply_fusion_result(
    elements: dict[str, list], fusion_result, index_map: dict[int, tuple[str, int]],
    envelope: tuple[float, float, float, float], drawing_id: str,
) -> dict[str, list]:
    """融合结果回灌：规则位按 ``_rule_index`` 原地更新 source/confidence（几何不变）；
    模型补召回候选落盘为新构件条目（近似几何，source="model" 如实标注）。
    """
    updated: dict[str, list] = {kind: list(items) for kind, items in elements.items()}
    for cand in fusion_result.candidates:
        rule_idx = cand.evidence.get("_rule_index") if isinstance(cand.evidence, dict) else None
        if rule_idx is not None and rule_idx in index_map:
            kind, pos = index_map[rule_idx]
            updated[kind][pos] = {
                **updated[kind][pos],
                "source": cand.source,
                "confidence": round(cand.confidence, 3),
            }
        elif cand.source == "model":
            new_item = _element_from_model_candidate(cand, envelope, drawing_id)
            if new_item is not None:
                updated[_CATEGORY_TO_KIND[cand.category]].append(new_item)
    return updated


def _reinject_fusion(elements: dict[str, list], geom, drawing_id: str) -> dict[str, list]:
    """D-09：符号 spotting 融合回灌构件识别（规则强命中不覆盖，模型只补漏召回）。

    无 fusion/spotting 依赖、无真实模型信号（仅 mock 兜底）、无构件坐标系可用、
    或识别/融合过程任何异常，均优雅降级为「原样构件 + source=rule 标注」——
    纯规则路径完整保留为回退，构件几何与数量不受影响。
    """
    model_candidates = _spot_model_candidates(geom, drawing_id)
    if not model_candidates:
        return _tag_rule_source(elements)

    envelope = _elements_bbox_m(elements)
    if envelope is None:
        return _tag_rule_source(elements)

    rule_candidates, index_map = _rule_candidates_from_elements(elements, envelope)
    if not rule_candidates:
        return _tag_rule_source(elements)

    try:
        from core.model3d.fusion import fuse

        result = fuse(rule_candidates, model_candidates)
    except Exception as exc:  # noqa: BLE001 — 融合失败降级为纯规则 + 标注
        logger.warning("[ModelElements] 融合回灌失败 %s: %s", drawing_id, exc)
        return _tag_rule_source(elements)

    return _apply_fusion_result(elements, result, index_map, envelope, drawing_id)


# ── 跨图轴号配准（统一源坐标点）──────────────────────────────

def _labeled_axis_map(axes: dict, direction: str) -> dict[str, float]:
    return {
        str(label): float(pos)
        for label, pos in (axes or {}).get(direction, [])
        if label
    }


def _axis_offset(ref: dict[str, float], cur: dict[str, float]) -> float:
    """共有轴号位置差的中位数（cur 平移 delta 后与 ref 对齐）；无共有轴号 → 0。"""
    deltas = sorted(ref[label] - cur[label] for label in ref.keys() & cur.keys())
    return deltas[len(deltas) // 2] if deltas else 0.0


def register_offset(ref_axes: dict, axes: dict) -> tuple[float, float]:
    """以参考图轴网为基准，计算当前图构件坐标的 (dx, dy) 平移量。

    对齐依据：两图共有轴号（如同为「5」轴）的位置差中位数——即所有图纸
    以「最小轴号交点」为统一源坐标点后残余的系统偏移。
    """
    dx = _axis_offset(_labeled_axis_map(ref_axes, "x"), _labeled_axis_map(axes, "x"))
    dy = _axis_offset(_labeled_axis_map(ref_axes, "y"), _labeled_axis_map(axes, "y"))
    return dx, dy


def _shift_elements(elements: dict, dx: float, dy: float) -> dict:
    """整体平移构件坐标（配准到统一源坐标点）。"""
    if abs(dx) < 1e-6 and abs(dy) < 1e-6:
        return elements
    shifted: dict[str, list] = {}
    for kind, items in elements.items():
        out = []
        for item in items:
            moved = dict(item)
            for key in ("outline", "path"):
                if key in moved:
                    moved[key] = [
                        [round(p[0] + dx, 3), round(p[1] + dy, 3)] for p in moved[key]
                    ]
            out.append(moved)
        shifted[kind] = out
    return shifted


def _has_labeled_axes(axes: dict) -> bool:
    return bool(
        _labeled_axis_map(axes, "x") or _labeled_axis_map(axes, "y")
    )


async def _recognize_one(
    loop: asyncio.AbstractEventLoop, executor, drawing: dict,
    discipline: str, file_getter: Callable[[str], bytes],
) -> dict | None:
    """单图识别（下载 + 提取 + 识别，20s 超时；任何失败返回 None）。"""
    file_key = drawing.get("file_key") or ""
    ext = file_key.rsplit(".", 1)[-1].lower() if "." in file_key else ""
    if not file_key or ext not in ("pdf", "dxf", "dwg"):
        return None
    try:
        data = await loop.run_in_executor(executor, file_getter, file_key)
        return await asyncio.wait_for(
            loop.run_in_executor(
                executor, _recognize_sync, data, ext, discipline, str(drawing["id"])
            ),
            timeout=_RECOGNIZE_TIMEOUT_SEC,
        )
    except Exception as exc:  # noqa: BLE001 — 单图识别失败跳过
        logger.warning("[ModelElements] 构件识别跳过 %s: %s", drawing.get("id"), exc)
        return None


def _merge_elements(target: dict[str, list], parts: dict | None, kinds: tuple[str, ...]) -> None:
    if not parts:
        return
    for kind in kinds:
        target[kind].extend(parts.get(kind) or [])


async def build_floor_elements(
    executor, floor_drawings: list[dict], file_getter: Callable[[str], bytes],
) -> tuple[dict[str, list], int, dict]:
    """构建单楼层 elements（识别 → 轴号配准 → 合并 + YOLO 补充）。

    返回 (elements, yolo_count, floor_meta)；floor_meta 含
    ``{"elevations": [标高候选], "registered": 配准图数}``。
    core.model3d 缺失时返回 (全空, 0, {})。
    """
    empty = {key: [] for key in EMPTY_ELEMENTS}
    try:
        import core.model3d  # noqa: F401 — 探测模块可用性
    except ImportError:
        return empty, 0, {}

    loop = asyncio.get_event_loop()
    picked = pick_element_drawings(floor_drawings)
    tasks: list[tuple[dict, str, tuple[str, ...]]] = [
        *[(d, "structure", ("columns", "walls", "slabs")) for d in picked["structure"]],
        *[(d, "structure", ("beams",)) for d in picked["beam"]],
        *[(d, "mep", ("pipes", "equipment")) for d in picked["mep"]],
    ]

    elements: dict[str, list] = empty
    elevations: list[float] = []
    ref_axes: dict | None = None
    ref_axes_drawing_id: str | None = None
    registered = 0
    for drawing, discipline, kinds in tasks:
        result = await _recognize_one(loop, executor, drawing, discipline, file_getter)
        if not result:
            continue
        axes = result.get("axes") or {}
        elevations.extend(axes.get("elevations") or [])
        part = result["elements"]
        # 轴号配准：以本层首张带轴号的图为参考系，其余图按共有轴号平移对齐
        if _has_labeled_axes(axes):
            if ref_axes is None:
                ref_axes = axes
                ref_axes_drawing_id = str(drawing.get("id") or "")
            else:
                dx, dy = register_offset(ref_axes, axes)
                part = _shift_elements(part, dx, dy)
                registered += 1
        _merge_elements(elements, part, kinds)

    yolo_count = await _yolo_supplement(loop, executor, picked["mep"], elements, file_getter)
    meta = {
        "elevations": sorted(set(elevations)),
        "registered": registered,
        # E2 轴网层：配准参考轴网以 scene 格式带出（此前算完配准即弃）
        "axes": _axes_scene_payload(ref_axes, ref_axes_drawing_id),
    }
    return elements, yolo_count, meta


def _axes_scene_payload(axes: dict | None, source_drawing_id: str | None) -> dict | None:
    """配准参考轴网 → scene floor.axes 载荷；无带标签轴网返回 None。

    输出：{"x": [{"label","coord"}...], "y": [...], "source_drawing_id"}，
    坐标为米（与构件同坐标系），仅收带标签轴线（未识别标签的轴线不进 scene）。
    """
    if not axes:
        return None
    def _entries(direction: str) -> list[dict]:
        return [
            {"label": str(label), "coord": round(float(pos), 3)}
            for label, pos in (axes.get(direction) or [])
            if str(label or "").strip()
        ]
    x_entries = _entries("x")
    y_entries = _entries("y")
    if not x_entries and not y_entries:
        return None
    return {"x": x_entries, "y": y_entries, "source_drawing_id": source_drawing_id or ""}


async def _yolo_supplement(
    loop, executor, mep_drawings: list[dict],
    elements: dict[str, list], file_getter: Callable[[str], bytes],
) -> int:
    """对该层首张机电图跑 YOLO 图元检测，检出设备并入 elements。"""
    if not mep_drawings:
        return 0
    drawing = mep_drawings[0]
    file_key = drawing.get("file_key") or ""
    ext = file_key.rsplit(".", 1)[-1].lower() if "." in file_key else ""
    if not file_key or ext not in ("pdf", "png", "jpg", "jpeg", "tif", "tiff"):
        return 0
    try:
        data = await loop.run_in_executor(executor, file_getter, file_key)
        detected = await loop.run_in_executor(
            executor, yolo_equipment, data, ext, elements, str(drawing["id"])
        )
    except Exception as exc:  # noqa: BLE001 — YOLO 失败不影响构件层
        logger.debug("[ModelElements] YOLO 补充跳过: %s", exc)
        return 0
    elements["equipment"].extend(detected)
    return len(detected)


# ── YOLO 设备补充 ────────────────────────────────────────────

def _floor_extent(elements: dict[str, list]) -> tuple[float, float]:
    """楼层包络（米）：由板/柱坐标推算，无参照用缺省。"""
    xs: list[float] = []
    ys: list[float] = []
    for slab in elements.get("slabs") or []:
        for x, y in slab.get("outline") or []:
            xs.append(x); ys.append(y)
    for column in elements.get("columns") or []:
        for x, y in column.get("outline") or []:
            xs.append(x); ys.append(y)
    if xs and ys and max(xs) > min(xs) and max(ys) > min(ys):
        return max(xs) - min(xs), max(ys) - min(ys)
    return _DEFAULT_FLOOR_EXTENT


def yolo_equipment(
    file_bytes: bytes, file_ext: str, elements: dict[str, list], drawing_id: str,
) -> list[dict]:
    """YOLO 检测框 → 设备块（label='YOLO:<cls>'）；ultralytics/权重缺失静默返回空。"""
    try:
        from core.ai_review.yolo_detector import detect_drawing_elements

        detections, _issues = detect_drawing_elements(file_bytes, file_ext)
    except Exception as exc:  # noqa: BLE001 — YOLO 为可插拔增强位
        logger.debug("[ModelElements] YOLO 跳过: %s", exc)
        return []

    width_m, height_m = _floor_extent(elements)
    equipment: list[dict] = []
    for det in detections:
        if det.confidence < _YOLO_MIN_CONFIDENCE:
            continue
        x1, y1, x2, y2 = det.box
        cx, cy = (x1 + x2) / 2 * width_m, (y1 + y2) / 2 * height_m
        half = 0.5
        equipment.append({
            "outline": [
                [round(cx - half, 3), round(cy - half, 3)],
                [round(cx + half, 3), round(cy - half, 3)],
                [round(cx + half, 3), round(cy + half, 3)],
                [round(cx - half, 3), round(cy + half, 3)],
            ],
            "height": 1.5,
            "label": f"YOLO:{det.label}",
            "src": drawing_id,
        })
    return equipment


def element_stats(elements: dict[str, list]) -> dict[str, int]:
    return {key: len(elements.get(key) or []) for key in EMPTY_ELEMENTS}


def reconstruction_mode(floors: list[dict]) -> str:
    """stats.reconstruction：elements | texture | mixed。"""
    with_elements = sum(
        1 for floor in floors
        if any((floor.get("elements") or {}).get(k) for k in EMPTY_ELEMENTS)
    )
    if with_elements == 0:
        return "texture"
    if with_elements == len(floors):
        return "elements"
    return "mixed"


def totals(floors: list[dict]) -> dict[str, int]:
    """全场景构件总量汇总。"""
    result: dict[str, int] = {key: 0 for key in EMPTY_ELEMENTS}
    for floor in floors:
        for key, count in (floor.get("element_stats") or {}).items():
            if key in result:
                result[key] += int(count)
    return result


def _split_elements_by_srcs(elements: dict, src_ids: set[str]) -> dict[str, list]:
    """按来源图纸集切分楼层构件（构件均携带 src=drawing_id）。

    src 不在任何单体图纸集内的构件（理论不存在）保留在其所属楼层的每个分组中
    的兜底策略改为：无 src 归入该分组，避免构件凭空丢失。
    """
    result: dict[str, list] = {}
    for kind in EMPTY_ELEMENTS:
        items = elements.get(kind) or []
        result[kind] = [
            item for item in items
            if not item.get("src") or str(item.get("src")) in src_ids
        ]
    return result


def group_buildings(
    floors: list[dict],
    drawings: list[dict],
    project_name: str,
    normalized_assignments: dict[str, dict[str, Any]] | None = None,
    building_units: list[dict[str, Any]] | None = None,
) -> list[dict]:
    """按单体分组楼层（同楼层图纸可能分属多单体 → 楼层按单体拆分）。

    输入 floors 为拍平楼层（V1 结构 + elements）；输出蓝图 buildings 数组。
    楼层构件按 src 来源图纸切分到所属单体（不重复归组）。
    """
    normalized_assignments = normalized_assignments or {}
    building_unit_map = {
        str(item.get("unit_key")): dict(item) for item in (building_units or []) if item.get("unit_key")
    }
    building_of_drawing = {
        str(d["id"]): building_of(d, normalized_assignments.get(str(d["id"])))
        for d in drawings
    }
    buildings: dict[str, dict] = {}
    for floor in floors:
        groups: dict[str, list[dict]] = {}
        for entry in floor.get("drawings") or []:
            key, _label = building_of_drawing.get(entry["drawing_id"], ("main", ""))
            groups.setdefault(key, []).append(entry)
        for key, entries in groups.items():
            label = (
                str(building_unit_map.get(key, {}).get("display_name") or "")
                or next((lb for k, lb in building_of_drawing.values() if k == key and lb), "")
            )
            building = buildings.setdefault(
                key,
                {
                    "key": key,
                    "label": label or (project_name if key == "main" else key),
                    "origin": [0, 0],
                    "floors": [],
                },
            )
            src_ids = {str(entry["drawing_id"]) for entry in entries}
            elements = _split_elements_by_srcs(
                floor.get("elements") or {}, src_ids
            )
            building["floors"].append({
                **{k: floor[k] for k in ("key", "label", "elevation", "order")},
                "elevation_m": floor.get("elevation_m"),
                "drawings": entries,
                "elements": elements,
                "element_stats": element_stats(elements),
            })
    for building in buildings.values():
        building["floors"].sort(key=lambda f: f["order"])
    return sorted(buildings.values(), key=lambda b: b["key"])
