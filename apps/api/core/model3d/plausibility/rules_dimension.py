"""尺寸族规则 —— 「这个尺寸低于规范强制下限吗」。

**与几何族的分界**：几何族只用定理，不引用任何外部标准，所以它的否定
永远成立；这一族的每一条否定都**踩在一个限值上**，限值错了、或者限值
适用条件不对，否定就是错的。凭记忆写的标准常量会错，本仓库付过两次学费
（平法代号表混进非平法代号、比例表里有国标根本没有的 `1:75`）。

因此这一族有三条纪律：

1. **限值在 `check` 运行时读**，不在 import 时读 —— 取证是另一条并行的
   工作线，模块加载时快照一份值会让后来填进去的原文永远不生效
   （本仓库有过「接线静默失效，整条通道从未生效」的先例）。
2. **限值未取证（`Limit.verified` 为 False）时降级为 `suspect`**，
   并在 `basis` 里写明「限值待取证」。仍然出结论 —— 藏起来不报，
   等于把「没量」说成「合格」。
3. **限值还是占位 0 / (0,0) 时抛 `RuleNotApplicable`**。
   拿 0 当下限去比，结果恒为「全体合格」，那是最坏的一种静默降级。

**单位**：限值表里的截面尺寸是**毫米**，构件坐标是**米**。
比较一律换算到毫米再比，并且用乘法（`m * 1000 < limit_mm`）而不是除法，
免得在边界值上被浮点舍入翻了方向。
"""
from __future__ import annotations

from core.model3d.plausibility import codes
from core.model3d.plausibility.model import PlausibilityModel
from core.model3d.plausibility.registry import register
# 公式出处的拼装与降级统一在 rules_quantity（`degrade`/`basis_of` 的老家）。
from core.model3d.plausibility.rules_quantity import formula_basis, formula_ref
from core.model3d.plausibility.types import Finding, Rule, RuleNotApplicable

#: 米 → 毫米
MM_PER_M = 1000.0

#: 柱截面（短边、长宽比）都量自**最小面积外接矩形**，判据的凭据落在它身上。
#: 只写 key，渲染留到 `check` 里 —— 出处正由取证任务逐条填。
_SECTION_FORMULAS = ("geometry.min_area_rect",)

#: 「柱」的最小外接矩形长宽比上限。超过它的竖向构件不是柱，是墙肢或条带。
#: 依据：识别器猜测路径本身用的就是 4:1 窗口（`element_recognizer._is_column_size`），
#: 而图层路径放宽到 8:1；两条路径落库到同一张表，长宽比 4~8 的那一批
#: 正是靠图层名混进来的墙。本条复核的是**落库结果**，统一按 4:1 收口。
#: 量级上也对得上：短肢剪力墙的构造分界即在 4~8 倍肢厚之间，
#: 长宽比超过 4 的截面按墙肢而不是按柱配筋与计算。
#: 这是**工程形态判据不是规范条款原文**，故只出 `implausible`，不出 `impossible`。
COLUMN_MAX_ASPECT = 4.0


# ── 限值读取（运行时，不是 import 时）────────────────────────────────

def _positive_limit(key: str) -> codes.Limit:
    """取一条**下限型**限值。还是占位 0 就抛 `RuleNotApplicable`。

    规范按构件形式分档时（矩形柱 300 / 圆柱 350 等）取**最宽松的一档**，
    理由见 `codes.lower_bound`：模型分不出是哪一种形式，只能否定「低于任何
    一种允许情形」的构件。取哪一档由 `_limit_floor` 一并返回，写进证据。
    """
    limit = codes.limit(key)
    try:
        number, _label = codes.lower_bound(key)
    except (TypeError, ValueError) as exc:
        raise RuleNotApplicable(f"限值 {key} 取不出可比较的数值（{exc}）")
    if number <= 0:
        raise RuleNotApplicable(f"限值 {key} 尚未填值（当前占位 {number}）—— 不能拿 0 当下限")
    return limit


def _limit_floor(key: str) -> tuple[float, str]:
    """(下限数值, 档位名)。档位名为空表示这条限值本就不分档。"""
    return codes.lower_bound(key)


def _range_limit(key: str) -> tuple[codes.Limit, float, float]:
    """取一条**区间型**限值 (lo, hi)。还是占位 (0, 0) 就抛 `RuleNotApplicable`。"""
    limit = codes.limit(key)
    value = limit.value
    if not isinstance(value, (tuple, list)) or len(value) != 2:
        raise RuleNotApplicable(f"限值 {key} 不是二元区间（当前 {value!r}）")
    try:
        low, high = float(value[0]), float(value[1])
    except (TypeError, ValueError):
        raise RuleNotApplicable(f"限值 {key} 的端点不是数值（当前 {value!r}）")
    if not (0 < low < high):
        raise RuleNotApplicable(f"限值 {key} 尚未填值（当前占位 {value!r}）")
    return limit, low, high


def _grade(limit: codes.Limit, basis: str) -> tuple[str, str]:
    """→ (严重度, 依据)。限值没取证就把结论降到 `suspect` 并在依据里说明。"""
    if limit.verified:
        return "implausible", f"{basis}（出处：{limit.source}）"
    return "suspect", (f"{basis}（**限值待取证**：{limit.source}，"
                       "未取到规范原文，故结论降级为 suspect，不作为否定依据）")


def _elements(model: PlausibilityModel, kind: str, field: str | None = None) -> list:
    items = [e for e in model.elements(kind)]
    if field is not None:
        items = [e for e in items if e.raw.get(field) is not None]
    return items


# ── 截面下限四条（柱 / 梁 / 板 / 墙）───────────────────────────────

def _check_scalar_below(model: PlausibilityModel, *, rule_id: str, kind: str,
                        field: str, limit_key: str, label: str, what: str,
                        evidence_key: str) -> list[Finding]:
    """「某个标量尺寸低于限值」的共同骨架 —— 三条规则只差取哪个字段。

    `evidence_key` 单独传：墙的存储字段是 `width`，但工程语义上是**厚度**，
    证据里按语义命名才能被复核的人看懂。
    """
    limit = _positive_limit(limit_key)
    targets = _elements(model, kind, field)
    if not targets:
        raise RuleNotApplicable(f"模型里没有带 {field} 的 {kind}")
    ceiling_mm, tier = _limit_floor(limit_key)
    tier_note = f"（取最宽松一档：{tier}）" if tier else ""
    severity, basis = _grade(limit, f"{what}，限值 {ceiling_mm:g} mm{tier_note}")
    findings: list[Finding] = []
    for element in targets:
        try:
            meters = float(element.raw[field])
        except (TypeError, ValueError):
            continue                          # 非数值由 geom.non_positive_size 去报
        millimeters = meters * MM_PER_M
        if millimeters <= 0 or millimeters >= ceiling_mm:
            continue                          # 非正尺寸同样归几何族，不在这里重复报
        findings.append(Finding(
            rule=rule_id, severity=severity, kind=kind, target=element.uid,
            detail=f"{label} {millimeters:g} mm，低于下限 {ceiling_mm:g} mm",
            evidence={f"{evidence_key}_mm": millimeters, "limit_mm": ceiling_mm,
                      "limit_tier": tier, "value_m": meters,
                      "limit_verified": limit.verified},
            basis=basis))
    return findings


def check_column_section(model: PlausibilityModel) -> list[Finding]:
    limit = _positive_limit("column.min_section_mm")
    targets = [e for e in model.elements("columns") if e.sides_m() is not None]
    if not targets:
        raise RuleNotApplicable("模型里没有能量出截面的柱（缺轮廓或轮廓退化）")
    ceiling_mm, tier = _limit_floor("column.min_section_mm")
    tier_note = f"（取最宽松一档：{tier}）" if tier else ""
    severity, basis = _grade(
        limit, f"框架柱截面最小边长限值 {ceiling_mm:g} mm{tier_note}")
    findings: list[Finding] = []
    for element in targets:
        long_m, short_m = element.sides_m()   # type: ignore[misc]
        short_mm = short_m * MM_PER_M
        if short_mm <= 0 or short_mm >= ceiling_mm:
            continue
        findings.append(Finding(
            rule="dim.column_section_below_code", severity=severity,
            kind="columns", target=element.uid,
            detail=f"截面短边 {short_mm:g} mm，低于下限 {ceiling_mm:g} mm",
            evidence={"short_side_mm": short_mm, "long_side_mm": long_m * MM_PER_M,
                      "limit_mm": ceiling_mm, "limit_tier": tier,
                      "limit_verified": limit.verified},
            basis=formula_basis(
                basis + "。截面按**最小面积外接矩形**量，不按轴对齐包围盒 ——"
                        "斜置构件的包围盒是它的影子，会把短边量大（标高符号"
                        "那条 `∨` 斜笔画就是这么被量成 0.52×0.59 m 近方形、"
                        "混进柱里的）。",
                *_SECTION_FORMULAS)))
    return findings


def check_beam_width(model: PlausibilityModel) -> list[Finding]:
    return _check_scalar_below(
        model, rule_id="dim.beam_width_below_code", kind="beams", field="width",
        limit_key="beam.min_width_mm", label="梁宽", what="框架梁最小截面宽度",
        evidence_key="width")


def check_slab_thickness(model: PlausibilityModel) -> list[Finding]:
    return _check_scalar_below(
        model, rule_id="dim.slab_thickness_below_code", kind="slabs",
        field="thickness", limit_key="slab.min_thickness_mm", label="板厚",
        what="现浇钢筋混凝土板最小厚度", evidence_key="thickness")


def check_wall_thickness(model: PlausibilityModel) -> list[Finding]:
    return _check_scalar_below(
        model, rule_id="dim.wall_thickness_below_code", kind="walls", field="width",
        limit_key="wall.min_thickness_mm", label="墙厚", what="承重墙最小厚度",
        evidence_key="thickness")


# ── 层高两条 ────────────────────────────────────────────────────────

def _floor_target(floor) -> str:
    return f"{floor.building_key}/{floor.key}"


def check_story_height_range(model: PlausibilityModel) -> list[Finding]:
    limit, low, high = _range_limit("story.height_range_m")
    # 层高 ≤ 0 是物理不可能，归 `dim.non_positive_story_height`，这里不重复报。
    targets = [f for f in model.floors() if f.height_m is not None and f.height_m > 0]
    if not targets:
        raise RuleNotApplicable("没有任何一层能算出层高（缺标高或标高非递增）")
    severity, basis = _grade(limit, f"民用建筑层高合理区间 {low:g}~{high:g} m")
    findings: list[Finding] = []
    for floor in targets:
        height = float(floor.height_m)        # type: ignore[arg-type]
        if low <= height <= high:
            continue
        side = "低于" if height < low else "高于"
        findings.append(Finding(
            rule="dim.story_height_out_of_range", severity=severity,
            kind="floor", target=_floor_target(floor),
            detail=f"{floor.label} 层高 {height:.2f} m，{side}合理区间 {low:g}~{high:g} m",
            evidence={"height_m": height, "min_m": low, "max_m": high,
                      "limit_verified": limit.verified,
                      # 层高由相邻标高相减而来：标高是估的，层高就是估的。
                      # 不因此改严重度（本仓库绝大多数楼层标高都带估值标记，
                      # 一改就等于这条规则永远只出 suspect），但必须让人看见。
                      "elevation_estimated": bool(floor.elevation_estimated)},
            basis=basis + "。层高 = 相邻楼层标高之差，标高是否为估值见证据 "
                          "`elevation_estimated`。"))
    return findings


def check_non_positive_story_height(model: PlausibilityModel) -> list[Finding]:
    targets = [f for f in model.floors() if f.height_m is not None]
    if not targets:
        raise RuleNotApplicable("没有任何一层能算出层高（缺标高或标高非递增）")
    findings: list[Finding] = []
    for floor in targets:
        height = float(floor.height_m)        # type: ignore[arg-type]
        if height > 0:
            continue
        findings.append(Finding(
            rule="dim.non_positive_story_height", severity="impossible",
            kind="floor", target=_floor_target(floor),
            detail=f"{floor.label} 层高 {height:.3f} m，非正",
            evidence={"height_m": height, "elevation_m": floor.elevation_m,
                      "elevation_estimated": bool(floor.elevation_estimated)},
            basis="层高是上一层标高减本层标高。等于 0 意味着两层叠在同一标高上、"
                  "小于 0 意味着上层在下层之下 —— 楼层序与标高序自相矛盾，"
                  "不需要任何规范就能否定（量纲/序关系）。"))
    return findings


# ── 柱长宽比 ────────────────────────────────────────────────────────

def check_column_aspect_ratio(model: PlausibilityModel) -> list[Finding]:
    targets = [e for e in model.elements("columns") if e.sides_m() is not None]
    if not targets:
        raise RuleNotApplicable("模型里没有能量出截面的柱（缺轮廓或轮廓退化）")
    findings: list[Finding] = []
    for element in targets:
        long_m, short_m = element.sides_m()   # type: ignore[misc]
        if short_m <= 0:
            continue                          # 退化轮廓归几何族
        # 用乘法而不是除法比较：`1.6 / 0.4` 这类边界值在浮点下会翻方向，
        # 而「正好 4:1 的柱仍是柱」这条边界必须稳。
        if short_m * COLUMN_MAX_ASPECT >= long_m:
            continue
        findings.append(Finding(
            rule="dim.column_aspect_ratio", severity="implausible",
            kind="columns", target=element.uid,
            detail=f"截面 {long_m:.3f}×{short_m:.3f} m，长宽比 "
                   f"{long_m / short_m:.1f} > {COLUMN_MAX_ASPECT}，实为墙肢或条带",
            evidence={"aspect_ratio": long_m / short_m, "limit": COLUMN_MAX_ASPECT,
                      "long_side_m": long_m, "short_side_m": short_m},
            basis=formula_basis(
                f"长宽比上限 {COLUMN_MAX_ASPECT}：识别器猜测路径用的即是 4:1 窗口，"
                "图层路径放宽到 8:1，两条路径落库同一张表，4~8 的那一批是靠图层名"
                "混进来的墙；短肢剪力墙的构造分界也在这个量级。"
                "长短边同样取自最小面积外接矩形，斜置构件不会被包围盒量胖。"
                "阈值见模块常量 COLUMN_MAX_ASPECT。",
                *_SECTION_FORMULAS)))
    return findings


# ── 注册 ────────────────────────────────────────────────────────────

RULES: dict[str, Rule] = {
    rule.id: rule for rule in (
        register(Rule(
            id="dim.column_section_below_code", title="柱截面短边低于规范下限",
            scope="element", severity="implausible",
            basis="codes: column.min_section_mm（未取证时降级 suspect）；"
                  "截面口径 " + formula_ref(*_SECTION_FORMULAS),
            check=check_column_section)),
        register(Rule(
            id="dim.beam_width_below_code", title="梁宽低于规范下限",
            scope="element", severity="implausible",
            basis="codes: beam.min_width_mm（未取证时降级 suspect）",
            check=check_beam_width)),
        register(Rule(
            id="dim.slab_thickness_below_code", title="板厚低于规范下限",
            scope="element", severity="implausible",
            basis="codes: slab.min_thickness_mm（未取证时降级 suspect）",
            check=check_slab_thickness)),
        register(Rule(
            id="dim.wall_thickness_below_code", title="墙厚低于规范下限",
            scope="element", severity="implausible",
            basis="codes: wall.min_thickness_mm（未取证时降级 suspect）",
            check=check_wall_thickness)),
        register(Rule(
            id="dim.story_height_out_of_range", title="层高超出合理区间",
            scope="floor", severity="implausible",
            basis="codes: story.height_range_m（未取证时降级 suspect）",
            check=check_story_height_range)),
        register(Rule(
            id="dim.non_positive_story_height", title="层高非正",
            scope="floor", severity="impossible",
            basis="层高为相邻标高之差，≤0 与楼层序自相矛盾",
            check=check_non_positive_story_height)),
        register(Rule(
            id="dim.column_aspect_ratio", title="柱长宽比过大（其实是墙肢）",
            scope="element", severity="implausible",
            basis=f"长宽比上限 {COLUMN_MAX_ASPECT}，见模块常量 COLUMN_MAX_ASPECT；"
                  "长短边口径 " + formula_ref(*_SECTION_FORMULAS),
            check=check_column_aspect_ratio)),
    )
}
