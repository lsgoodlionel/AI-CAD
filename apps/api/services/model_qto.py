"""IFC-QTO 算量（工作块六 B-16/B-17/B-18）。

纯几何计算（离线可手算校验）+ IFC 量集写入（ifcopenshell）：
- B-16 混凝土净体积：毛体积 − 拓扑扣减（梁嵌柱、板压梁），保留毛体积供对比。
- B-17 模板量：接触模板面（侧+底）与自由面（顶/端）分列，扣构件相交面。
- B-18 钢筋量：只读复用 rebar_calculator.optimize_cutting（不改算法），无配筋标 rebar_missing。

MVP 扣减口径（确定性、可手算）：
- 柱：net = 毛（不扣）。
- 梁：net = 毛 − Σ支承端 (柱边/2 × 梁宽 × 梁高)。
- 板：net = 毛 − Σ支承梁 (梁在板内长度 × 梁宽 × 板厚)。
变截面/地区细则留后续，近似处标注 estimated。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from core.economic.rebar_calculator import BarItem, optimize_cutting
from services.model_topology import build_topology_graph

logger = logging.getLogger(__name__)

# 钢筋下料默认参数（引擎参数缺省兜底；真实取自引擎业务参数）
_DEFAULT_STANDARD_LENGTHS = [9000, 10000, 12000]
_DEFAULT_FIELD_WASTE = {"d6_10": 0.06, "d12_16": 0.045, "d18_22": 0.04, "d25_plus": 0.035}
_DEFAULT_STEEL_PRICE = 4000.0
_DEFAULT_TARGET_WASTE = 0.015
_DEFAULT_AUTO_MIN = 5000.0

_DEFAULT_STORY_HEIGHT_M = 4.5


@dataclass(frozen=True)
class ElementQuantity:
    element_id: str
    element_type: str            # column|wall|beam|slab
    gross_volume_m3: float
    net_volume_m3: float
    formwork_contact_m2: float
    formwork_free_m2: float
    estimated: bool
    source: str
    #: 该量来自**兜底**构件时记下兜底方式，否则为 None。
    #: 实测大歌剧院板混凝土的 **84%** 只来自 15 块 `column_envelope` 板 ——
    #: 那是「找不到真实板时用柱的包络凑」的兜底，与实测的板不是一回事。
    #: 按「降级必须可见」标出来而不是删掉：删掉会让本就稀少的板量归零。
    fallback_basis: str | None = None


def compute_quantities(
    elements: dict,
    *,
    story_height_m: float = _DEFAULT_STORY_HEIGHT_M,
    topology=None,
) -> list[ElementQuantity]:
    """算各构件混凝土毛/净体积 + 模板接触/自由面。elements 为 FloorElements.as_dict 结构。"""
    columns = _with_ids(elements.get("columns"), "column")
    walls = _with_ids(elements.get("walls"), "wall")
    beams = _with_ids(elements.get("beams"), "beam")
    slabs = _with_ids(elements.get("slabs"), "slab")

    graph = topology or build_topology_graph(walls, columns, beams, slabs, [])
    column_boxes = {c["id"]: _bbox(c.get("outline")) for c in columns}
    beam_index = {b["id"]: b for b in beams}

    result: list[ElementQuantity] = []
    for column in columns:
        result.append(_column_quantity(column, story_height_m))
    for wall in walls:
        result.append(_wall_quantity(wall, story_height_m))
    for beam in beams:
        result.append(_beam_quantity(beam, graph, column_boxes))
    for slab in slabs:
        result.append(_slab_quantity(slab, graph, beam_index))
    return result


# ── 混凝土 + 模板（按构件）───────────────────────────────────

# ── 轮廓规范化：朴素鞋带公式在自交环上会失效 ──────────────────────
#
# 存量 10170 个柱里 3166 个（31.1%）的轮廓是这个形状：
#
#     A(75.969,16.585) → B(75.769,16.585) → C(75.969,16.385) → D(75.769,16.385)
#            上边              对角线             下边              对角线回
#
# 四角走成对角交叉的「蝴蝶结」，且带重复点（轮廓降点留下的）。
# 两个三角形面积等值反号，鞋带和**恰好抵消为 0** —— 于是这些柱的混凝土量
# 算出来是 0，而 QTO 汇总是三审审批人看的依据。周长同样受害：
# 朴素周长会把两条对角线算进去，模板接触面积高估 21%。
#
# 兜底只在朴素公式失效（面积为 0）时介入，其余一律不动 ——
# 存量另外 69% 的柱走原路，数字一个不变。兜底出来的值一律标 `estimated`，
# 因为它是从点集重建的凸包，对异形柱（L 形）会偏大，这个偏差必须可见。


def _dedup(outline) -> list[tuple[float, float]]:
    """去掉重复顶点，保持原有顺序。"""
    seen: list[tuple[float, float]] = []
    for pt in outline or ():
        p = (float(pt[0]), float(pt[1]))
        if p not in seen:
            seen.append(p)
    return seen


def _convex_hull(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Andrew monotone chain。返回逆时针环，点数不足或共线时返回原点集。"""
    pts = sorted(set(points))
    if len(pts) < 3:
        return pts

    def cross(o, a, b) -> float:
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: list[tuple[float, float]] = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper: list[tuple[float, float]] = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def _ring_area(outline) -> tuple[float, bool]:
    """(面积, 是否兜底而来)。面积算得出来就原样返回，算不出来才重建凸包。"""
    area = _polygon_area(outline)
    if area > 0.0:
        return area, False
    hull = _convex_hull(_dedup(outline))
    return _polygon_area(hull), True


def _ring_perimeter(outline) -> tuple[float, bool]:
    """(周长, 是否兜底而来)。判据与面积一致 —— 面积失效说明环序不可信，
    这时朴素周长同样不可信（它会把自交的对角线算进去）。"""
    if _polygon_area(outline) > 0.0:
        return _polygon_perimeter(outline), False
    return _polygon_perimeter(_convex_hull(_dedup(outline))), True


def _column_quantity(column: dict, height: float) -> ElementQuantity:
    area, area_estimated = _ring_area(column.get("outline"))
    perimeter, _ = _ring_perimeter(column.get("outline"))
    gross = area * height
    return _make(
        column, "column", gross, gross,
        contact=perimeter * height,          # 四周支模
        free=2 * area,                        # 顶/底（浇筑面）
        estimated=area_estimated or None,     # 面积兜底了就一定是估算
    )


def _wall_quantity(wall: dict, height: float) -> ElementQuantity:
    length = _segment_length(wall.get("path"))
    width = float(wall.get("width") or 0.2)
    gross = length * width * height
    return _make(
        wall, "wall", gross, gross,
        contact=2 * length * height,          # 两大面
        free=2 * width * height + 2 * length * width,
    )


def _beam_quantity(beam: dict, graph, column_boxes: dict) -> ElementQuantity:
    length = _segment_length(beam.get("path"))
    width = float(beam.get("width") or 0.3)
    depth = float(beam.get("depth") or 0.6)
    gross = length * width * depth

    deduct = 0.0
    for column_id in graph.columns_under(beam["id"]):
        box = column_boxes.get(column_id)
        if box is None:
            continue
        col_side = min(box[2] - box[0], box[3] - box[1])
        deduct += (col_side / 2.0) * width * depth
    net = max(gross - deduct, 0.0)
    return _make(
        beam, "beam", gross, net,
        contact=(2 * depth + width) * length,   # 两侧 + 底
        free=width * length + 2 * width * depth,  # 顶（贴板）+ 两端
    )


#: 哪些 `basis` 算兜底 —— 这两种都不是从图上认出来的板，
#: 而是用别的构件的包络凑出来的。
FALLBACK_SLAB_BASES = ("column_envelope", "axis_envelope")


def _slab_quantity(slab: dict, graph, beam_index: dict) -> ElementQuantity:
    # 板同样踩自交环：实测 514 块里 69 块（13.4%）鞋带面积为 0。
    # 板比柱更可能是凹的（L 形楼板、带洞口），凸包兜底会偏大 —— 但兜底只在
    # 原值已经是 0 时介入，此时凸包再不准也比 0 接近真值，且一律标 estimated。
    area, area_estimated = _ring_area(slab.get("outline"))
    perimeter, _ = _ring_perimeter(slab.get("outline"))
    thickness = float(slab.get("thickness") or 0.12)
    gross = area * thickness
    box = _bbox(slab.get("outline"))

    deduct = 0.0
    soffit_deduct = 0.0
    for beam_id in graph.beams_under(slab["id"]):
        beam = beam_index.get(beam_id)
        if beam is None:
            continue
        overlap = _segment_aabb_overlap(beam.get("path"), box)
        width = float(beam.get("width") or 0.3)
        deduct += overlap * width * thickness
        soffit_deduct += overlap * width
    net = max(gross - deduct, 0.0)
    return _make(
        slab, "slab", gross, net,
        contact=max(area - soffit_deduct, 0.0) + perimeter * thickness,  # 底模（扣梁顶）+ 侧边
        free=area,                                                        # 顶面
        estimated=area_estimated or None,
    )


def _make(element: dict, element_type: str, gross: float, net: float,
          *, contact: float, free: float,
          estimated: bool | None = None) -> ElementQuantity:
    # `estimated` 传 True 表示调用方另有理由判定它是估算（如面积走了兜底）；
    # 传 None 就只看 z_source。两个理由是**或**的关系，不能互相抵消。
    estimated = bool(estimated) or str(element.get("z_source") or "") != "measured"
    return ElementQuantity(
        element_id=str(element.get("id")),
        element_type=element_type,
        gross_volume_m3=round(gross, 4),
        net_volume_m3=round(net, 4),
        formwork_contact_m2=round(contact, 4),
        formwork_free_m2=round(free, 4),
        estimated=estimated,
        source="measured" if not estimated else "estimated",
        fallback_basis=(str(element.get("basis"))
                        if str(element.get("basis") or "") in FALLBACK_SLAB_BASES
                        else None),
    )


# ── B-18 钢筋量 ───────────────────────────────────────────────

def compute_rebar_quantities(rebar_inputs: list[dict], params: dict | None = None) -> dict:
    """只读复用 optimize_cutting 得钢筋量。无配筋 → rebar_missing，不臆造。"""
    if not rebar_inputs:
        return {"rebar_missing": True, "total_steel_kg": None}

    params = params or {}
    bars = [
        BarItem(
            int(item["diameter"]),
            str(item.get("steel_grade") or "HRB400"),
            int(item["required_length"]),
            int(item.get("count") or 1),
        )
        for item in rebar_inputs
    ]
    _patterns, summary = optimize_cutting(
        bars,
        list(params.get("standard_lengths") or _DEFAULT_STANDARD_LENGTHS),
        dict(params.get("field_waste_rates") or _DEFAULT_FIELD_WASTE),
        float(params.get("steel_price_per_ton") or _DEFAULT_STEEL_PRICE),
        float(params.get("target_waste_rate") or _DEFAULT_TARGET_WASTE),
        float(params.get("auto_proposal_min_saving") or _DEFAULT_AUTO_MIN),
    )
    return {
        "rebar_missing": False,
        "total_steel_kg": summary["total_steel_kg"],
        "summary": summary,
    }


# ── IFC 量集写入（ifcopenshell）───────────────────────────────

_IFC_CLASS = {"column": "IfcColumn", "wall": "IfcWall", "beam": "IfcBeam", "slab": "IfcSlab"}
_QTO_NAME = {
    "column": "Qto_ColumnBaseQuantities",
    "wall": "Qto_WallBaseQuantities",
    "beam": "Qto_BeamBaseQuantities",
    "slab": "Qto_SlabBaseQuantities",
}


def write_concrete_quantities(ifc_model, elements: dict, *, story_height_m: float = _DEFAULT_STORY_HEIGHT_M, topology=None) -> int:
    """把净/毛体积挂到 IFC 构件量集（按 IFC 类 + 顺序匹配）。返回挂载数。"""
    quantities = compute_quantities(elements, story_height_m=story_height_m, topology=topology)
    return _attach(
        ifc_model, quantities,
        lambda q: {"NetVolume": q.net_volume_m3, "GrossVolume": q.gross_volume_m3},
        kind="volume",
    )


def write_formwork_quantities(ifc_model, elements: dict, *, story_height_m: float = _DEFAULT_STORY_HEIGHT_M, topology=None) -> int:
    quantities = compute_quantities(elements, story_height_m=story_height_m, topology=topology)
    return _attach(
        ifc_model, quantities,
        lambda q: {"GrossSideArea": q.formwork_contact_m2, "OutsideFreeArea": q.formwork_free_m2},
        kind="area",
    )


def write_rebar_quantities(ifc_model, elements: dict, rebar_inputs: list[dict] | None = None, params: dict | None = None) -> dict:
    """回填钢筋量：有配筋 → 项目级钢筋质量属性；无 → rebar_missing（不臆造）。"""
    result = compute_rebar_quantities(rebar_inputs or [], params)
    if result["rebar_missing"]:
        return result
    try:
        import ifcopenshell.guid

        prop = ifc_model.createIfcQuantityWeight("TotalSteelMass", None, None, float(result["total_steel_kg"]), None)
        qto = ifc_model.createIfcElementQuantity(
            ifcopenshell.guid.new(), None, "Qto_BodyGeometryValidation", None, None, [prop]
        )
        projects = ifc_model.by_type("IfcProject")
        if projects:
            ifc_model.createIfcRelDefinesByProperties(
                ifcopenshell.guid.new(), None, None, None, [projects[0]], qto
            )
    except Exception as exc:  # noqa: BLE001 — 写入失败不阻断算量
        logger.warning("[model_qto] 钢筋量写入 IFC 失败: %s", exc)
    return result


def _attach(ifc_model, quantities, value_fn, *, kind: str) -> int:
    import ifcopenshell.guid

    written = 0
    for element_type, ifc_class in _IFC_CLASS.items():
        products = ifc_model.by_type(ifc_class)
        typed = [q for q in quantities if q.element_type == element_type]
        for product, quantity in zip(products, typed):
            props = _quantity_entities(ifc_model, value_fn(quantity), kind)
            if not props:
                continue
            qto = ifc_model.createIfcElementQuantity(
                ifcopenshell.guid.new(), None, _QTO_NAME[element_type], None, None, props
            )
            ifc_model.createIfcRelDefinesByProperties(
                ifcopenshell.guid.new(), None, None, None, [product], qto
            )
            written += 1
    return written


def _quantity_entities(ifc_model, values: dict, kind: str) -> list:
    entities = []
    for name, value in values.items():
        if kind == "volume":
            entities.append(ifc_model.createIfcQuantityVolume(name, None, None, float(value), None))
        else:
            entities.append(ifc_model.createIfcQuantityArea(name, None, None, float(value), None))
    return entities


# ── 几何原语 ──────────────────────────────────────────────────

def _with_ids(elements, prefix: str) -> list[dict]:
    result: list[dict] = []
    for index, element in enumerate(elements or []):
        item = dict(element)
        item.setdefault("id", f"{prefix}_{index}")
        result.append(item)
    return result


def _polygon_area(outline) -> float:
    if not outline or len(outline) < 3:
        return 0.0
    total = 0.0
    for i in range(len(outline)):
        x0, y0 = float(outline[i][0]), float(outline[i][1])
        x1, y1 = float(outline[(i + 1) % len(outline)][0]), float(outline[(i + 1) % len(outline)][1])
        total += x0 * y1 - x1 * y0
    return abs(total) / 2.0


def _polygon_perimeter(outline) -> float:
    if not outline or len(outline) < 2:
        return 0.0
    total = 0.0
    for i in range(len(outline)):
        x0, y0 = float(outline[i][0]), float(outline[i][1])
        x1, y1 = float(outline[(i + 1) % len(outline)][0]), float(outline[(i + 1) % len(outline)][1])
        total += ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
    return total


def _segment_length(path) -> float:
    if not path or len(path) < 2:
        return 0.0
    x0, y0 = float(path[0][0]), float(path[0][1])
    x1, y1 = float(path[-1][0]), float(path[-1][1])
    return ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5


def _bbox(outline):
    if not outline:
        return None
    xs = [float(p[0]) for p in outline]
    ys = [float(p[1]) for p in outline]
    return min(xs), min(ys), max(xs), max(ys)


def _segment_aabb_overlap(path, box) -> float:
    """线段与 AABB 相交部分长度（Liang-Barsky）。box=None → 0。"""
    if not path or len(path) < 2 or box is None:
        return 0.0
    x0, y0 = float(path[0][0]), float(path[0][1])
    x1, y1 = float(path[-1][0]), float(path[-1][1])
    dx, dy = x1 - x0, y1 - y0
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, x0 - box[0]), (dx, box[2] - x0), (-dy, y0 - box[1]), (dy, box[3] - y0)):
        if p == 0:
            if q < 0:
                return 0.0
            continue
        t = q / p
        if p < 0:
            t0 = max(t0, t)
        else:
            t1 = min(t1, t)
    if t0 > t1:
        return 0.0
    return ((dx * (t1 - t0)) ** 2 + (dy * (t1 - t0)) ** 2) ** 0.5
