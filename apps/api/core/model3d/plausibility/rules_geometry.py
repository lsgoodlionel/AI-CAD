"""几何族规则 —— 「这个形状在数学上能不能成立」。

**为什么这一族基本都是 `impossible`**：它不引用任何规范、不假设任何工程
惯例，只用平面几何的定理。一个环自己穿过自己，鞋带公式就不再是它的面积；
一个多边形在两个方向都有跨度，面积就不可能是零。这类否定不需要取证，
也不会因为项目类型不同而失效。

**两条规则打的是实测存在的真缺陷**（不是假想的）：
- `geom.self_intersecting_outline`：存量 **3166/10170 根柱（31.1%）**、
  69/514 块板因轮廓自交，鞋带面积等值反号相消，混凝土量算成 **0**；
- `geom.duplicate_element`：总图与分图画同一个区域时，同一根柱被落库几遍。

**与已有闸的分工**：`ring_order.repair_ring` 是在**入库前**修环，
`annotation_filter` 是在**识别时**删标注。这一族是**事后复核落库结果** ——
它不改任何数据，只回答「现在库里这份模型，有多少形状根本不成立」。
两者都要有：修复端会漏，复核端才能量出漏了多少。
"""
from __future__ import annotations

from core.model3d.plausibility import geometry as geo
from core.model3d.plausibility.model import Element, PlausibilityModel
from core.model3d.plausibility.registry import register
# 公式出处的拼装与降级与限值那一侧同源，统一放在 rules_quantity（`degrade`/
# `basis_of` 的老家）—— 两套「凭据不硬就降级」的规矩分开写必然漂移。
from core.model3d.plausibility.rules_quantity import (
    formula_basis, formula_ref, formula_severity,
)
from core.model3d.plausibility.types import Finding, Rule, RuleNotApplicable

# ── 阈值 ────────────────────────────────────────────────────────────
# 这一族的阈值都是**量纲门槛**（区分「零」与「非零」），不是工程判据 ——
# 工程量级的判据在 rules_dimension，规范限值在 codes。

#: 面积视为零的门槛（m²）。1 mm² = 1e-6 m²，比任何真实构件截面小四个数量级；
#: 同时远高于浮点误差（坐标量级 1e2 m，双精度相对误差 1e-16 → 绝对约 1e-11 m²）。
AREA_EPS_M2 = 1e-6

#: 短边视为零（退化 / 共线）的门槛（m）= 1 毫米。
#: 依据：图纸本身的绘制精度与落库坐标精度都到不了毫米以下，
#: 短边小于 1mm 的「多边形」只能是共线点列，不是截面。
DEGENERATE_SHORT_M = 1e-3

#: 实心度（面积 ÷ 凸包面积）下限。
#: 依据：**最凹的标准截面**是 L 形与十字形柱。肢厚取边长的 0.3 倍时
#: （已是构造允许的偏薄一档），L 形实心度 = 1 - 0.7² = 0.51，
#: 十字形 = 2×0.3 - 0.3² = 0.51。取 0.35 留一档余量 ——
#: 低于它的轮廓不对应任何标准截面形式，只能是轮廓被撕开了。
#: 这一条是**统计/形态判断而非定理**，所以只出 `suspect`。
MIN_SOLIDITY = 0.35

#: 单个构件最小外接矩形长边的工程上限（m）。**按类分开**，因为量级本就不同。
#: 依据与数量级：
#: - `columns` 6.0：识别器图层路径的柱窗口上限本身是 3.0m（`_is_plausible_column`），
#:   这里留一倍余量只打「离谱」的一档 —— 转换层巨柱截面长边也在 3m 以内。
#: - `beams` 60.0：现浇混凝土梁常见跨度 6~12m，预应力大跨 30~40m；
#:   60m 已越出混凝土梁的量级（再长的是桁架/钢结构，不会以 `beams` 落库）。
#: - `walls` / `slabs` / `pipes` 300.0：单体平面尺寸的量级 ——
#:   实测大歌剧院真实内容跨度约 760m（含室外总图），单体本身数百米；
#:   一道**连续**墙段或一块板长边超过 300m，实际是图框线、总图轮廓，
#:   或跨图拼接时坐标变换出错（实测曾把场景撑到 4.8 公里）。
#: - `equipment` 50.0：单台设备。冷却塔/风机房设备外廓量级在 10m 内，
#:   50m 已经是整个机房而不是一台设备。
def _max_extent_m() -> dict[str, float]:
    """单构件尺度上限，按类分档 —— **运行时**从限值表读。

    这组数原本写死在本模块里，没有出处可查。挪进 `codes.py` 的
    `extent.max_by_kind_m`（标 EMPIRICAL、逐条写明量级依据）之后，
    「这个 300 米是谁定的」在报告里回答得出来。
    """
    from core.model3d.plausibility import codes
    value = codes.value("extent.max_by_kind_m")
    return {k: float(v) for k, v in dict(value).items()}  # type: ignore[arg-type]

#: 判为「重复落库」的重叠比（重叠面积 ÷ 较小者面积）。
#: 依据：`geometry.polygon_overlap_area` 按凸包裁剪会**高估**重叠，
#: 所以阈值必须定在明显的量级上 —— 0.9 意味着两者几乎是同一个形状，
#: 而不是相邻构件的正常搭接（梁柱节点处的搭接占比远低于此）。
DUPLICATE_OVERLAP_RATIO = 0.9

#: 带尺寸语义的标量字段。构件有哪个就查哪个，没有就跳过这个构件 ——
#: 柱没有 `width` 是正常的，不是缺数据。
SIZE_FIELDS = ("width", "thickness", "height")

# ── 各规则回指的公式（`formulas.py` 的 key）──────────────────────────
#
# 只写 key，渲染留到 `check` 里：出处正由取证任务逐条填，
# 在这里拼成字符串等于快照，填好的原文永远到不了报告。

#: 自交轮廓：鞋带公式的成立条件 + 自交时两瓣面积相消。
_SELF_INTERSECT_FORMULAS = ("geometry.shoelace_area",
                            "geometry.signed_area_cancellation")
#: 面积为零却有两向跨度：面积由鞋带算，跨度由最小面积外接矩形量。
_ZERO_AREA_FORMULAS = ("geometry.shoelace_area", "geometry.min_area_rect")
#: 共线退化：短边取自最小面积外接矩形，而它建在凸包上。
_COLLINEAR_FORMULAS = ("geometry.min_area_rect",
                       "geometry.convex_hull_monotone_chain")
#: 单构件尺度：同上，长边口径。
_EXTENT_FORMULAS = ("geometry.min_area_rect",)
#: 实心度：定义式 + 分母的凸包。
_SOLIDITY_FORMULAS = ("geometry.solidity", "geometry.convex_hull_monotone_chain")
#: 重复落库：重叠面积用 Sutherland–Hodgman 裁剪算，面积仍是鞋带。
_DUPLICATE_FORMULAS = ("geometry.polygon_clipping_convex_requirement",
                       "geometry.shoelace_area")


# ── 小工具 ──────────────────────────────────────────────────────────

def _distinct(ring: list) -> list:
    """按位置去重、保序。

    **必须去重再数点**：`path_points` 把每条线段的两个端点都收进来，
    三角形恒为 6 个点。2026-08-28 那条「三顶点多边形占 0%」的结论
    就是栽在原始点数口径上，把一条能走通的路封了半个月 —— 不再重蹈。
    """
    seen: set = set()
    out: list = []
    for point in ring:
        key = (round(float(point[0]), 9), round(float(point[1]), 9))
        if key in seen:
            continue
        seen.add(key)
        out.append((float(point[0]), float(point[1])))
    return out


def _extent_m(element: Element) -> float | None:
    """构件的最大跨度（m）：面状取最小外接矩形长边，线状取路径长度。"""
    ring = element.footprint()
    if len(ring) >= 3:
        rect = geo.min_area_rect(ring)
        if rect is not None:
            return rect[0]
    length = element.length_m()
    return length if length is not None else None


def _numeric(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ── geom.self_intersecting_outline ─────────────────────────────────

def check_self_intersecting(model: PlausibilityModel) -> list[Finding]:
    targets = [e for e in model.elements() if len(e.outline) >= 3]
    if not targets:
        raise RuleNotApplicable("模型里没有带轮廓（outline）的构件")
    findings: list[Finding] = []
    for element in targets:
        ring = element.outline
        if not geo.is_self_intersecting(ring):
            continue
        findings.append(Finding(
            rule="geom.self_intersecting_outline",
            severity=formula_severity("impossible", *_SELF_INTERSECT_FORMULAS),
            kind=element.kind, target=element.uid,
            detail=f"轮廓自交（{len(_distinct(ring))} 个相异顶点），"
                   f"鞋带面积 {geo.polygon_area(ring):.4f} m² 不可信",
            evidence={
                "points": len(ring),
                "distinct_points": len(_distinct(ring)),
                "area_m2": geo.polygon_area(ring),
                "signed_area_m2": geo.signed_area(ring),
                "hull_area_m2": _hull_area(ring),
            },
            basis=formula_basis(
                "鞋带公式的成立条件里写着「简单多边形」，自交环不满足它 ——"
                "环被交点分成的两瓣绕向相反、贡献等值反号，**因此自交环算出的"
                "面积不可信**（本例 0 只是相消的结果，不是它真实的大小）。"
                "实测存量 3166/10170 根柱（31.1%）、69/514 块板因此混凝土量算成 0。",
                *_SELF_INTERSECT_FORMULAS, base="impossible")))
    return findings


def _hull_area(ring: list) -> float:
    hull = geo.convex_hull(ring)
    return geo.polygon_area(hull) if len(hull) >= 3 else 0.0


# ── geom.zero_area_with_extent ─────────────────────────────────────

def check_zero_area_with_extent(model: PlausibilityModel) -> list[Finding]:
    targets = [e for e in model.elements() if len(e.outline) >= 3]
    if not targets:
        raise RuleNotApplicable("模型里没有带轮廓（outline）的构件")
    findings: list[Finding] = []
    for element in targets:
        ring = element.outline
        rect = geo.min_area_rect(ring)
        # 短边为零 = 真退化（点全共线），那是 `geom.degenerate_outline` 的事。
        # 在这里放过它，两条规则的射程才不重叠，报告里也不会同一件事数两遍。
        if rect is None or rect[1] <= DEGENERATE_SHORT_M:
            continue
        area = geo.polygon_area(ring)
        if area > AREA_EPS_M2:
            continue
        findings.append(Finding(
            rule="geom.zero_area_with_extent",
            severity=formula_severity("impossible", *_ZERO_AREA_FORMULAS),
            kind=element.kind, target=element.uid,
            detail=f"面积算成 {area:.3e} m²，但真实跨度有 "
                   f"{rect[0]:.3f}×{rect[1]:.3f} m",
            evidence={
                "area_m2": area, "long_side_m": rect[0], "short_side_m": rect[1],
                "hull_area_m2": _hull_area(ring), "points": len(ring),
            },
            basis=formula_basis(
                "满足鞋带公式成立条件（简单多边形）的环，两个方向都有跨度时"
                "面积必然为正；面积为零只能是环序坏了（原路回描 A→B→A→C，"
                "正负相消）。跨度用最小面积外接矩形量而不是轴对齐包围盒 ——"
                "斜置构件的包围盒是它的影子，会把短边量大。"
                "同 `ring_order._has_zero_area_with_2d_extent` 判据，无阈值。",
                *_ZERO_AREA_FORMULAS, base="impossible")))
    return findings


# ── geom.degenerate_outline ────────────────────────────────────────

def check_degenerate_outline(model: PlausibilityModel) -> list[Finding]:
    area_like = [e for e in model.elements() if e.raw.get("outline")]
    line_like = [e for e in model.elements()
                 if not e.raw.get("outline") and e.raw.get("path")]
    if not area_like and not line_like:
        raise RuleNotApplicable("模型里的构件既没有轮廓（outline）也没有路径（path）")

    findings: list[Finding] = []
    for element in area_like:
        ring = element.outline
        distinct = _distinct(ring)
        if len(distinct) < 3:
            findings.append(Finding(
                rule="geom.degenerate_outline", severity="impossible",
                kind=element.kind, target=element.uid,
                detail=f"轮廓只有 {len(distinct)} 个相异顶点，构不成多边形",
                evidence={"points": len(ring), "distinct_points": len(distinct)},
                basis="多边形至少需要 3 个不共点的顶点（欧氏平面的定义）。"
                      "点数按位置去重后统计 —— 原始点数含线段端点重复，不可用。"))
            continue
        rect = geo.min_area_rect(ring)
        short = 0.0 if rect is None else rect[1]
        if short <= DEGENERATE_SHORT_M:
            findings.append(Finding(
                rule="geom.degenerate_outline",
                # 这一支的短边量自最小面积外接矩形，所以它的凭据硬不硬
                # 取决于那两条公式；上面「相异顶点不足 3」那一支只用多边形的
                # 定义，不经过公式表，故不随之降级。
                severity=formula_severity("impossible", *_COLLINEAR_FORMULAS),
                kind=element.kind, target=element.uid,
                detail=f"轮廓上 {len(distinct)} 个点全部共线（短边 {short:.2e} m）",
                evidence={"distinct_points": len(distinct), "short_side_m": short,
                          "threshold_m": DEGENERATE_SHORT_M},
                basis=formula_basis(
                    "共线点列的凸包退化成线段，最小面积外接矩形的短边随之为零，"
                    "围不出面积。"
                    f"门槛 {DEGENERATE_SHORT_M} m = 1 毫米，低于图纸与落库坐标的精度。",
                    *_COLLINEAR_FORMULAS, base="impossible")))

    for element in line_like:
        length = element.length_m() or 0.0
        if length <= DEGENERATE_SHORT_M:
            findings.append(Finding(
                rule="geom.degenerate_outline", severity="impossible",
                kind=element.kind, target=element.uid,
                detail=f"路径长度 {length:.2e} m，首尾点重合",
                evidence={"length_m": length, "threshold_m": DEGENERATE_SHORT_M},
                basis="线状构件（墙/梁/管线）的长度由路径两端点决定；"
                      "零长线段加厚后仍然没有占地，不构成构件。"))
    return findings


# ── geom.non_positive_size ─────────────────────────────────────────

def check_non_positive_size(model: PlausibilityModel) -> list[Finding]:
    targets = [(e, f) for e in model.elements() for f in SIZE_FIELDS
               if e.raw.get(f) is not None]
    if not targets:
        raise RuleNotApplicable(
            f"模型里没有任何构件带尺寸字段（{'/'.join(SIZE_FIELDS)}）")
    findings: list[Finding] = []
    for element, field in targets:
        raw_value = element.raw.get(field)
        number = _numeric(raw_value)
        if number is None:
            findings.append(Finding(
                rule="geom.non_positive_size", severity="impossible",
                kind=element.kind, target=element.uid,
                detail=f"{field} 不是数：{raw_value!r}",
                evidence={"field": field, "value": None},
                basis="长度量必须是实数；非数值的尺寸字段无法参与任何几何计算。"))
            continue
        if number > 0:
            continue
        findings.append(Finding(
            rule="geom.non_positive_size", severity="impossible",
            kind=element.kind, target=element.uid,
            detail=f"{field} = {number} m，非正",
            evidence={"field": field, "value": number},
            basis="长度是非负实数，构件的宽/厚/高为零或为负没有对应的实体 ——"
                  "取零则体积恒为零，取负则体积为负，量纲上都不成立。"))
    return findings


# ── geom.absurd_extent ─────────────────────────────────────────────

def check_absurd_extent(model: PlausibilityModel) -> list[Finding]:
    targets = [(e, _extent_m(e)) for e in model.elements()]
    ceilings = _max_extent_m()
    targets = [(e, x) for e, x in targets if x is not None and e.kind in ceilings]
    if not targets:
        raise RuleNotApplicable("没有构件能量出跨度（既无可用轮廓也无路径）")
    findings: list[Finding] = []
    for element, extent in targets:
        ceiling = ceilings[element.kind]
        if extent <= ceiling:
            continue
        findings.append(Finding(
            rule="geom.absurd_extent", severity="implausible",
            kind=element.kind, target=element.uid,
            detail=f"跨度 {extent:.2f} m，超出 {element.kind} 的工程上限 {ceiling} m",
            evidence={"long_side_m": extent, "limit_m": ceiling,
                      "ratio": extent / ceiling},
            basis=formula_basis(
                f"{element.kind} 的单构件尺度上限 {ceiling} m 取自工程量级"
                "（codes.extent.max_by_kind_m，EMPIRICAL，逐条依据见该条 note）。"
                "面状构件的跨度按最小面积外接矩形的长边量，线状按路径长。"
                "这是量级判断不是规范条款，故只出 implausible："
                "超限的通常是图框线、总图轮廓，或坐标变换出错把一张图摊到公里级。",
                *_EXTENT_FORMULAS)))
    return findings


# ── geom.low_solidity ──────────────────────────────────────────────

def check_low_solidity(model: PlausibilityModel) -> list[Finding]:
    targets = [e for e in model.elements() if len(e.outline) >= 3]
    if not targets:
        raise RuleNotApplicable("模型里没有带轮廓（outline）的构件")
    findings: list[Finding] = []
    for element in targets:
        ring = element.outline
        # 自交环的面积本身就不可信，实心度更无从谈起 —— 交给自交规则去报。
        if geo.is_self_intersecting(ring):
            continue
        ratio = geo.solidity(ring)
        if ratio is None or ratio >= MIN_SOLIDITY:
            continue
        findings.append(Finding(
            rule="geom.low_solidity", severity="suspect",
            kind=element.kind, target=element.uid,
            detail=f"实心度 {ratio:.2f} < {MIN_SOLIDITY}，轮廓被撕成异形",
            evidence={"solidity": ratio, "threshold": MIN_SOLIDITY,
                      "area_m2": geo.polygon_area(ring), "hull_area_m2": _hull_area(ring)},
            basis=formula_basis(
                "最凹的标准截面（肢厚 0.3 倍边长的 L 形柱、十字形柱）实心度约 0.51，"
                f"取 {MIN_SOLIDITY} 留余量。低于它的轮廓不对应任何标准截面形式。"
                "自交环的面积本身不可信，已在上游排除，不进这条判据。"
                "这是形态统计不是定理，故只出 suspect。",
                *_SOLIDITY_FORMULAS)))
    return findings


# ── geom.duplicate_element ─────────────────────────────────────────

def _comparable(elements) -> list[tuple]:
    """→ [(x0, 构件, 占地环, 面积, 包围盒)]，按 x0 升序。

    自交环与零面积环一律排除：它们的面积不可信，拿来算重叠比只会造假阳性，
    而且各有专门的规则去报。
    """
    items: list[tuple] = []
    for element in elements:
        ring = element.footprint()
        if len(ring) < 3 or geo.is_self_intersecting(ring):
            continue
        area = geo.polygon_area(ring)
        if area <= AREA_EPS_M2:
            continue
        box = geo.bbox(ring)
        if box is None:
            continue
        items.append((box[0], element, ring, area, box))
    items.sort(key=lambda item: item[0])
    return items


def check_duplicate_element(model: PlausibilityModel) -> list[Finding]:
    # 分组键 = 同一层 × 同一类。柱压在板上是正常的，只有同类重合才叫重复。
    groups: dict[tuple, list[Element]] = {}
    for floor in model.floors():
        for element in floor.elements:
            groups.setdefault(
                (element.building_key, element.floor_key, element.kind), []
            ).append(element)

    prepared = {key: _comparable(items) for key, items in groups.items()}
    if not any(len(items) >= 2 for items in prepared.values()):
        raise RuleNotApplicable("没有任何一层的同一类里有两个及以上可比较的构件")

    findings: list[Finding] = []
    # **一个构件至多报一条**：一簇 k 个近乎重合的构件，两两配对会报 k(k-1)/2 条 ——
    # 实测歌剧院 v85 的 2681 根梁报出 18251 条，比构件本身还多，读报告的人
    # 没法从中知道「有多少构件是多余的」。改成「每个构件只报它遇到的第一个
    # 重复对象」，命中数就等于**冗余构件数**，正是要从算量里剔除的那个数。
    reported: set[str] = set()
    for items in prepared.values():
        for index, (_, element_a, ring_a, area_a, box_a) in enumerate(items):
            # 用下标而不是切片遍历后半段：切片每轮复制一次列表，
            # 单层上千根柱时光复制就是 O(n²)，而这里本来就在省常数。
            for other in range(index + 1, len(items)):
                _, element_b, ring_b, area_b, box_b = items[other]
                # 扫描线：已按 x0 升序，一旦 B 的左边界越过 A 的右边界，
                # 其后所有构件也必然越过 —— 这一 break 是 O(n²) 不退化的关键。
                if box_b[0] > box_a[2]:
                    break
                if not geo.boxes_overlap(box_a, box_b):
                    continue
                if element_b.uid in reported:
                    continue
                overlap = geo.polygon_overlap_area(ring_a, ring_b)
                ratio = overlap / min(area_a, area_b)
                if ratio <= DUPLICATE_OVERLAP_RATIO:
                    continue
                reported.add(element_b.uid)
                findings.append(Finding(
                    rule="geom.duplicate_element", severity="implausible",
                    kind=element_a.kind, target=element_b.uid,
                    detail=f"与 {element_a.uid} 重叠 {ratio:.0%}，同层同类近乎完全重合",
                    evidence={"duplicate_of": element_a.uid, "overlap_ratio": ratio,
                              "overlap_m2": overlap, "area_a_m2": area_a,
                              "area_b_m2": area_b,
                              "threshold": DUPLICATE_OVERLAP_RATIO},
                    basis=formula_basis(
                        "两个实体不能占据同一块平面位置。实测总图与分图画同一区域时"
                        "同一根柱会被落库几遍，算量因此翻倍。"
                        "Sutherland–Hodgman 只对**凸**裁剪多边形成立，而构件轮廓偶有"
                        "凹形，实现里先取凸包再裁 —— **按凸包裁剪会高估重叠**，"
                        f"故阈值定在明显的量级（{DUPLICATE_OVERLAP_RATIO:.0%}，"
                        "意味着两者几乎是同一个形状），不拿它做精确面积。",
                        *_DUPLICATE_FORMULAS)))
    return findings


# ── 注册 ────────────────────────────────────────────────────────────

RULES: dict[str, Rule] = {
    rule.id: rule for rule in (
        register(Rule(
            id="geom.self_intersecting_outline", title="轮廓自交（蝴蝶结）",
            scope="element", severity="impossible",
            basis="鞋带公式只对简单多边形成立；自交环两瓣等值反号相消（"
                  + formula_ref(*_SELF_INTERSECT_FORMULAS) + "）",
            check=check_self_intersecting)),
        register(Rule(
            id="geom.zero_area_with_extent", title="面积为零却有两向跨度",
            scope="element", severity="impossible",
            basis="简单多边形两向有跨度则面积必为正（"
                  + formula_ref(*_ZERO_AREA_FORMULAS) + "）",
            check=check_zero_area_with_extent)),
        register(Rule(
            id="geom.degenerate_outline", title="轮廓退化（点数不足或全共线）",
            scope="element", severity="impossible",
            basis="多边形至少需要 3 个不共线的相异顶点；共线判据取自最小面积"
                  "外接矩形的短边（" + formula_ref(*_COLLINEAR_FORMULAS) + "）",
            check=check_degenerate_outline)),
        register(Rule(
            id="geom.non_positive_size", title="宽/厚/高非正",
            scope="element", severity="impossible",
            basis="长度是非负实数，零或负的尺寸没有对应实体",
            check=check_non_positive_size)),
        register(Rule(
            id="geom.absurd_extent", title="单构件尺度超出工程量级",
            scope="element", severity="implausible",
            basis="按类的工程尺度上限（codes.extent.max_by_kind_m，量级分析）；"
                  "跨度口径 " + formula_ref(*_EXTENT_FORMULAS),
            check=check_absurd_extent)),
        register(Rule(
            id="geom.low_solidity", title="实心度过低（轮廓被撕成异形）",
            scope="element", severity="suspect",
            basis="最凹的标准截面（L 形/十字形柱）实心度约 0.51（"
                  + formula_ref(*_SOLIDITY_FORMULAS) + "）",
            check=check_low_solidity)),
        register(Rule(
            id="geom.duplicate_element", title="同层同类构件近乎完全重合",
            scope="element", severity="implausible",
            basis="两个实体不能占据同一块平面位置；实测总图与分图重复落库。"
                  "重叠面积按凸包裁剪会偏大（" + formula_ref(*_DUPLICATE_FORMULAS) + "）",
            check=check_duplicate_element)),
    )
}
