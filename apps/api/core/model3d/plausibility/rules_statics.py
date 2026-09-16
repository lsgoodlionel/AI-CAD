"""力学数量级校核 —— 这个截面扛不扛得住。

**不做设计，只做数量级**。真正的配筋计算要荷载组合、内力包络、地震作用，
那些数据模型里一个都没有。这一族只回答一个粗问题：按最粗的传力假定，
这根柱、这根梁的截面**差了几个量级吗**。所以：

- 所有系数从 `codes.limit(key)` 取，**运行时取**（限值表正在被取证任务逐条填，
  import 时快照会让填好的值到不了规则）；
- 限值没取证 → 结论降到 `suspect` 并在 `basis` 注明「限值待取证」；
- 代入量是假定的（不知道混凝土等级、不知道抗震等级）→ 同样降级；
- 公式写在各函数 docstring 里，**每个代入量进 `evidence`** —— 否则结论无法复核。

面积口径与限值读取从 `rules_quantity` 取：统计荷载的从属面积必须与那边算
「单位建筑面积混凝土用量」的建筑面积是**同一口径**，分在两处写必然漂移
（本仓库面积公式写过三遍、改一处就不一致，是付过的学费）。
"""
from __future__ import annotations

import math
from collections.abc import Iterable
from typing import NamedTuple

from core.model3d.plausibility import codes, registry
from core.model3d.plausibility.model import PlausibilityModel
from core.model3d.plausibility.rules_quantity import (
    basis_of, beam_depth_m, degrade, floor_built_area_m2, limit_mapping,
    limit_range, limit_scalar,
)
from core.model3d.plausibility.types import Finding, Rule, RuleNotApplicable

#: 钢筋混凝土梁的单跨上限（m）。EMPIRICAL，无规范可引 —— 规范不规定
#: 「梁最长能有多长」。量级依据：民用建筑常规框架梁 6~9 m，预应力梁可到
#: 20~30 m；再大的跨度在工程上换的是桁架、网架、张弦梁，不再叫「梁」。
#: 取 60 m 意在只抓**荒谬**（实测出现过整张图被当成一根构件的情形），
#: 不去碰任何有争议的中间地带。
BEAM_ABSURD_SPAN_M = 60.0

#: 单段直墙的长度上限（m）。同为 EMPIRICAL。量级依据：单体建筑平面尺寸
#: 通常在 100 m 以内，超长建筑也要设变形缝把墙断开。取 500 m 是因为实测
#: 出现过场景被 2 张离群图撑到 4.8 公里、sprawl 2583 m 的情况 ——
#: 那类坐标错乱才是这条要抓的。
WALL_ABSURD_RUN_M = 500.0

#: 矩形截面回转半径 i = b/√12（b 为短边）。PHYSICS：由 I = bh³/12、A = bh
#: 代入 i = √(I/A) 得到，对绕短边方向弯曲取最小值。
_SQRT_12 = math.sqrt(12.0)


class _AxialRow(NamedTuple):
    """一根柱的轴压比校核记录（无论是否超限都产出，便于复核与测试）。"""
    target: str
    evidence: dict


# ── statics.column_axial_ratio ─────────────────────────────────────

def _axial_ratio_rows(model: PlausibilityModel) -> list[_AxialRow]:
    """逐柱算轴压比，产出全部记录（含未超限的）。

    公式（单位都在括号里，代入量全部进 `evidence`）::

        A_trib = A_楼层建筑面积(m²) / n_柱(本层柱数)      # 从属面积，**粗估**
        n_层   = 本层及以上的楼层数
        N      = A_trib × (g + q)(kN/m²) × n_层           # 轴力(kN)
        μN     = N × 1000 / (f_c(MPa) × A_c(m²) × 1e6)    # = N / (f_c × A_c × 1000)

    **从属面积是粗估，误差来源要说清楚**：

    1. 「楼层面积 ÷ 柱数」假定所有柱均分楼面。真实工程里边柱只摊半格、
       角柱摊四分之一，中柱摊满格 —— 对中柱**低估**约一倍，对角柱**高估**约四倍。
    2. 本工程柱识别率实测个位数（2026-09-15 批次 4.6%）。柱数偏少会让
       从属面积成倍放大，这是本规则**最大的系统性偏差**，方向是偏向误报。
    3. 不计悬挑、中庭、错层；不计梁自重与墙体线荷载，只算楼面均布荷载。
    4. 不知道混凝土等级时按表中**最小** f_c 假定（最不利），结论随之降到 suspect。
    5. 不知道抗震等级时取轴压比限值表中**最宽松**的一档（最不容易误报）。

    所以它能成立的只有一种结论：**差了一个数量级**。μN 算到 2 以上，
    无论上面五条怎么补偿都翻不过来；μN 在 0.8~0.9 之间的不要当回事。
    """
    # 荷载表里填的是区间（恒载 3.5~6.0）与按房间用途分档的 dict（活载）。
    # 一律取**最小**的一档：荷载取小 → N 取小 → μN 取小 → 更不容易误报。
    # 取大才是危险方向（会把正常柱判成超限），所以这里的保守方向是确定的。
    _, dead = limit_scalar("load.dead_floor_kn_m2", pick="min")
    _, live = limit_scalar("load.live_floor_kn_m2", pick="min")
    _, fc_table = limit_mapping("concrete.fc_mpa_by_grade")
    _, ratio_table = limit_mapping("column.max_axial_ratio")

    if not model.count("columns"):
        raise RuleNotApplicable("模型里没有柱")

    grade = model.meta.get("concrete_grade")
    fc_assumed = grade not in fc_table
    fc = min(float(v) for v in fc_table.values()) if fc_assumed else float(fc_table[grade])
    ratio_limit = max(float(v) for v in ratio_table.values())   # 最宽松一档
    load = dead + live

    rows: list[_AxialRow] = []
    floors_with_area = 0
    for building in model.buildings:
        ordered = sorted(building.floors, key=lambda f: f.order)
        for index, floor in enumerate(ordered):
            area = floor_built_area_m2(floor)
            columns = floor.of_kind("columns")
            if not area or not columns:
                continue
            floors_with_area += 1
            carried = len(ordered) - index
            tributary = area / len(columns)
            axial_kn = tributary * load * carried
            for column in columns:
                sides = column.sides_m()
                if not sides or sides[0] <= 0 or sides[1] <= 0:
                    continue
                section = sides[0] * sides[1]
                rows.append(_AxialRow(column.uid, {
                    "tributary_area_m2": round(tributary, 3),
                    "floors_carried": carried,
                    "load_kn_m2": round(load, 3),
                    "axial_force_kn": round(axial_kn, 2),
                    "section_area_m2": round(section, 4),
                    "fc_mpa": fc,
                    "fc_assumed": int(fc_assumed),
                    "axial_ratio": round(axial_kn / (fc * section * 1000.0), 4),
                    "limit": ratio_limit,
                }))
    if rows:
        return rows
    if not floors_with_area:
        raise RuleNotApplicable("算不出楼层建筑面积：没有一层带非兜底楼板，"
                                "从属面积无从谈起")
    raise RuleNotApplicable("柱都取不到截面（轮廓点数不足）")


def column_axial_ratio(model: PlausibilityModel) -> Iterable[Finding]:
    """柱轴压比 μN 超过限值 —— 截面小到扛不住它自己上面的楼。"""
    rows = _axial_ratio_rows(model)
    # 再取一次限值对象只为拿 source/verified 写依据；取值本身已在 rows 的 evidence 里。
    # **四条都要带上**：任何一条没取证，整个结论的凭据就不硬，都得降级。
    used = tuple(codes.limit(key) for key in (
        "column.max_axial_ratio", "load.dead_floor_kn_m2",
        "load.live_floor_kn_m2", "concrete.fc_mpa_by_grade"))
    findings: list[Finding] = []
    for row in rows:
        evidence = row.evidence
        if evidence["axial_ratio"] <= evidence["limit"]:
            continue
        findings.append(Finding(
            rule="statics.column_axial_ratio",
            severity=degrade("implausible", *used,
                             assumed=bool(evidence["fc_assumed"])),
            kind="columns", target=row.target,
            detail=(f"轴压比 μN={evidence['axial_ratio']} 超过限值 {evidence['limit']}："
                    f"从属面积 {evidence['tributary_area_m2']} m²、"
                    f"承担 {evidence['floors_carried']} 层、"
                    f"截面仅 {evidence['section_area_m2']} m²"),
            evidence=evidence,
            basis=basis_of(
                "μN = N / (f_c·A_c)，N = A_trib × (g+q) × n_层；"
                "A_trib = 楼层建筑面积 ÷ 本层柱数（粗估，边角柱误差可达数倍）",
                *used,
                extra="混凝土等级未知，f_c 按表中最小值假定 —— 结论已降级"
                      if evidence["fc_assumed"] else "")))
    return findings


registry.register(Rule(
    id="statics.column_axial_ratio", title="柱轴压比数量级校核",
    scope="element", severity="implausible",
    basis="μN = N/(f_c·A_c) + column.max_axial_ratio",
    check=column_axial_ratio))


# ── statics.beam_span_depth ────────────────────────────────────────

def beam_span_depth(model: PlausibilityModel) -> Iterable[Finding]:
    """梁高跨比 ``h / l0`` 是否落在常用区间内。

    公式：``k = h(m) / l0(m)``，l0 取梁段路径长（**近似**：路径两端到的是
    轴线交点，真实计算跨度还要扣支座宽，短梁上这个近似会把 k 低估约一成）。

    **梁高常常取不到**：scene 的梁只有 `path` + `width`（见 `model` 模块），
    梁高只有在上游写了 `depth`/`height`/`thickness` 时才有。取不到就 skip，
    绝不按宽度乘经验系数去凑一个 —— 凑出来的 h 会让这条规则和
    `qty.concrete_per_floor_area` 同时失真，而且失真方向一致，互相印证假象。
    """
    lim, lo, hi = limit_range("beam.span_depth_ratio")
    beams = list(model.elements("beams"))
    if not beams:
        raise RuleNotApplicable("模型里没有梁")

    findings: list[Finding] = []
    checked = 0
    for beam in beams:
        span, depth = beam.length_m(), beam_depth_m(beam)
        if not span or not depth:
            continue
        checked += 1
        ratio = depth / span
        if lo <= ratio <= hi:
            continue
        over = ratio > hi
        findings.append(Finding(
            rule="statics.beam_span_depth", severity=degrade("implausible", lim),
            kind="beams", target=beam.uid,
            detail=(f"高跨比 h/l0={ratio:.4f}，"
                    + (f"高于常用上限 {hi:.4f}（梁高过大，更像是把墙或整片构件当成了梁）"
                       if over else f"低于常用下限 {lo:.4f}（梁过于细柔，挠度不可能满足）")),
            evidence={"span_depth_ratio": round(ratio, 5), "span_m": round(span, 3),
                      "depth_m": round(depth, 3), "lower": round(lo, 5), "upper": round(hi, 5)},
            basis=basis_of("k = h / l0；l0 取梁段路径长（未扣支座宽，短梁偏低约一成）", lim)))
    if not checked:
        raise RuleNotApplicable("梁取不到截面高度：scene 的梁只有 path 与 width，"
                                "上游未写 depth/height/thickness")
    return findings


registry.register(Rule(
    id="statics.beam_span_depth", title="梁高跨比",
    scope="element", severity="implausible", basis="h/l0 + beam.span_depth_ratio",
    check=beam_span_depth))


# ── statics.column_slenderness ─────────────────────────────────────

def column_slenderness(model: PlausibilityModel) -> Iterable[Finding]:
    """柱长细比 ``λ = l0 / i`` 是否超限 —— 细长到会先失稳。

    公式::

        i = √(I / A)，矩形截面 I = b·h³/12、A = b·h  ⇒  i = b_短 / √12
        λ = l0 / i

    代入口径与误差来源：

    - ``l0`` 直接取**层高**。真实计算长度 l0 = μ·H，μ 随两端约束在
      0.5~2.0 之间；取 μ=1 意味着对无侧移框架**高估**、对悬臂柱**低估** 一倍。
    - ``i`` 取短边方向的最小回转半径（最不利方向），异形柱、圆柱按最小
      外接矩形的短边折算，会略偏小（偏保守，偏向误报）。

    同样只当数量级判据：λ 算到 100 以上的必是截面或层高读错了。
    """
    lim, max_lambda = limit_scalar("column.max_slenderness")
    if not model.count("columns"):
        raise RuleNotApplicable("模型里没有柱")

    findings: list[Finding] = []
    checked = 0
    floors_with_height = 0
    for floor in model.floors():
        height = floor.height_m
        if not height or height <= 0:
            continue
        floors_with_height += 1
        for column in floor.of_kind("columns"):
            sides = column.sides_m()
            if not sides or sides[1] <= 0:
                continue
            checked += 1
            radius = sides[1] / _SQRT_12
            slenderness = height / radius
            if slenderness <= max_lambda:
                continue
            findings.append(Finding(
                rule="statics.column_slenderness", severity=degrade("implausible", lim),
                kind="columns", target=column.uid,
                detail=(f"长细比 λ={slenderness:.1f} 超过 {max_lambda}："
                        f"层高 {height:.2f} m、截面短边仅 {sides[1]:.3f} m"),
                evidence={"slenderness": round(slenderness, 3),
                          "clear_height_m": round(height, 3),
                          "short_side_m": round(sides[1], 4),
                          "radius_gyration_m": round(radius, 5), "limit": max_lambda},
                basis=basis_of("λ = l0 / i，i = b短/√12（矩形截面，由 I=bh³/12、A=bh 得）；"
                               "l0 按层高取（μ=1，两端约束未知）", lim)))
    if checked:
        return findings
    if not floors_with_height:
        raise RuleNotApplicable("楼层都没有层高（标高缺失或非递增），算不出计算长度 l0")
    raise RuleNotApplicable("柱都取不到截面（轮廓点数不足）")


registry.register(Rule(
    id="statics.column_slenderness", title="柱长细比",
    scope="element", severity="implausible", basis="λ = l0/i + column.max_slenderness",
    check=column_slenderness))


# ── statics.absurd_span：已并入 geom.absurd_extent ──────────────────
#
# 两条判的是同一件事（单构件尺度荒谬），而 `geom.absurd_extent` 覆盖更全
# （六类各有上限，线状构件按 footprint 的最小外接矩形长边量，等价于段长）。
# 同一根构件被两条规则各报一次，只会让命中数虚高、让读报告的人以为
# 是两个独立证据。阈值已挪进 `codes.extent.max_by_kind_m`（EMPIRICAL，
# 逐条写明量级依据），本族不再重复实现。

