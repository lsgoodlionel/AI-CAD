"""重力与支承族：**这栋楼站得住吗**。

每个构件的重力都必须有一条连续的传力路径通往基础 —— 这是静力平衡的直接
推论，不需要任何规范背书。模型里最常见的荒谬正好落在这里：构件悬在空中
（下层没建模、图纸配准错位）、两个实体占同一块楼面、某张离群图把整个场景
拉到几公里外。

**阈值都是几何/统计判据，不是规范限值**，所以本族不引用 `codes.py`
（那张表当前全是待取证占位，而按契约 `UNVERIFIED` 的限值不得用来出
`impossible`/`implausible` 结论）。每个常量的依据写在它自己旁边。

**为什么很多判据故意偏宽松**：本族最重的一档是 `impossible`，误判的代价是
把真构件判成不可能存在。凡是拿不准的方向，一律选「更难报出来」的那一侧 ——
支承重叠只要 5% 就算数、梁一端有支座就算数、互穿只查白名单里的几对。
"""
from __future__ import annotations

import math
import statistics
from collections import defaultdict

from core.model3d.plausibility import geometry as geo
from core.model3d.plausibility.model import ELEMENT_KINDS, Element, Floor
from core.model3d.plausibility.registry import register
# 公式出处的拼装与降级统一在 rules_quantity（`degrade`/`basis_of` 的老家）。
from core.model3d.plausibility.rules_quantity import (
    formula_basis, formula_ref, formula_severity,
)
from core.model3d.plausibility.types import Finding, Rule, RuleNotApplicable

Ring = list
Box = tuple

# ── 各规则回指的公式（`formulas.py` 的 key）──────────────────────────
#
# 只写 key，渲染留到 `check` 里：出处正由取证任务逐条填，在这里拼成字符串
# 等于快照，后来填好的原文永远到不了报告。

#: 柱悬空：传力路径来自静力平衡；「下方有没有东西托住」用重叠面积量。
_FLOATING_FORMULAS = ("mechanics.static_equilibrium",
                      "geometry.polygon_clipping_convex_requirement")
#: 梁的支承（两端皆无 / 只有一端）：同为静力平衡；找支承用射线法判点在轮廓内。
_BEAM_FORMULAS = ("mechanics.static_equilibrium",
                  "geometry.jordan_curve_ray_casting")
#: 板整圈无支承：同上。
_SLAB_FORMULAS = _BEAM_FORMULAS
#: 互穿：重叠面积用 Sutherland–Hodgman 裁剪算，面积仍是鞋带。
_INTERPENETRATION_FORMULAS = ("geometry.polygon_clipping_convex_requirement",
                              "geometry.shoelace_area")
#: 离群：构件位置取多边形形心。
_ISOLATION_FORMULAS = ("geometry.polygon_centroid",)

# ── 阈值 ──────────────────────────────────────────────────────────────

#: 柱算「有支承」所需的最小重叠比例（占柱自身占地面积）。
#: 依据：一道 200mm 的墙托住一根 800mm 的柱，重叠也有 25%；取 5% 是给上下层
#: 配准误差留的量 —— 宁可漏报，不可把真柱判成不可能。
COLUMN_SUPPORT_MIN_RATIO = 0.05

#: 整层「悬空柱」占比超过此值 → 判为转换层嫌疑，降级为 suspect。
TRANSFER_FLOOR_RATIO = 0.5

#: 转换层判定的最小样本。柱少于这个数时「比例」没有统计意义
#: （2 根里 2 根悬空不能叫「整层不连续」）。
TRANSFER_FLOOR_MIN_COLUMNS = 4

#: 梁端 / 板边找支承的搜索半径（米）。
#: 依据：常见柱截面 400~1000mm，梁端搭在柱内；1.0m 覆盖「梁端点落在柱轮廓
#: 之外半个柱宽」的建模对位误差。EMPIRICAL，非规范限值。
SUPPORT_SEARCH_RADIUS_M = 1.0

#: 板边界采样：沿周长每 2 米一个点，总数钳在 [8, 64]。
#: 上限是性能护栏 —— 兜底板可能是整张图框那么大的轮廓。
SLAB_EDGE_SAMPLE_STEP_M = 2.0
SLAB_EDGE_SAMPLE_MIN = 8
SLAB_EDGE_SAMPLE_MAX = 64

#: 互穿判定：重叠面积占**较小者**的比例超过此值即判互穿。
#: 不取 0.9：识别噪声让两个本应重合的轮廓很少严丝合缝；0.6 已是「大部分重合」。
INTERPENETRATION_MIN_RATIO = 0.6

#: **白名单而非黑名单**：平面上跨类重叠绝大多数是正常的（柱穿板、梁压墙、
#: 管线绕柱都在竖向错开）。只有「都自楼面起算、都占满层高」的两类才互斥。
#: 同类重叠归 `geom.duplicate_element` 管，本族不碰。
INTERPENETRATION_PAIRS = (("columns", "equipment"), ("walls", "equipment"))

#: 离群判据。同层构件最近邻距离的中位数是柱网量级（米级）；离群项要同时
#: 超过「中位数的 10 倍」和「50 米」两道线。
#: 50 米下限的出处是实测教训：曾有 2 张离群图把场景包络从 760 米撑到 4.8 公里。
ISOLATION_FACTOR = 10.0
ISOLATION_FLOOR_MIN_M = 50.0
#: 分桶边长。常见柱网跨度 6~9 米，20 米已覆盖两三跨 —— 正常构件必在
#: 3×3 桶内找得到邻居，找不到的才进精确复核。
ISOLATION_CELL_M = 20.0
#: 整层构件少于这个数，「主群」无从谈起。
ISOLATION_MIN_SAMPLE = 8
#: 候选（桶内无邻居）占比超过一半时，说明这一层本就稀疏（如场地桩位图），
#: 中位数不可信，整层跳过。
ISOLATION_MAX_CANDIDATE_SHARE = 0.5

#: 标高比较容差（米）。人工录入的标高精确到厘米。
ELEVATION_TOLERANCE_M = 0.01

#: 粗筛分桶边长（米）与单元素/单查询的最大占用桶数（超了就退化为全扫，
#: 免得一块整层大板把桶炸开）。
_GRID_CELL_M = 4.0
_MAX_CELLS = 4096


# ── 粗筛索引 ──────────────────────────────────────────────────────────
def _cells_of(box: Box, cell: float) -> list[tuple[int, int]] | None:
    """包围盒覆盖的桶坐标。超过 `_MAX_CELLS` 返回 None（调用方退化为全扫）。"""
    x0, y0, x1, y1 = box
    i0, i1 = int(math.floor(x0 / cell)), int(math.floor(x1 / cell))
    j0, j1 = int(math.floor(y0 / cell)), int(math.floor(y1 / cell))
    if (i1 - i0 + 1) * (j1 - j0 + 1) > _MAX_CELLS:
        return None
    return [(i, j) for i in range(i0, i1 + 1) for j in range(j0, j1 + 1)]


class _Grid:
    """构件占地的包围盒分桶索引。

    单层构件上千时两两比较是 10⁶ 量级，纯 Python 扛不住；所有「附近有没有」
    的查询都先过它，再做精确相交。
    """

    def __init__(self, entries: list[tuple[Element, Ring, Box]],
                 cell_m: float = _GRID_CELL_M) -> None:
        self.cell = cell_m
        self.entries = entries
        self._buckets: dict[tuple[int, int], list[int]] = defaultdict(list)
        self._oversized: list[int] = []          # 桶太多的大构件，每次查询都带上
        for index, (_el, _ring, box) in enumerate(entries):
            cells = _cells_of(box, cell_m)
            if cells is None:
                self._oversized.append(index)
                continue
            for key in cells:
                self._buckets[key].append(index)

    def query(self, box: Box) -> list[int]:
        cells = _cells_of(box, self.cell)
        if cells is None:
            return list(range(len(self.entries)))
        seen: set[int] = set()
        for key in cells:
            seen.update(self._buckets.get(key, ()))
        seen.update(self._oversized)
        return sorted(seen)


def _entries_of(floor: Floor, kinds: tuple[str, ...]) -> list[tuple[Element, Ring, Box]]:
    """取某几类构件的（构件, 占地环, 包围盒）。没有占地的直接不进来。"""
    out: list[tuple[Element, Ring, Box]] = []
    for element in floor.elements:
        if element.kind not in kinds:
            continue
        ring = element.footprint()
        if len(ring) < 3:
            continue
        box = geo.bbox(ring)
        if box is not None:
            out.append((element, ring, box))
    return out


def _sorted_floors(building) -> list[Floor]:
    """楼层自下而上。key 参与排序，免得 order 相同时顺序随输入漂移。"""
    return sorted(building.floors, key=lambda f: (f.order, str(f.key)))


def _floor_target(floor: Floor) -> str:
    return f"{floor.building_key}/{floor.key}"


def _point_ring_distance(point, ring: Ring) -> float:
    """点到多边形的距离，落在内部记 0。"""
    if geo.point_in_polygon(point, ring):
        return 0.0
    px, py = float(point[0]), float(point[1])
    best = float("inf")
    for (x0, y0), (x1, y1) in zip(ring, list(ring[1:]) + list(ring[:1])):
        dx, dy = x1 - x0, y1 - y0
        span = dx * dx + dy * dy
        t = 0.0 if span <= 0 else max(0.0, min(1.0, ((px - x0) * dx + (py - y0) * dy) / span))
        best = min(best, math.hypot(px - (x0 + t * dx), py - (y0 + t * dy)))
    return best


def _has_support_near(point, grid: _Grid, radius: float) -> bool:
    px, py = float(point[0]), float(point[1])
    probe = (px - radius, py - radius, px + radius, py + radius)
    for index in grid.query(probe):
        _el, ring, box = grid.entries[index]
        if not geo.boxes_overlap(probe, box):
            continue
        if _point_ring_distance((px, py), ring) <= radius:
            return True
    return False


def _support_overlap(ring: Ring, box: Box, area: float, grid: _Grid) -> float:
    """被下方构件覆盖的占地面积（累加，封顶到自身面积）。

    累加而非取最大：一根柱可能同时骑在两道墙上，各覆盖一部分。多算一点
    会让结论更难报出来 —— 对 `impossible` 档而言，这个方向是对的。
    """
    total = 0.0
    for index in grid.query(box):
        _el, other, other_box = grid.entries[index]
        if not geo.boxes_overlap(box, other_box):
            continue
        total += geo.polygon_overlap_area(ring, other)
        if total >= area:
            return area
    return min(total, area)


# ── support.floating_column ───────────────────────────────────────────
def _floating_basis() -> str:
    """柱悬空的依据。**运行时拼**，出处才跟得上取证进度。"""
    return formula_basis(
        "重力荷载必须有连续传递路径直至基础，这是 ΣF=0 的直接推论。柱脚正下方"
        "没有任何竖向构件（柱/墙）与之重叠，即无传力路径。"
        f"重叠比例阈值 {COLUMN_SUPPORT_MIN_RATIO:.0%}（占柱自身占地）为上下层"
        "配准误差留量。重叠面积按凸包裁剪（Sutherland–Hodgman 只对凸裁剪多边形"
        "成立）会**高估**覆盖 —— 方向是「更难报出来」，对 impossible 这一档正合适。",
        *_FLOATING_FORMULAS, base="impossible")
_TRANSFER_BASIS = (
    "统计判据（非条款）：转换层（梁式/桁架转换）本就允许竖向构件不连续。"
    f"当一层里超过 {TRANSFER_FLOOR_RATIO:.0%} 的柱都对不上下层时，"
    "「这是转换层，或下层根本没建模」比「几百根柱同时悬空」更可能，"
    "故不出 impossible，降为 suspect 交人判。"
)


def _check_floating_column(model) -> list[Finding]:
    pairs: list[tuple[Floor, Floor]] = []
    for building in model.buildings:
        floors = _sorted_floors(building)
        pairs.extend((upper, lower) for lower, upper in zip(floors, floors[1:]))
    upper_columns = sum(len(upper.of_kind("columns")) for upper, _ in pairs)
    if not pairs:
        raise RuleNotApplicable("每栋楼都只有一层 —— 没有「下一层」可查支承")
    if upper_columns == 0:
        raise RuleNotApplicable("非底层楼层里一根柱也没有 —— 无从判断支承")

    findings: list[Finding] = []
    for upper, lower in pairs:
        support = _Grid(_entries_of(lower, ("columns", "walls")))
        columns = _entries_of(upper, ("columns",))
        floating: list[tuple[Element, float, float]] = []
        for element, ring, box in columns:
            area = geo.polygon_area(ring)
            if area <= 0:
                continue                      # 退化轮廓归 geom 族，这里不抢
            covered = _support_overlap(ring, box, area, support)
            ratio = covered / area
            if ratio < COLUMN_SUPPORT_MIN_RATIO:
                floating.append((element, area, ratio))
        if not floating:
            continue
        share = len(floating) / len(columns)
        if len(columns) >= TRANSFER_FLOOR_MIN_COLUMNS and share >= TRANSFER_FLOOR_RATIO:
            findings.append(Finding(
                rule="support.floating_column", severity="suspect", kind="floor",
                target=_floor_target(upper),
                detail=(f"{upper.label} 层 {len(floating)}/{len(columns)} 根柱在下层"
                        f"（{lower.label}）找不到支承 —— 整层大范围如此，更可能是"
                        f"转换层或下层未建模，不逐根判为不可能"),
                evidence={"floating": len(floating), "columns": len(columns),
                          "share": round(share, 3),
                          "share_threshold": TRANSFER_FLOOR_RATIO,
                          "lower_floor": _floor_target(lower)},
                basis=_TRANSFER_BASIS))
            continue
        for element, area, ratio in floating:
            findings.append(Finding(
                rule="support.floating_column",
                severity=formula_severity("impossible", *_FLOATING_FORMULAS),
                kind="columns",
                target=element.uid,
                detail=(f"{upper.label} 层这根柱的正下方（{lower.label} 层）"
                        f"没有柱也没有墙 —— 荷载无处可去"),
                evidence={"support_overlap_ratio": round(ratio, 3),
                          "min_ratio": COLUMN_SUPPORT_MIN_RATIO,
                          "footprint_m2": round(area, 3),
                          "lower_floor": _floor_target(lower),
                          "lower_candidates": len(support.entries)},
                basis=_floating_basis()))
    return findings


# ── support.beam_without_support ──────────────────────────────────────
def _beam_basis() -> str:
    return formula_basis(
        "受弯构件至少要有一个支座才谈得上传力（ΣF=0）。一端悬挑合法，"
        "**两端都**在柱/墙的搜索半径内找不到东西，则这根梁没有任何传力路径。"
        f"半径 {SUPPORT_SEARCH_RADIUS_M} m 取常见柱截面 0.4~1.0 m 的量级"
        "（EMPIRICAL，非规范限值）；「梁端是否落在支承轮廓内」用射线法判。"
        "支承候选只算柱与墙，不算梁：两根都悬空的梁会互相「支承」，"
        "把彼此的错误洗白。",
        *_BEAM_FORMULAS)


def _check_beam_without_support(model) -> list[Finding]:
    checked = 0
    findings: list[Finding] = []
    for floor in model.floors():
        beams = [e for e in floor.of_kind("beams") if len(e.path) >= 2]
        if not beams:
            continue
        support = _Grid(_entries_of(floor, ("columns", "walls")))
        for beam in beams:
            checked += 1
            path = beam.path
            ends = (path[0], path[-1])
            if any(_has_support_near(p, support, SUPPORT_SEARCH_RADIUS_M) for p in ends):
                continue
            findings.append(Finding(
                rule="support.beam_without_support", severity="implausible", kind="beams",
                target=beam.uid,
                detail=(f"梁两端 {SUPPORT_SEARCH_RADIUS_M} m 内都没有柱或墙 —— "
                        f"既非简支也非悬挑，荷载无处传递"),
                evidence={"x0": round(ends[0][0], 3), "y0": round(ends[0][1], 3),
                          "x1": round(ends[1][0], 3), "y1": round(ends[1][1], 3),
                          "length_m": round(beam.length_m() or 0.0, 3),
                          "search_radius_m": SUPPORT_SEARCH_RADIUS_M,
                          "supports_on_floor": len(support.entries)},
                basis=_beam_basis()))
    if checked == 0:
        raise RuleNotApplicable("模型里没有带两端坐标（path）的梁")
    return findings


# ── support.beam_support_count ────────────────────────────────────────
#
# 与上一条的分工：`beam_without_support` 打的是「两端都没有」，为悬挑梁留了
# 余地；本条补的是「**恰好只有一端有**」这一档。两条都从 ΣF=0 且 ΣM=0 出发，
# 但结论的硬度差很远 —— 一端有支座的梁可能是合法的悬挑，也可能是另一端的
# 支座根本没建模，而平面图上看不出有没有嵌固。判不出就说判不出：`suspect`。


def _beam_support_count_basis() -> str:
    return formula_basis(
        "静定性：一根平面受弯构件有三个自由度，要 ΣF=0 且 ΣM=0 同时成立，"
        "简支梁**至少需要两个支座**；只有一个支座且无嵌固时它是机构，不是结构。"
        "但反过来不成立 —— 悬挑梁靠嵌固端提供的力矩约束就能平衡，"
        "而嵌固在平面图上看不出来。故本条只出 suspect，不否定构件。"
        f"两端各按 {SUPPORT_SEARCH_RADIUS_M} m 半径找柱/墙，"
        "判据与 support.beam_without_support 同一套（**复用同一个搜索**，"
        "两条规则的口径分开写必然漂移）。",
        *_BEAM_FORMULAS)


def _check_beam_support_count(model) -> list[Finding]:
    checked = 0
    findings: list[Finding] = []
    for floor in model.floors():
        beams = [e for e in floor.of_kind("beams") if len(e.path) >= 2]
        if not beams:
            continue
        support = _Grid(_entries_of(floor, ("columns", "walls")))
        for beam in beams:
            checked += 1
            path = beam.path
            ends = (path[0], path[-1])
            supported = [_has_support_near(p, support, SUPPORT_SEARCH_RADIUS_M)
                         for p in ends]
            # 两端皆无 → 归 support.beam_without_support，本条不抢；
            # 两端皆有 → 静定，正常。只剩「恰好一端」这一档。
            if sum(supported) != 1:
                continue
            free = 1 if supported[0] else 0
            findings.append(Finding(
                rule="support.beam_support_count", severity="suspect", kind="beams",
                target=beam.uid,
                detail=(f"梁只有一端找得到支座（{'终' if free else '起'}端 "
                        f"{SUPPORT_SEARCH_RADIUS_M} m 内无柱无墙）—— "
                        f"可能是悬挑（嵌固端在平面图上看不出来），"
                        f"也可能是缺支座，需人工判"),
                evidence={"supported_ends": 1, "required_ends": 2,
                          "free_end_x": round(ends[free][0], 3),
                          "free_end_y": round(ends[free][1], 3),
                          "length_m": round(beam.length_m() or 0.0, 3),
                          "search_radius_m": SUPPORT_SEARCH_RADIUS_M,
                          "supports_on_floor": len(support.entries)},
                basis=_beam_support_count_basis()))
    if checked == 0:
        raise RuleNotApplicable("模型里没有带两端坐标（path）的梁")
    return findings


# ── support.slab_without_edge_support ─────────────────────────────────
def _slab_basis() -> str:
    return formula_basis(
        "板是受弯构件，荷载经边界传给梁/墙/柱（ΣF=0）。沿轮廓等弧长取样，"
        f"**每一个**采样点 {SUPPORT_SEARCH_RADIUS_M} m 内都没有支承构件，"
        "则这块板整圈悬空；采样点是否落在支承轮廓内用射线法判。"
        "只要有一点落在支承上就不报 —— 判据刻意偏宽。",
        *_SLAB_FORMULAS)


def _sample_ring(ring: Ring) -> list[tuple[float, float]]:
    """沿轮廓等弧长取样。点数 = 周长/步长，钳在 [MIN, MAX]。"""
    closed = list(ring) + [ring[0]]
    segments = []
    perimeter = 0.0
    for a, b in zip(closed, closed[1:]):
        length = math.hypot(b[0] - a[0], b[1] - a[1])
        if length > 0:
            segments.append((a, b, length))
            perimeter += length
    if not segments:
        return []
    count = int(perimeter / SLAB_EDGE_SAMPLE_STEP_M)
    count = max(SLAB_EDGE_SAMPLE_MIN, min(SLAB_EDGE_SAMPLE_MAX, count))
    points: list[tuple[float, float]] = []
    step = perimeter / count
    index, walked = 0, 0.0
    for k in range(count):
        target = k * step
        while index < len(segments) - 1 and walked + segments[index][2] < target:
            walked += segments[index][2]
            index += 1
        (ax, ay), (bx, by), length = segments[index]
        t = 0.0 if length <= 0 else max(0.0, min(1.0, (target - walked) / length))
        points.append((ax + (bx - ax) * t, ay + (by - ay) * t))
    return points


def _check_slab_without_edge_support(model) -> list[Finding]:
    checked = 0
    findings: list[Finding] = []
    for floor in model.floors():
        slabs = [e for e in floor.of_kind("slabs") if len(e.outline) >= 3]
        if not slabs:
            continue
        support = _Grid(_entries_of(floor, ("beams", "walls", "columns")))
        for slab in slabs:
            samples = _sample_ring(slab.outline)
            if not samples:
                continue
            checked += 1
            supported = sum(1 for p in samples
                            if _has_support_near(p, support, SUPPORT_SEARCH_RADIUS_M))
            if supported:
                continue
            evidence = {"samples": len(samples), "supported_samples": 0,
                        "search_radius_m": SUPPORT_SEARCH_RADIUS_M,
                        "area_m2": round(slab.area_m2() or 0.0, 3),
                        "supports_on_floor": len(support.entries)}
            if slab.fallback_basis:
                # 兜底板（图框被当成楼板）正是「整圈没有支承」的典型，
                # 把来路写进证据，省得复核时再去翻场景。
                evidence["fallback_basis"] = slab.fallback_basis
            findings.append(Finding(
                rule="support.slab_without_edge_support", severity="implausible",
                kind="slabs", target=slab.uid,
                detail=(f"板轮廓上 {len(samples)} 个采样点附近都没有梁/墙/柱 —— "
                        f"整圈边界无支承"),
                evidence=evidence, basis=_slab_basis()))
    if checked == 0:
        raise RuleNotApplicable("模型里没有带闭合轮廓的板")
    return findings


# ── support.interpenetration ──────────────────────────────────────────
def _interpenetration_basis() -> str:
    return formula_basis(
        "固体不可互穿（物理，公式表里暂无对应条目，见模块说明）。只查竖向确实"
        "同占一段高度的两类："
        + "、".join(f"{a}×{b}" for a, b in INTERPENETRATION_PAIRS)
        + f"；占地重叠超过较小者 {INTERPENETRATION_MIN_RATIO:.0%} 即同一块楼面"
        "被两个实体占满。其余跨类组合（柱穿板、梁压墙、管线绕柱）在竖向错开，"
        "属正常，故用白名单而非黑名单。同类重复归 geom.duplicate_element。"
        "重叠面积先取凸包再裁剪，**按凸包裁剪会高估重叠**，"
        f"故阈值定在明显的量级（{INTERPENETRATION_MIN_RATIO:.0%}「大部分重合」），"
        "不拿它做精确面积。",
        *_INTERPENETRATION_FORMULAS)


def _check_interpenetration(model) -> list[Finding]:
    checked = 0
    findings: list[Finding] = []
    for floor in model.floors():
        for kind_a, kind_b in INTERPENETRATION_PAIRS:
            left = _entries_of(floor, (kind_a,))
            right = _entries_of(floor, (kind_b,))
            if not left or not right:
                continue
            grid = _Grid(right)
            for element, ring, box in left:
                area_a = geo.polygon_area(ring)
                if area_a <= 0:
                    continue
                checked += 1
                for index in grid.query(box):
                    other, other_ring, other_box = grid.entries[index]
                    if not geo.boxes_overlap(box, other_box):
                        continue
                    area_b = geo.polygon_area(other_ring)
                    smaller = min(area_a, area_b)
                    if smaller <= 0:
                        continue
                    overlap = geo.polygon_overlap_area(ring, other_ring)
                    ratio = overlap / smaller
                    if ratio <= INTERPENETRATION_MIN_RATIO:
                        continue
                    findings.append(Finding(
                        rule="support.interpenetration", severity="implausible",
                        kind=kind_a, target=element.uid,
                        detail=(f"{kind_a} 与 {kind_b}（{other.uid}）在同一层占同一块"
                                f"楼面，重叠占较小者 {ratio:.0%} —— 两个实体互穿"),
                        evidence={"pair": [element.uid, other.uid],
                                  "overlap_m2": round(overlap, 4),
                                  "area_a_m2": round(area_a, 4),
                                  "area_b_m2": round(area_b, 4),
                                  "overlap_ratio": round(ratio, 3),
                                  "min_ratio": INTERPENETRATION_MIN_RATIO},
                        basis=_interpenetration_basis()))
    if checked == 0:
        raise RuleNotApplicable(
            "没有一层同时具备白名单里的两类构件（"
            + "、".join(f"{a}×{b}" for a, b in INTERPENETRATION_PAIRS) + "）")
    return findings


# ── support.isolated_element ──────────────────────────────────────────
def _isolation_basis() -> str:
    return formula_basis(
        "统计离群，分布取自本模型内部（不是外部标准）：同层构件最近邻距离的中位数"
        f"是柱网量级，离群项要同时超过「中位数 × {ISOLATION_FACTOR:g}」和 "
        f"{ISOLATION_FLOOR_MIN_M:g} m 两道线。下限的出处是实测教训 —— "
        "曾有 2 张离群图把场景包络从 760 m 撑到 4.8 km。"
        "构件位置取面积加权形心（不是顶点平均），否则顶点密的一侧会把位置拉偏。",
        *_ISOLATION_FORMULAS)


def _center(ring: Ring, box: Box) -> tuple[float, float]:
    point = geo.centroid(ring)
    return point if point is not None else ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)


def _nearest_in_cells(points, buckets, index: int) -> float | None:
    """3×3 桶内的最近邻距离。桶内没有别的构件返回 None（转精确复核）。"""
    x, y = points[index]
    cell = ISOLATION_CELL_M
    i0, j0 = int(math.floor(x / cell)), int(math.floor(y / cell))
    best: float | None = None
    for i in range(i0 - 1, i0 + 2):
        for j in range(j0 - 1, j0 + 2):
            for other in buckets.get((i, j), ()):
                if other == index:
                    continue
                distance = math.hypot(points[other][0] - x, points[other][1] - y)
                if best is None or distance < best:
                    best = distance
    return best


def _check_isolated_element(model) -> list[Finding]:
    usable_floors = 0
    findings: list[Finding] = []
    for floor in model.floors():
        entries = _entries_of(floor, ELEMENT_KINDS)
        if len(entries) < ISOLATION_MIN_SAMPLE:
            continue
        usable_floors += 1
        points = [_center(ring, box) for _el, ring, box in entries]
        buckets: dict[tuple[int, int], list[int]] = defaultdict(list)
        for index, (x, y) in enumerate(points):
            buckets[(int(math.floor(x / ISOLATION_CELL_M)),
                     int(math.floor(y / ISOLATION_CELL_M)))].append(index)

        # 第一遍：桶内粗筛。有邻居的给出（足够准的）最近邻距离，没邻居的进候选。
        near: list[float] = []
        candidates: list[int] = []
        for index in range(len(points)):
            distance = _nearest_in_cells(points, buckets, index)
            if distance is None:
                candidates.append(index)
            else:
                near.append(distance)
        if not candidates:
            continue
        if len(candidates) > len(points) * ISOLATION_MAX_CANDIDATE_SHARE:
            continue        # 整层本就稀疏（如场地桩位图），中位数不可信
        threshold = ISOLATION_FLOOR_MIN_M
        median = statistics.median(near) if len(near) >= 4 else None
        if median is not None:
            threshold = max(threshold, ISOLATION_FACTOR * median)

        # 第二遍：只对候选做精确最近邻（候选很少，O(k·n) 可接受）。
        for index in candidates:
            x, y = points[index]
            distance = min((math.hypot(px - x, py - y)
                            for k, (px, py) in enumerate(points) if k != index),
                           default=None)
            if distance is None or distance <= threshold:
                continue
            element = entries[index][0]
            findings.append(Finding(
                rule="support.isolated_element", severity="suspect", kind=element.kind,
                target=element.uid,
                detail=(f"{floor.label} 层这个构件离最近的同层构件 {distance:.1f} m，"
                        f"远超本层的正常间距 —— 多半来自一张没配准好的离群图，"
                        f"它会把整个场景的包围盒撑大"),
                evidence={"nearest_neighbor_m": round(distance, 2),
                          "threshold_m": round(threshold, 2),
                          "median_spacing_m": round(median, 2) if median else 0.0,
                          "factor": ISOLATION_FACTOR,
                          "floor_elements": len(points),
                          "x": round(x, 2), "y": round(y, 2)},
                basis=_isolation_basis()))
    if usable_floors == 0:
        raise RuleNotApplicable(
            f"没有一层的构件数达到 {ISOLATION_MIN_SAMPLE} —— 「主群」无从谈起，"
            f"分布判据没有统计意义")
    return findings


# ── support.story_z_overlap ───────────────────────────────────────────
_STORY_Z_BASIS = (
    "几何：楼层占据的 z 区间 [标高, 标高+层高) 互不重叠，且楼层序号与标高"
    f"同向单调；否则同一段高度被两层同时占用。容差 {ELEVATION_TOLERANCE_M} m"
    "（人工录入标高的精度）。"
)


def _check_story_z_overlap(model) -> list[Finding]:
    usable = 0
    unusable = 0
    findings: list[Finding] = []
    for building in model.buildings:
        floors = _sorted_floors(building)
        for lower, upper in zip(floors, floors[1:]):
            if lower.elevation_m is None or upper.elevation_m is None:
                unusable += 1
                continue
            if lower.elevation_estimated or upper.elevation_estimated:
                # 估出来的标高不能拿去否定楼层 —— 那等于用自己的猜测判自己有罪
                unusable += 1
                continue
            usable += 1
            delta = upper.elevation_m - lower.elevation_m
            if delta <= ELEVATION_TOLERANCE_M:
                findings.append(Finding(
                    rule="support.story_z_overlap", severity="impossible", kind="floor",
                    target=_floor_target(upper),
                    detail=(f"{upper.label}（序号 {upper.order}）的标高 "
                            f"{upper.elevation_m:.3f} m 不高于下层 {lower.label} 的 "
                            f"{lower.elevation_m:.3f} m —— 层序与标高不单调"),
                    evidence={"elevation_m": round(upper.elevation_m, 3),
                              "lower_elevation_m": round(lower.elevation_m, 3),
                              "elevation_delta_m": round(delta, 3),
                              "order": upper.order, "lower_order": lower.order,
                              "tolerance_m": ELEVATION_TOLERANCE_M},
                    basis=_STORY_Z_BASIS))
                continue
            if lower.height_m is None:
                continue
            overlap = (lower.elevation_m + lower.height_m) - upper.elevation_m
            if overlap > ELEVATION_TOLERANCE_M:
                findings.append(Finding(
                    rule="support.story_z_overlap", severity="impossible", kind="floor",
                    target=_floor_target(lower),
                    detail=(f"{lower.label} 的顶标高 "
                            f"{lower.elevation_m + lower.height_m:.3f} m 高过 "
                            f"{upper.label} 的底标高 {upper.elevation_m:.3f} m —— "
                            f"{overlap:.3f} m 的高度被两层同时占用"),
                    evidence={"overlap_m": round(overlap, 3),
                              "elevation_m": round(lower.elevation_m, 3),
                              "height_m": round(lower.height_m, 3),
                              "upper_elevation_m": round(upper.elevation_m, 3),
                              "tolerance_m": ELEVATION_TOLERANCE_M},
                    basis=_STORY_Z_BASIS))
    if usable == 0:
        raise RuleNotApplicable(
            f"没有一对相邻楼层拿得到实测标高（缺失或标高是估的：{unusable} 对）"
            f" —— 估出来的标高不能用来否定楼层")
    return findings


# ── 注册 ──────────────────────────────────────────────────────────────
# **规则级 basis 只回指 key**：`Rule` 在 import 时构造，此刻渲染出处等于快照，
# 取证任务后来填的原文就到不了这里。渲染留在各 `check` 里。
RULE_FLOATING_COLUMN = register(Rule(
    id="support.floating_column", title="柱悬空（下层无竖向支承）",
    scope="element", severity="impossible",
    basis="重力必须有连续传力路径直至基础；"
          + formula_ref(*_FLOATING_FORMULAS),
    check=_check_floating_column))

RULE_BEAM_WITHOUT_SUPPORT = register(Rule(
    id="support.beam_without_support", title="梁两端皆无支座",
    scope="element", severity="implausible",
    basis="受弯构件至少要有一个支座；" + formula_ref(*_BEAM_FORMULAS),
    check=_check_beam_without_support))

RULE_BEAM_SUPPORT_COUNT = register(Rule(
    id="support.beam_support_count", title="梁只有一端有支座（静定性存疑）",
    scope="element", severity="suspect",
    basis="简支梁至少需要两个支座（ΣF=0 且 ΣM=0）；悬挑梁与嵌固端是例外，"
          "故只存疑不否定。" + formula_ref(*_BEAM_FORMULAS),
    check=_check_beam_support_count))

RULE_SLAB_WITHOUT_EDGE_SUPPORT = register(Rule(
    id="support.slab_without_edge_support", title="板整圈边界无支承",
    scope="element", severity="implausible",
    basis="板的荷载经边界传出；" + formula_ref(*_SLAB_FORMULAS),
    check=_check_slab_without_edge_support))

RULE_INTERPENETRATION = register(Rule(
    id="support.interpenetration", title="跨类构件互穿（同占一块楼面）",
    scope="element", severity="implausible",
    basis="固体不可互穿；重叠面积按凸包裁剪会偏大（"
          + formula_ref(*_INTERPENETRATION_FORMULAS) + "）",
    check=_check_interpenetration))

RULE_ISOLATED_ELEMENT = register(Rule(
    id="support.isolated_element", title="构件离群（撑大场景包络）",
    scope="element", severity="suspect",
    basis="统计离群，分布取自本模型内部；位置取形心（"
          + formula_ref(*_ISOLATION_FORMULAS) + "）",
    check=_check_isolated_element))

RULE_STORY_Z_OVERLAP = register(Rule(
    id="support.story_z_overlap", title="相邻楼层 z 区间交叠或层序标高不单调",
    scope="floor", severity="impossible", basis=_STORY_Z_BASIS,
    check=_check_story_z_overlap))

RULES = (RULE_FLOATING_COLUMN, RULE_BEAM_WITHOUT_SUPPORT,
         RULE_BEAM_SUPPORT_COUNT, RULE_SLAB_WITHOUT_EDGE_SUPPORT,
         RULE_INTERPENETRATION, RULE_ISOLATED_ELEMENT, RULE_STORY_Z_OVERLAP)
