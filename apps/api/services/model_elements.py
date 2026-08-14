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

from services.drawing_view_classifier import classify_view_type
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


def _placement_suspect(placement: Any) -> bool:
    """摆放是否存疑（dict 与对象两种形态都要吃得下）。"""
    if isinstance(placement, dict):
        return bool(placement.get("suspect"))
    return bool(getattr(placement, "suspect", False))


def _transform_rank(drawing: dict, transforms: dict | None,
                    placements: dict | None = None) -> int:
    """图纸的定位可靠度排序键（越小越优先）。

    **-1 = 有世界摆放**（锚点求解，绝对坐标）；0 = 有标准比例变换（§6.0.4）；
    1 = 有变换但比例非标准；2 = 无变换。

    **为什么世界摆放排在最前**：它是**绝对**位置（残差毫米级），
    而标准比例变换只保证图内比例对、不保证摆在哪。
    实测交点传播算出 12 张图的世界坐标后 `placed_drawings` 仍是 0 ——
    因为选图不看 placement，那 12 张一张也没被选中出构件，
    **算出来的世界坐标进不了模型**。

    残差过大（`suspect`）的摆放不算数：宁可用相对配准，不用错的绝对坐标。
    """
    did = str(drawing.get("id") or "")
    placement = (placements or {}).get(did)
    if placement is not None and not _placement_suspect(placement):
        return -1
    if not transforms:
        return 2
    transform = transforms.get(str(drawing.get("id") or ""))
    if transform is None:
        return 2
    try:
        from services.drawing_transform import is_standard_scale

        return 0 if is_standard_scale(float(transform.scale_m_pt)) else 1
    except Exception:  # noqa: BLE001 — 判不了就当非标准，不阻断
        return 1


def _dominant_unit(drawings: list[dict]) -> str | None:
    """本层图纸最多的那个单体 —— 最可能是本层主体。"""
    counts: dict[str, int] = {}
    for drawing in drawings:
        for pattern, key in _BUILDING_PATTERNS:
            if pattern.search(f"{drawing.get('title') or ''} "
                              f"{drawing.get('drawing_no') or ''}"):
                counts[key] = counts.get(key, 0) + 1
                break
    if not counts:
        return None
    return max(counts.items(), key=lambda kv: (kv[1], kv[0]))[0]


def _same_unit(drawing: dict, unit: str | None) -> bool:
    if unit is None:
        return True
    text = f"{drawing.get('title') or ''} {drawing.get('drawing_no') or ''}"
    for pattern, key in _BUILDING_PATTERNS:
        if pattern.search(text):
            return key == unit
    return True          # 没写单体的图不排除——它可能就是本层通用图


def pick_element_drawings(
    floor_drawings: list[dict], transforms: dict | None = None,
    placements: dict | None = None,
) -> dict[str, list[dict]]:
    """楼层图纸 → 各构件类的「最适图纸」清单（蓝图 4 节规则）。

    选图规则（**顺序即优先级**，实测教训见下）：

    1. **只取同一个单体的图** —— 南区与北区各有各的坐标系原点，
       混着取会差几十米。实测 F2 两图构件中心散布 **103 米**、F3 **83 米**，
       正是南北区混取造成的。
    2. **有标准比例变换的优先** —— 位置才靠得住（见 `_transform_rank`）。
    3. 每类仍有张数上限，控制构建时长。

    `transforms` 形如 ``{drawing_id: DrawingTransform}``；不传时退回
    「按原顺序取前 N 张」的旧行为，老调用方不受影响。
    """
    unit = _dominant_unit(floor_drawings)
    structure: list[dict] = []
    beams: list[dict] = []
    mep: list[dict] = []
    for drawing in floor_drawings:
        if not _same_unit(drawing, unit):
            continue                  # 跨单体不混取——这是错位几十米的根因
        title = str(drawing.get("title") or "")
        discipline = str(drawing.get("discipline") or "")
        if discipline == "mep":
            mep.append(drawing)
        elif _BEAM_TITLE_RE.search(title):
            beams.append(drawing)
        elif _STRUCTURE_TITLE_RE.search(title) or discipline == "structure":
            structure.append(drawing)
        elif _transform_rank(drawing, transforms, placements) < 0:
            # **有世界坐标却进不了任何桶** —— 实测 19 张里有 3 张这样
            # （屋顶花园排水组织图、隔声隔振平面图、夹层平面图，
            # 都是 architecture 且标题无结构词）。它们的位置是**绝对可信**的
            # （锚点求解、残差毫米级），整张丢弃太可惜；建筑平面图上
            # 本就有墙、柱、门窗。**只对有世界坐标的图开这个口子** ——
            # 位置不可信的图进来只会添噪声。
            structure.append(drawing)

    def by_quality(items: list[dict]) -> list[dict]:
        # 同等可靠度时按 drawing_id 定序 —— stable sort 保留的是**输入顺序**，
        # 而 builder 与诊断脚本拿到的顺序不同，会算出不同结果（曾为此排查三轮）。
        return sorted(items, key=lambda d: (
            _transform_rank(d, transforms, placements), str(d.get("id") or "")))

    def take(items: list[dict], limit: int) -> list[dict]:
        """有世界摆放的**全取**，常规配额只填充其余。

        实测 F2 层有 8 张图带世界坐标却只摆放了 3 张 —— 被 `limit` 挡掉了。
        全项目 2309 张里只有 19 张有世界坐标，把它们全用上成本可控，
        而它们的位置是**绝对**可信的（残差毫米级），不该被上限浪费。
        存疑的摆放不享受此待遇。
        """
        ordered = by_quality(items)
        has_world = [d for d in ordered
                     if _transform_rank(d, transforms, placements) < 0]
        rest = [d for d in ordered if d not in has_world]
        return has_world + rest[:limit]

    return {
        "structure": take(structure, _MAX_STRUCTURE_PLANS),
        "beam": take(beams, _MAX_BEAM_PLANS),
        "mep": take(mep, _MAX_MEP_PLANS),
    }


#: 参与**轴网聚合**的图纸上限。与构件选图上限(2)分开——
#: 构件识别每图要 10~40 秒(几何提取 + 识别 + YOLO)，而轴网只是
#: 「坐标 + 标签」的纯计算，几乎不花时间。
#:
#: **实测缺口**:v33 有六个楼层 scene 里无轴网，而 F1 有 **139 张**
#: 「有轴号且有变换」的图可用 —— 它们全被构件选图的上限挡在外面了。
#:
#: 仍然限量，但限的理由不同:图越多、变换不一致的风险越大。
#: 按定位可靠度排序后取前若干张，再由 `dedupe_axis_labels` 与
#: 序列校验(§8.0.3)兜底。
MAX_AXIS_SOURCE_PLANS = 12

#: 新图并入前的一致性门禁:与已聚合轴网**同名**的轴号里，位置对不上的比例。
#:
#: **实测教训**:把聚合上限从 2 提到 12 后，轴网覆盖**从 6 层跌到 2 层**——
#: 新引入的图变换与主组不一致，同名轴号落在不同位置，冲突暴增
#: (B3 一层 **74 条**)，去重后保留的反而更少。
#:
#: **更多来源 ≠ 更好的结果**。逐张检验:对不上的比例超过此值就跳过该图，
#: 让聚合自动收敛到「变换一致的那一组」。
MAX_AXIS_DISAGREEMENT_RATIO = 0.3


def collect_floor_axes(
    floor_drawings: list[dict], *, transforms: dict | None,
    recognized: dict | None, max_drawings: int = MAX_AXIS_SOURCE_PLANS,
) -> dict:
    """从本层**所有**有轴号且有变换的图聚合轴网(不受构件选图上限约束)。

    没有变换的图**跳过** —— 没有米坐标就没法把轴线放到正确位置，
    硬放只会制造错位。

    按定位可靠度排序:同名冲突时先到的胜出(见 `dedupe_axis_labels`)，
    所以最可靠的那张要排在前面。
    """
    if not transforms or not recognized:
        return {"x": [], "y": []}

    from services.axis_recognition import axes_to_scene

    usable = [d for d in floor_drawings
              if str(d.get("id") or "") in transforms
              and recognized.get(str(d.get("id") or ""))]
    # **排序键必须完全确定**:`_transform_rank` 只有 0/1/2 三档，
    # stable sort 在同档内保持**输入顺序**。而 builder 拿到的是 DB 返回顺序、
    # 诊断脚本拿到的是 scene 顺序 —— 两者的「前 N 张」不是同一批，
    # 于是同一层算出的轴网不同、诊断结论无法预期 builder 的行为。
    # 同档时按 drawing_id 定序，让结果可复现。
    usable.sort(key=lambda d: (_transform_rank(d, transforms),
                               str(d.get("id") or "")))

    candidates: list[tuple[str, dict]] = []
    for drawing in usable[:max_drawings]:
        did = str(drawing.get("id") or "")
        try:
            candidates.append((did, axes_to_scene(recognized[did], transforms[did])))
        except Exception as exc:  # noqa: BLE001 — 单图失败不拖垮整层
            logger.info("[ModelElements] 轴网聚合跳过 %s: %s", did, exc)
    if not candidates:
        return {"x": [], "y": []}

    group = _largest_consistent_group(candidates)
    aggregated: dict | None = None
    for _did, scene_axes in group:
        aggregated = _merge_axes(aggregated, scene_axes, authoritative=True)
    if len(group) < len(candidates):
        logger.info("[ModelElements] 轴网聚合采纳 %d/%d 张（其余变换与主组不一致）",
                    len(group), len(candidates))
    return aggregated or {"x": [], "y": []}


def _largest_consistent_group(
    candidates: list[tuple[str, dict]],
) -> list[tuple[str, dict]]:
    """找出**彼此一致的最大那组**图纸。

    **为什么不能只与第一张比**:排序取到的第一张若恰好是离群值，
    后面**正确的会被全部挡掉**。实测 v35 里 F1（195 张图）、F2、F3、B1
    在只与第一张比时全部失去轴网，而它们在 v33 是有的 ——
    基准选错的代价是整层归零。

    做法:以每张图为基准各试一遍，取能吸纳最多图的那一组。
    候选最多 `MAX_AXIS_SOURCE_PLANS` 张，O(n²) 完全可接受。
    平局时取靠前的（已按定位可靠度排序）。
    """
    best: list[tuple[str, dict]] = []
    for base_index, (_bid, base_axes) in enumerate(candidates):
        group = [candidates[base_index]]
        merged = dict(base_axes)
        for index, (did, axes) in enumerate(candidates):
            if index == base_index:
                continue
            if _axes_disagree(merged, axes):
                continue
            group.append((did, axes))
            merged = _merge_axes(dict(merged), axes, authoritative=True)
        if len(group) > len(best):
            best = group
    return best


def _axes_disagree(
    aggregated: dict, candidate: dict,
    max_ratio: float = MAX_AXIS_DISAGREEMENT_RATIO,
) -> bool:
    """候选轴网与已聚合的主组是否**位置对不上**。

    只比**同名**轴号 —— 没有同名的说明两者覆盖不同区域，那不是矛盾，
    是互补，应当并入。
    """
    compared = mismatched = 0
    for direction in ("x", "y"):
        known = {}
        for label, pos in aggregated.get(direction) or ():
            text = str(label or "").strip()
            if text:
                known.setdefault(text, float(pos))
        for label, pos in candidate.get(direction) or ():
            text = str(label or "").strip()
            if text and text in known:
                compared += 1
                if abs(float(pos) - known[text]) > _AXIS_MERGE_TOL_M:
                    mismatched += 1
    if compared == 0:
        return False          # 无同名可比 —— 是互补不是矛盾
    return mismatched / compared > max_ratio


def _prefer_collected_axes(collected: dict | None, fallback: dict | None) -> dict | None:
    """优先用独立聚合的轴网，聚不出才退回构件循环里攒的那份。

    **不能写成 `collected or fallback`**：`{"x": [], "y": []}` 是**非空 dict、
    truthy**，`or` 永远不会回落 —— 没有识别轴号或没有变换的场景会整个丢掉轴网。
    要看的是**里面有没有内容**，不是 dict 本身真假。
    """
    if collected and (collected.get("x") or collected.get("y")):
        return collected
    return fallback


def _recognize_sync(
    data: bytes, ext: str, discipline: str, drawing_id: str, allow_circles: bool = False,
) -> dict | None:
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
    # E3/路径B：PDF 圆形桩/圆柱补识别——几何识别器只抓闭合近方多段线,抓不到
    # 圆(桩/钢立柱多画成圆)。栅格 HoughCircles 检圆 → 米坐标八边形柱,去重后并入。
    # 双闸防误检:①仅平面图(allow_circles,剖面/详图里的钢筋圆不算桩)
    #            ②仅结构/通用专业(机电图圆多是设备/管件)。
    if allow_circles and ext == "pdf" and discipline in ("structure", "general"):
        elements["columns"] = _augment_circle_columns(
            data, geom, elements.get("columns") or [], drawing_id
        )
        # E3-4:桩增强后若无板,用桩包络补底板(基坑楼层得到板参与体量/算量)
        elements["slabs"] = ensure_slab_from_columns(
            elements.get("columns") or [], elements.get("slabs") or []
        )
    return {"elements": elements, "axes": result.axes}


# 桩包络补板参数(米):足量柱/桩才补,外扩边距,最小面积
_SLAB_FROM_PILES_MIN = 8
_SLAB_ENVELOPE_MARGIN_M = 1.0
_SLAB_MIN_AREA_M2 = 10.0
_PILE_SLAB_THICKNESS_M = 0.5   # 基坑底板偏厚


def ensure_slab_from_columns(
    columns: list[dict], existing_slabs: list[dict],
) -> list[dict]:
    """E3-4:该图有大量柱/桩却无板时,用柱/桩包络补一块底板(基坑楼层)。

    已有板则原样返回(不覆盖识别结果)。纯函数,离线可测。
    """
    if existing_slabs:
        return existing_slabs
    pts: list[tuple[float, float]] = []
    for col in columns:
        for p in col.get("outline") or []:
            if len(p) >= 2:
                pts.append((float(p[0]), float(p[1])))
    if len(columns) < _SLAB_FROM_PILES_MIN or len(pts) < 3:
        return existing_slabs
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    m = _SLAB_ENVELOPE_MARGIN_M
    x0, x1, y0, y1 = min(xs) - m, max(xs) + m, min(ys) - m, max(ys) + m
    if (x1 - x0) * (y1 - y0) < _SLAB_MIN_AREA_M2:
        return existing_slabs
    from core.model3d.element_recognizer import SLAB_BASIS_COLUMN_ENVELOPE

    return [{
        "outline": [[round(x0, 3), round(y0, 3)], [round(x1, 3), round(y0, 3)],
                    [round(x1, 3), round(y1, 3)], [round(x0, 3), round(y1, 3)]],
        "thickness": _PILE_SLAB_THICKNESS_M,
        # 兜底,不是识别结果 —— 与 element_recognizer 的三条兜底同口径
        "basis": SLAB_BASIS_COLUMN_ENVELOPE,
        "src": "piles-envelope",
    }]


def _augment_circle_columns(
    data: bytes, geom, existing_columns: list[dict], drawing_id: str,
) -> list[dict]:
    """圆检测补柱并去重（失败返回原柱,绝不阻断）。"""
    try:
        from core.model3d.circle_detector import dedupe_against, detect_pile_columns
        circles = detect_pile_columns(data, geom, src=drawing_id)
        if not circles:
            return existing_columns
        fresh = dedupe_against(circles, existing_columns)
        return existing_columns + fresh
    except Exception as exc:  # noqa: BLE001 — 圆柱补充失败不影响既有识别
        logger.warning("[ModelElements] 圆柱补充跳过 %s: %s", drawing_id, exc)
        return existing_columns


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
        # 圆检测仅对平面图开启(剖面/立面/详图里的圆多是钢筋/符号,非平面桩)
        allow_circles = classify_view_type(drawing).view_type in ("plan", "unknown")
        return await asyncio.wait_for(
            loop.run_in_executor(
                executor, _recognize_sync, data, ext, discipline,
                str(drawing["id"]), allow_circles,
            ),
            timeout=_RECOGNIZE_TIMEOUT_SEC,
        )
    except Exception as exc:  # noqa: BLE001 — 单图识别失败跳过
        # **异常类型必须打出来**：`asyncio.TimeoutError` 的 str() 是空字符串，
        # 只打 `exc` 会得到「构件识别跳过 <id>: 」——**跳过了却没说为什么**。
        # 而超时恰恰是最该知道的：它说明那张图大到超了 `_RECOGNIZE_TIMEOUT_SEC`，
        # 处置方式（拆图/提超时）与其他异常完全不同。
        logger.warning(
            "[ModelElements] 构件识别跳过 %s: %s: %s",
            drawing.get("id"), type(exc).__name__, exc or "(无消息)",
        )
        return None


def _merge_elements(target: dict[str, list], parts: dict | None, kinds: tuple[str, ...]) -> None:
    if not parts:
        return
    for kind in kinds:
        target[kind].extend(parts.get(kind) or [])


async def build_floor_elements(
    executor, floor_drawings: list[dict], file_getter: Callable[[str], bytes],
    archive_axes_by_drawing: dict | None = None, transforms: dict | None = None,
    archive_text_by_drawing: dict | None = None,
    recognized_axes_by_drawing: dict | None = None,
    placements: dict | None = None,
) -> tuple[dict[str, list], int, dict]:
    """构建单楼层 elements（识别 → 轴号配准 → 合并 + YOLO 补充）。

    返回 (elements, yolo_count, floor_meta)；floor_meta 含
    ``{"elevations": [标高候选], "registered": 配准图数, "axes": ...}``。
    core.model3d 缺失时返回 (全空, 0, {})。

    A2:传入 archive_axes_by_drawing/transforms 时,每图的档案轴号(好标签)经
    该图坐标变换转米(同图同变换,与识别路径同坐标系),并入聚合——升级识别路径
    的 "X" 噪声标签、补识别未命中的轴线。

    H23:传入 placements 时,**有工程坐标锚点的图按绝对坐标摆放**,并跳过相对
    轴号配准——绝对定位优先于相对对齐。没有锚点的图保持原有相对配准行为
    (诚实降级:不给它编一个世界坐标)。`floor_meta["placed"]` 报出绝对定位的图数。
    """
    empty = {key: [] for key in EMPTY_ELEMENTS}
    try:
        import core.model3d  # noqa: F401 — 探测模块可用性
    except ImportError:
        return empty, 0, {}

    loop = asyncio.get_event_loop()
    # 传入 transforms:选图要按**定位可靠度**排序、且**不跨单体混取**
    # ——这是同层两图构件中心差 83~103 米的根因(见 pick_element_drawings)
    picked = pick_element_drawings(floor_drawings, transforms, placements)
    tasks: list[tuple[dict, str, tuple[str, ...]]] = [
        *[(d, "structure", ("columns", "walls", "slabs")) for d in picked["structure"]],
        *[(d, "structure", ("beams",)) for d in picked["beam"]],
        *[(d, "mep", ("pipes", "equipment")) for d in picked["mep"]],
    ]

    elements: dict[str, list] = empty
    elevations: list[float] = []
    ref_axes: dict | None = None
    ref_axes_drawing_id: str | None = None
    aggregated_axes: dict | None = None  # 跨该层所有图配准对齐后聚合的轴网
    registered = 0
    placed = 0                           # 按工程坐标绝对定位的图数(H23)
    for drawing, discipline, kinds in tasks:
        result = await _recognize_one(loop, executor, drawing, discipline, file_getter)
        if not result:
            continue
        axes = result.get("axes") or {}
        elevations.extend(axes.get("elevations") or [])
        part = result["elements"]
        # A2：并入本图档案轴号(好标签,经该图变换转米,与识别轴号同坐标系)
        did = str(drawing.get("id") or "")
        if archive_axes_by_drawing and transforms and did in transforms:
            arch_items = archive_axes_by_drawing.get(did) or []
            if arch_items:
                arch = archive_axes_to_scene(arch_items, transforms[did])
                axes = _merge_axes(dict(axes), arch)
        # Phase I:并入**轴网识别**产出的轴号。多分区图带分区前缀
        # (§8.0.5「分区号-轴线号」),且全部经国标校验(全项目 0 违规)。
        # **authoritative=True**:识别标签覆盖档案标签。档案是未经校验的
        # OCR 原文(实测噪声 `IX`/`80`/`BY`),只按「空才升级」会让噪声恒定胜出。
        if recognized_axes_by_drawing and transforms and did in transforms:
            from services.axis_recognition import axes_to_scene

            recognized = recognized_axes_by_drawing.get(did) or []
            if recognized:
                axes = _merge_axes(
                    dict(axes), axes_to_scene(recognized, transforms[did]),
                    authoritative=True)
        # C-下一步：按位置给本图构件附类型标签(钢构/幕墙/围护桩;OCR 短标签
        # 经该图变换转米就近关联,与构件同坐标系;配准前处理,标签随构件平移)
        if archive_text_by_drawing and transforms and did in transforms:
            text_items = archive_text_by_drawing.get(did) or []
            if text_items:
                part = _attach_component_type_labels(part, text_items, transforms[did])
        # H23：有工程坐标锚点的图,按绝对坐标摆到工程坐标系(优先于相对配准)
        placement = (placements or {}).get(did)
        if placement:
            from services.model_world_placement import place_elements
            part = place_elements(part, placement)
            placed += 1

        # 轴号配准：以本层首张带轴号的图为参考系，其余图按共有轴号平移对齐
        # 已绝对定位的图不再相对平移——否则会被拉离它的真实工程坐标
        if _has_labeled_axes(axes):
            if ref_axes is None:
                ref_axes = axes
                ref_axes_drawing_id = str(drawing.get("id") or "")
                aligned_axes = axes
            elif placement:
                aligned_axes = axes
            else:
                dx, dy = register_offset(ref_axes, axes)
                part = _shift_elements(part, dx, dy)
                aligned_axes = _shift_axes(axes, dx, dy)
                registered += 1
            # E2 覆盖提升：聚合本层所有已识别图的轴网（对齐到同一坐标系），
            # 不再只取首张——多张结构/梁图各识别到部分轴线，并集才完整。
            aggregated_axes = _merge_axes(aggregated_axes, aligned_axes)
        _merge_elements(elements, part, kinds)

    yolo_count = await _yolo_supplement(loop, executor, picked["mep"], elements, file_getter)
    meta = {
        "elevations": sorted(set(elevations)),
        "registered": registered,
        # placed = 按工程坐标绝对定位的图数;与 registered(相对配准)并列报出,
        # 这一层到底有多少图是真定位、多少是相对贴合,一眼可见
        "placed": placed,
        # 轴网**不受构件选图上限约束**:构件识别每图 10~40 秒所以限 2 张，
        # 而轴网只是坐标 + 标签的纯计算。实测 v33 有六层 scene 无轴网，
        # 而 F1 有 139 张「有轴号且有变换」的图被白白挡在外面。
        # 先用本层可用图聚合，聚不出再退回构件循环里攒的那份。
        "axes": _axes_scene_payload(
            _prefer_collected_axes(
                collect_floor_axes(floor_drawings, transforms=transforms,
                                   recognized=recognized_axes_by_drawing),
                aggregated_axes),
            ref_axes_drawing_id),
    }
    return elements, yolo_count, meta


_AXIS_MERGE_TOL_M = 0.3  # 同轴线去重容差（米）


def _shift_axes(axes: dict, dx: float, dy: float) -> dict:
    """按配准偏移平移轴网坐标（x 位置移 dx、y 位置移 dy），对齐到参考坐标系。"""
    return {
        "x": [[label, float(pos) + dx] for label, pos in (axes.get("x") or [])],
        "y": [[label, float(pos) + dy] for label, pos in (axes.get("y") or [])],
    }


def dedupe_axis_labels(axes: dict | None) -> tuple[dict, int]:
    """同一方向上**一个轴号只保留一条轴线**，并按坐标排序。

    **国标依据**：GB/T 50001 §8.0.3「依次注写」+ §8.0.5 分区编号 ⇒
    同一分区、同一方向上一个轴号只对应一条轴线。

    **实测必要性**：模型 v31 的 F5 层出现

    ```
    {"coord":  8.394, "label": "2"}
    {"coord": 16.290, "label": "2"}   ← 同名 `2` 在两个位置
    ```

    因为 `_merge_axes` **只按坐标去重（容差 0.3 米），不看标签**，
    同一条 `2` 轴在两张图上因变换差异落到相距 **7.9 米** 的两处，
    超出容差就变成了两条同名轴线。

    **同名冲突的真正含义不是标签写错，而是这些图的坐标变换不一致**——
    整套轴网都偏了。留哪条都不对，所以:保留**先到的那条**
    （选图已按定位可靠度排序，第一张最可靠），并把冲突数报出来。

    返回 ``(去重后的轴网, 冲突条数)``。
    """
    if not axes:
        return {"x": [], "y": []}, 0
    out: dict[str, list] = {"x": [], "y": []}
    conflicts = 0
    for direction in ("x", "y"):
        seen: set[str] = set()
        for entry in axes.get(direction) or []:
            label = str(entry[0] or "").strip()
            if label:
                if label in seen:
                    conflicts += 1        # 变换不一致的信号，不是标签错
                    continue
                seen.add(label)
            out[direction].append([entry[0], float(entry[1])])
        out[direction].sort(key=lambda e: e[1])   # §8.0.3 依次注写
    return out, conflicts


def _merge_axes(agg: dict | None, new: dict, *,
                authoritative: bool = False) -> dict:
    """并入一张图的轴网：按坐标去重（容差内视为同轴）。

    标签冲突规则：
    * 默认（``authoritative=False``）：只有**无标签**的轴线会被升级，
      已有标签保持不动。
    * ``authoritative=True``：新标签**覆盖**已有标签——留给识别路径用。

    **为什么需要这个区分**：档案的 axis 条目是未经校验的 OCR 原文，
    实测样本 ``IX / 80 / 3 / 0 / BY / M / E / P / S`` —— `IX` 含国标禁用字母 I
    （§8.0.4）、数字是尺寸碎片、字母是图框专业代号，43643 条覆盖全部 2309 张图。
    识别路径的轴号由几何推导 + 国标校验（全项目 0 违规）得来。
    档案先合、识别后合，若只按「空才升级」，噪声会恒定压过真轴号。
    """
    if agg is None:
        agg = {"x": [], "y": []}
    for direction in ("x", "y"):
        existing = agg[direction]
        for label, pos in (new.get(direction) or []):
            pos = float(pos)
            hit = next(
                (e for e in existing if abs(e[1] - pos) < _AXIS_MERGE_TOL_M), None
            )
            if hit is None:
                existing.append([label, pos])
                continue
            if not str(label).strip():
                continue                      # 空标签永不覆盖已有标签
            if authoritative or not str(hit[0]).strip():
                hit[0] = label
    return agg


def _attach_component_type_labels(part: dict, text_items: list[dict], transform) -> dict:
    """C-下一步:档案短标签 → 构件类型,就近关联到本图构件(失败返回原 part)。"""
    try:
        from core.model3d.component_labels import (
            attach_type_labels,
            classify_component_labels,
        )
        labels = classify_component_labels(text_items, transform)
        if not labels:
            return part
        return attach_type_labels(part, labels)
    except Exception:  # noqa: BLE001 — 类型标签失败不影响几何构件
        return part


def _axis_point_of(loc: dict) -> tuple[float | None, float | None]:
    """档案位置 → 页面点 (x_pt, y_pt);缺 x/y 时**从 bbox 中心兜底**。

    实测:全项目 43612 条轴号中 **17023 条(39%)只有 bbox 没有 x/y**,
    原实现直接跳过 → 轴网覆盖凭空少四成。bbox 为 [x0,y0,x1,y1],取中心即可。
    """
    x_pt, y_pt = loc.get("x"), loc.get("y")
    if x_pt is not None and y_pt is not None:
        return float(x_pt), float(y_pt)
    bbox = loc.get("bbox")
    if isinstance(bbox, (list, tuple)) and len(bbox) >= 4:
        try:
            x0, y0, x1, y1 = (float(v) for v in bbox[:4])
        except (TypeError, ValueError):
            return None, None
        return (x0 + x1) / 2, (y0 + y1) / 2
    return None, None


def archive_axes_to_scene(archive_items: list[dict], transform) -> dict:
    """档案 axis 项(label + pt 位置)→ scene 轴网格式 {"x":[[label,pos_m]], "y":[...]}。

    A2:档案 OCR/矢量抽出的轴号质量高(真实 1/2/3/A/B),但坐标是页面点;
    经每图坐标变换(A1)转米,与识别路径 _merge_axes 合并(升级 "X" 标签 + 补覆盖)。
    方向按轴号惯例:纯数字→x(竖轴),字母→y(横轴)。
    """
    from services.drawing_transform import pt_to_meter

    x_axes: list[list] = []
    y_axes: list[list] = []
    for item in archive_items:
        label = str(item.get("content") or "").strip()
        if not _is_grid_label(label):
            continue
        loc = item.get("location_json") or {}
        x_pt, y_pt = _axis_point_of(loc)
        if x_pt is None or y_pt is None:
            continue
        x_m, y_m = pt_to_meter(float(x_pt), float(y_pt), transform)
        if label.isdigit():
            x_axes.append([label, x_m])       # 数字轴号 → 竖轴(x 位置)
        else:
            y_axes.append([label, y_m])       # 字母轴号 → 横轴(y 位置)
    return {"x": x_axes, "y": y_axes}


def _is_grid_label(label: str) -> bool:
    """真实轴号判定（GB/T 50001 第 8 章）。

    合法形式:

    * 数字轴号 `1`/`24`（§8.0.3 横向从左至右）
    * 单字母 `A`~`Y`，**跳过 I、O、Z**（§8.0.4）
    * 双字母 `AA`/`BB` —— §8.0.4「不够用时可用**双字母**」，指重复同一字母
    * 字母加数字注脚 `A1`/`B2` —— §8.0.4 的另一种形式
    * 分区编号 `1-1`/`2-A`（§8.0.5）
    * 附加轴线分数式 `1/A`（§8.0.6）

    **两个不同字母的组合（`BY`/`AC`/`PS`）不是国标形式**——
    实测它们是图框专业代号(Phase I 已查明 OCR 在图框读出
    `A/BY/E/M/P/S`)，混进来后出现在 F2/F4 的轴号序列开头。
    """
    from core.model3d.drawing_conventions import FORBIDDEN_AXIS_LETTERS

    raw = (label or "").strip()
    if not raw or len(raw) > 4:
        return False
    # §8.0.5 分区编号:去掉「分区号-」前缀后按轴线号判
    if "-" in raw:
        head, _, tail = raw.partition("-")
        return bool(head) and _is_grid_label(tail)
    if raw.isdigit():
        return True
    # §8.0.6 附加轴线分数式
    if "/" in raw and len(raw) <= 4:
        return True
    upper = raw.upper()
    if upper.isalpha():
        if len(upper) == 1:
            return upper not in FORBIDDEN_AXIS_LETTERS
        if len(upper) == 2:
            # §8.0.4「双字母」= 重复同一字母;两个不同字母是图框代号
            return upper[0] == upper[1] and upper[0] not in FORBIDDEN_AXIS_LETTERS
        return False
    # §8.0.4「字母加数字注脚」
    if len(upper) >= 2 and upper[0].isalpha() and upper[1:].isdigit():
        return upper[0] not in FORBIDDEN_AXIS_LETTERS
    return False


#: **离群轴号**占比上限（不在最长递增子序列上的那些）。超过就不输出该方向。
#:
#: **实测违规**（v32/v33 的 F5 层 x 向）:`1 2 3 4 5 10 12 14 6 15 7 8`
#: —— `1~8` 是连续的 8 个、`10 12 14 15` 是另外 4 个,**两套轴网交织**,
#: 正是坐标变换不一致的表现。§8.0.3 规定轴号随坐标单调递增。
#:
#: **不能用「相邻逆序次数」度量**:该序列只有 **2 次**相邻逆序(17%),
#: 低于阈值而漏掉;而按「不在最长递增子序列上」算是 **3 条(25%)**,
#: 才反映出真实的错乱程度。
#:
#: 取 0.2:附加轴线(§8.0.6)、局部补号会造成个别离群,
#: 但两套轴网交织必然产生大量离群。
MAX_SEQUENCE_OUTLIER_RATIO = 0.2


def _sequence_rank(label: str) -> tuple[str, int] | None:
    """轴号 → (分区, 序号)。认不出返回 None（不参与序列校验）。

    §8.0.5 的分区前缀要剥掉后再比——不同分区各自从 1 开始，跨区比较会误报。
    §8.0.4 的字母跳过 I/O/Z，所以字母序号按 `AXIS_LETTERS` 的位置算，
    这样 `H` 之后是 `J` 不算逆序。
    """
    from core.model3d.drawing_conventions import AXIS_LETTERS

    raw = str(label or "").strip()
    if not raw:
        return None
    zone = ""
    if "-" in raw:
        zone, _, raw = raw.partition("-")
    if raw.isdigit():
        return (f"{zone}#num", int(raw))
    upper = raw.upper()
    if len(upper) == 1 and upper in AXIS_LETTERS:
        return (f"{zone}#alpha", AXIS_LETTERS.index(upper))
    return None


def axis_sequence_outliers(entries: list[dict]) -> int:
    """按坐标排序后，**不在最长递增子序列上**的轴号数（§8.0.3 依次注写）。

    **为什么不用「相邻逆序次数」**:实测 F5 的
    `1 2 3 4 5 10 12 14 6 15 7 8` 只有 **2 次**相邻逆序（17%），
    低于阈值而漏掉;但它其实是**两套轴网交织**
    （`1~8` 一套、`10 12 14 15` 一套）。按最长递增子序列算，
    离群 **3 条（25%）**，才反映出真实错乱程度。

    数字与字母各自成序——混在一起比较没有意义。
    """
    import bisect

    groups: dict[str, list[tuple[float, int]]] = {}
    for entry in entries or ():
        rank = _sequence_rank(entry.get("label", ""))
        if rank is None:
            continue
        groups.setdefault(rank[0], []).append(
            (float(entry.get("coord", 0.0)), rank[1]))
    outliers = 0
    for items in groups.values():
        items.sort(key=lambda t: t[0])
        seq = [n for _c, n in items]
        tails: list[int] = []          # 最长**严格**递增子序列
        for value in seq:
            index = bisect.bisect_left(tails, value)
            if index == len(tails):
                tails.append(value)
            else:
                tails[index] = value
        outliers += len(seq) - len(tails)
    return outliers


def _axes_scene_payload(axes: dict | None, source_drawing_id: str | None) -> dict | None:
    """聚合轴网 → scene floor.axes 载荷；无带标签轴网返回 None。

    输出：{"x": [{"label","coord"}...], "y": [...], "source_drawing_id"}，
    坐标为米（与构件同坐标系），仅收带标签且通过去噪的轴线，按坐标排序。
    """
    if not axes:
        return None

    # **同名轴号只留一条**（§8.0.3/§8.0.5）。实测 v31 的 F5 层出现两条
    # 都叫 `2` 的轴线（8.394 与 16.290，相距 7.9 米）——那是两张图的坐标
    # 变换不一致，不是标签写错。冲突数一并报出，供诊断变换质量。
    axes, label_conflicts = dedupe_axis_labels(axes)
    if label_conflicts:
        logger.warning(
            "[ModelElements] 轴号同名冲突 %d 条（图纸变换不一致，已保留先到者）",
            label_conflicts)

    def _entries(direction: str) -> list[dict]:
        out = [
            {"label": str(label).strip(), "coord": round(float(pos), 3)}
            for label, pos in (axes.get(direction) or [])
            if _is_grid_label(str(label or ""))
        ]
        return sorted(out, key=lambda e: e["coord"])

    x_entries = _entries("x")
    y_entries = _entries("y")

    # §8.0.3 依次注写:轴号必须随坐标单调递增。大面积逆序说明这些轴线
    # 来自变换不一致的多张图 —— **宁可不给,也不给一套顺序错乱的轴网**。
    inversions = 0
    for direction, entries in (("x", x_entries), ("y", y_entries)):
        bad = axis_sequence_outliers(entries)
        inversions += bad
        if entries and bad / len(entries) >= MAX_SEQUENCE_OUTLIER_RATIO:
            logger.warning(
                "[ModelElements] %s 向轴号离群 %d/%d（≥%.0f%%）—— 疑为两套轴网"
                "交织，不输出该方向", direction, bad, len(entries),
                MAX_SEQUENCE_OUTLIER_RATIO * 100)
            entries.clear()

    if not x_entries and not y_entries:
        return None
    return {"x": x_entries, "y": y_entries,
            "source_drawing_id": source_drawing_id or "",
            # >0 说明本层各图的坐标变换不一致，轴网位置不可尽信
            "label_conflicts": label_conflicts,
            # §8.0.3 离群轴号数;大面积离群的方向已被剔除
            "sequence_outliers": inversions}


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


#: 板的统计里额外报一个「真识别出来的板数」。
#: 板有四种来源(见 `element_recognizer.SLAB_BASIS_*`),只有图层/块名命中那种
#: 算识别结果,其余三种是兜底。混在一个 `slabs` 数字里,
#: 「0 块真板」会显示成「N 块板」—— 实测大歌剧院 v30 正是如此。
#: 缺 `basis` 的旧数据按**兜底**处理,绝不默认算成成果。
SLABS_RECOGNISED_KEY = "slabs_recognised"


def element_stats(elements: dict[str, list]) -> dict[str, int]:
    from core.model3d.element_recognizer import SLAB_BASIS_RECOGNISED

    stats = {key: len(elements.get(key) or []) for key in EMPTY_ELEMENTS}
    stats[SLABS_RECOGNISED_KEY] = sum(
        1 for slab in (elements.get("slabs") or [])
        if (slab or {}).get("basis") == SLAB_BASIS_RECOGNISED)
    return stats


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
    result[SLABS_RECOGNISED_KEY] = 0
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
    stories_by_building: dict[str, list] | None = None,
) -> list[dict]:
    """按单体分组楼层（同楼层图纸可能分属多单体 → 楼层按单体拆分）。

    输入 floors 为拍平楼层（V1 结构 + elements）；输出蓝图 buildings 数组。
    楼层构件按 src 来源图纸切分到所属单体（不重复归组）。

    ``stories_by_building``：各单体自己的楼层表。**必须传** —— 否则各单体
    共用汇总层的标高，实测值到不了 3D。实测 north 的 RF 图纸值 25.00
    与 main 的 33.90 差 **8.9 米**，共用一个数就是把两个单体摞错位置。
    """
    normalized_assignments = normalized_assignments or {}
    # (单体, 楼层) → 该单体自己的标高与 provenance
    level_of: dict[tuple[str, str], Any] = {
        (str(unit_key), str(level.story_key)): level
        for unit_key, levels in (stories_by_building or {}).items()
        for level in levels
    }
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
            # 优先用该单体自己的标高；查不到才退回汇总值，
            # 且**不谎报 provenance** —— 退回来的值不属于这个单体。
            level = level_of.get((key, str(floor.get("key") or "")))
            unit_floor = {
                **{k: floor[k] for k in ("key", "label", "elevation", "order")},
                "elevation_m": (float(level.elevation_m) if level is not None
                                else floor.get("elevation_m")),
                "drawings": entries,
                "elements": elements,
                "element_stats": element_stats(elements),
            }
            if level is not None:
                unit_floor["elevation_source"] = str(
                    getattr(level, "elevation_source", "") or "")
                unit_floor["elevation_estimated"] = bool(
                    getattr(level, "elevation_estimated", True))
            building["floors"].append(unit_floor)
    for building in buildings.values():
        building["floors"].sort(key=lambda f: f["order"])
    return sorted(buildings.values(), key=lambda b: b["key"])
