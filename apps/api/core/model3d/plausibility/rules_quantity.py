"""量纲与量级守恒 —— 抓「整体上不可能」的那一类错。

**这一族存在的理由是一次真事故**：兜底板把**图框**当成了楼板，
折算混凝土方量 519,684 m³，而这个工程的真实量级在几万。
单看那块板，它的面积、厚度、轮廓样样「合法」；只有把它除以建筑面积、
和「每平米用多少混凝土」这个量级一比，才会露馅。逐构件的规则永远抓不到它，
所以要有这一族。

**估算口径（全族共用，写在这里是为了不漂移）**

| 构件 | 体积 |
|---|---|
| 柱 | 最小面积外接矩形长×短 × 层高 |
| 板 | 轮廓面积 × 厚度 |
| 墙 | 路径长 × 宽 × 层高 |
| 梁 | 路径长 × 宽 × 梁高（**梁高常常没有，此时不估，如实记欠账**）|

柱用**外接矩形**而不是轮廓面积：实测 31% 的柱轮廓是自交的「蝴蝶结」，
鞋带面积等值反号恰好抵成 0，用它算体积会把柱全部算没。外接矩形由凸包导出，
对自交免疫。板反过来用轮廓面积 —— L 形楼层的外接矩形会严重高估。

**不 import `services/model_qto.py`**：那是三审看的那份量，走的是另一条链路
（构件净扣减、模板面积、按标高分段）。这里只要数量级，两条链路互为独立校核，
合成一条就失去了交叉验证的意义，还会把 ORM 依赖拖进纯计算层。

**建筑面积口径**：只认**非兜底**楼板的面积之和。兜底板（`basis` 非空）
不计入分母，却照常计入分子 —— 这个不对称是刻意的，它正是 519,684 m³
那类错误的指纹：假板把分子撑大而分母不动，比值立刻越界。
"""
from __future__ import annotations

from collections.abc import Iterable

from core.model3d.plausibility import codes, formulas, geometry as geo, registry
from core.model3d.plausibility.codes import Limit
from core.model3d.plausibility.model import Floor, PlausibilityModel
from core.model3d.plausibility.types import Finding, Rule, RuleNotApplicable

#: 参与混凝土体积估算的构件类别。管线与设备不是混凝土，不进这本账。
CONCRETE_KINDS = ("columns", "walls", "beams", "slabs")

#: 本层构件占地之和 ÷ 本层包络面积的上限。构件本来就互相压叠
#: （梁压墙、柱埋在墙里、板盖住一切），比值略大于 1 是常态；
#: 到 3 倍只能是同一批构件被数了好几遍（总图与分图重复导入的指纹）。
#: EMPIRICAL：无规范可引 —— 规范不管「一层楼被导入几次」。
FOOTPRINT_OVER_ENVELOPE_MAX = 3.0

#: 同一比值的下限。低到 2% 意味着 20×20 的范围里只剩几根柱，
#: 说的是识别几乎全丢，不是现实不可能，故只到 suspect。
FOOTPRINT_OVER_ENVELOPE_MIN = 0.02


# ── 限值读取（全族共用；statics 也从这里取）────────────────────────
#
# **一律运行时取，不在 import 时取**：限值表正在被取证任务逐条填，
# import 时快照会让填好的值到不了规则，而这类静默失效在本仓库发生过多次。

def limit_range(key: str) -> tuple[Limit, float, float]:
    """取一条区间限值。占位（lo≥hi）视为「还没填」，如实 skip。"""
    lim = codes.limit(key)
    raw = lim.value
    if not isinstance(raw, (tuple, list)) or len(raw) != 2:
        raise RuleNotApplicable(f"限值 {key} 不是区间：{raw!r}")
    lo, hi = float(raw[0]), float(raw[1])
    if hi <= lo:
        raise RuleNotApplicable(f"限值 {key} 尚未取证填值（占位 {raw!r}）")
    return lim, lo, hi


def _is_number(raw) -> bool:
    return isinstance(raw, (int, float)) and not isinstance(raw, bool)


def limit_scalar(key: str, *, pick: str | None = None) -> tuple[Limit, float]:
    """取一条要当成一个数用的限值。

    表里同一个量会有三种形状（取证任务按条款原文填，形状随原文走）：
    标量、区间 `(lo, hi)`、按档分的 dict（楼面活载就是按房间用途分的）。
    `pick` 说明**在不知道该用哪一档时取哪一端**：`min` / `max` / `mid`。
    `pick=None` 表示这个量只接受标量，形状不对就如实 skip。

    选端的方向要由调用方按「哪一端更不容易误报」来定，并在那里写明理由 ——
    选错端会让规则系统性地多报，而多报出来的每一条都长得像真的。
    """
    lim = codes.limit(key)
    raw = lim.value
    if _is_number(raw):
        return (lim, float(raw)) if float(raw) > 0 else _placeholder(key, raw)

    if pick is None:
        raise RuleNotApplicable(f"限值 {key} 是 {type(raw).__name__}（{raw!r}），"
                                "而本规则要一个标量 —— 取证填的形状与用法不匹配")

    if isinstance(raw, (tuple, list)) and len(raw) == 2 and all(_is_number(v) for v in raw):
        candidates = [float(v) for v in raw]
    elif isinstance(raw, dict) and raw and all(_is_number(v) for v in raw.values()):
        candidates = [float(v) for v in raw.values()]
    else:
        return _placeholder(key, raw)

    chosen = {"min": min, "max": max}.get(pick)
    value = chosen(candidates) if chosen else sum(candidates) / len(candidates)
    if value <= 0:
        return _placeholder(key, raw)
    return lim, value


def _placeholder(key: str, raw) -> tuple[Limit, float]:
    raise RuleNotApplicable(f"限值 {key} 尚未取证填值（占位 {raw!r}）")


def limit_mapping(key: str) -> tuple[Limit, dict]:
    """取一条分档限值（按抗震等级、混凝土强度等级分档）。空 dict 视为占位。"""
    lim = codes.limit(key)
    raw = lim.value
    if not isinstance(raw, dict) or not raw:
        raise RuleNotApplicable(f"限值 {key} 尚未取证填值（占位 {raw!r}）")
    return lim, dict(raw)


def degrade(base: str, *limits: Limit, assumed: bool = False) -> str:
    """限值没取证、或有代入量是假定的 → 结论降到 `suspect`。

    理由见 `codes` 模块：一个记错的数字会把真构件判成「不可能」，
    比不查更坏。所以凭据不硬时，结论的语气也要跟着软下来。
    """
    if assumed or any(not lim.verified for lim in limits):
        return "suspect"
    return base


def basis_of(formula: str, *limits: Limit, extra: str = "") -> str:
    """依据串：公式 + 每条限值的取值与出处 + 降级说明。"""
    parts = [formula]
    parts += [f"{lim.key}={lim.value}{lim.unit} [{lim.source}]" for lim in limits]
    if extra:
        parts.append(extra)
    if any(not lim.verified for lim in limits):
        parts.append("限值待取证 —— 结论已降级为 suspect")
    return "；".join(parts)


# ── 公式出处（全族共用；几何/尺寸/支承/力学四族都从这里取）──────────
#
# 与 `degrade` / `basis_of` 作伴，因为回答的是同一个问题：**凭据有多硬，
# 结论的语气就该有多硬**。规范那一侧的规矩是「限值没取证就降级」，
# 数学这一侧一模一样 —— 公式找不到出处，就不许用「不可能」这个词。
#
# **一律运行时读 `formulas.FORMULAS`**：出处正由取证任务逐条填，
# import 时拼好的字符串会把「待取证」永久钉死在报告里（本仓库的
# 「接线静默失效、整条通道从未生效」正是这么来的）。

def cite_formulas(*keys: str) -> str:
    """逐条公式的一行引用，**带成立条件**。

    成立条件单列，是取证这一轮真正的产出：鞋带公式只对简单多边形成立、
    Sutherland–Hodgman 只对凸裁剪多边形成立 —— 条件不满足时公式照样算得出
    一个数，错的结论看上去仍然「有公式撑腰」，比没有公式更难发现。
    """
    return "；".join(
        f"{formulas.cite(key)}，成立条件：{formulas.formula(key).conditions}"
        for key in keys)


def formula_severity(base: str, *keys: str) -> str:
    """出处未落实 → `impossible` 降为 `implausible`。

    `impossible` 的含义是「数学或物理上不可能」。凭一条找不到出处的公式说
    「不可能」，是把没有依据说成了最强的依据。另两档不动 ——
    `implausible` / `suspect` 本来就不自称定理。
    """
    if base != "impossible":
        return base
    return base if all(formulas.formula(key).cited for key in keys) else "implausible"


def formula_basis(explanation: str, *keys: str, base: str = "") -> str:
    """依据串 = 公式引用（含成立条件）+ 本规则自己的说明 + 降级说明。

    两截都要：**出处回答「凭什么」，说明回答「所以怎么了」**。
    只留出处，读报告的人不知道这条公式在这里被用来否定什么；
    只留说明，就退回到这次取证之前 —— 判据只有名字，没有来处。
    """
    parts = [cite_formulas(*keys), explanation]
    missing = [key for key in keys if not formulas.formula(key).cited]
    if missing:
        note = "公式出处待取证（" + "、".join(missing) + "）"
        if base == "impossible":
            note += " —— 结论已由 impossible 降为 implausible"
        parts.append(note)
    return "；".join(part for part in parts if part)


def formula_ref(*keys: str) -> str:
    """给 `Rule.basis` 用的**指针**，不是渲染好的引用。

    `Rule` 在 import 时构造，此刻把出处渲染进去就是快照：取证任务后来填的
    原文永远到不了规则级依据。所以规则级只回指 key，渲染留到 `check` 里。

    只校验 key 存在（拼错当场 KeyError），不读它的 `source` —— 校验的是
    「这条公式在不在表里」，那个事实不会被取证改写。
    """
    for key in keys:
        formulas.formula(key)
    return "见 formulas：" + "、".join(keys)


def floor_target(floor: Floor) -> str:
    """楼层 key 在不同单体间会重名，带上单体才唯一。"""
    return f"{floor.building_key}/{floor.key}"


# ── 面积与体积的估算口径 ────────────────────────────────────────────

def is_fallback_slab(element) -> bool:
    """兜底板：不是从图上认出来的板，是识别失败后拿最大多边形凑的。"""
    return element.kind == "slabs" and bool(element.fallback_basis)


def floor_built_area_m2(floor: Floor) -> float | None:
    """本层建筑面积 = **非兜底**楼板面积之和。没有可信板就返回 None。

    为什么不拿构件包络兜底：几根柱的凸包只有零点几平米，
    当成「建筑面积」会让所有下游比值荒唐地放大。宁可 skip。
    """
    areas = [element.area_m2() for element in floor.of_kind("slabs")
             if not is_fallback_slab(element)]
    usable = [a for a in areas if a and a > 0]
    return sum(usable) if usable else None


def floor_envelope_area_m2(floor: Floor) -> float | None:
    """本层包络面积 = 所有构件占地顶点的凸包面积。

    **含兜底板**：这里要抓的是重复计数，先把嫌疑对象剔掉就没得查了。
    """
    points: list[tuple[float, float]] = []
    for element in floor.elements:
        points.extend(element.footprint())
    if len(points) < 3:
        return None
    hull = geo.convex_hull(points)
    if len(hull) < 3:
        return None
    area = geo.polygon_area(hull)
    return area if area > 0 else None


def beam_depth_m(element) -> float | None:
    """梁高。scene 的梁只有 `path` + `width`，梁高多半没有 —— 没有就是没有。

    依次找 `depth` / `height` / `thickness`：三个字段名在不同上游写法里都出现过，
    但**一个也不会去猜**（例如按宽度乘个经验系数），编出来的梁高会让
    高跨比与混凝土用量两条规则同时失真。
    """
    raw_depth = element.raw.get("depth")
    if raw_depth is not None:
        return float(raw_depth)
    return element.height_m if element.height_m else element.thickness_m


def element_volume_m3(element, floor_height_m: float | None) -> float | None:
    """单个构件的混凝土体积估算。缺任何一个代入量就返回 None（不补）。"""
    if element.kind == "slabs":
        area, thickness = element.area_m2(), element.thickness_m
        return area * thickness if area and thickness else None

    if element.kind == "columns":
        sides = element.sides_m()
        if not sides or not floor_height_m:
            return None
        return sides[0] * sides[1] * floor_height_m

    if element.kind == "walls":
        length, width = element.length_m(), element.width_m
        if not length or not width or not floor_height_m:
            return None
        return length * width * floor_height_m

    if element.kind == "beams":
        length, width, depth = element.length_m(), element.width_m, beam_depth_m(element)
        if not length or not width or not depth:
            return None
        return length * width * depth

    return None


def _concrete_totals(model: PlausibilityModel) -> dict:
    """整模型的体积与建筑面积汇总，附带欠账计数（欠了多少没估进去）。"""
    by_kind = {kind: 0.0 for kind in CONCRETE_KINDS}
    fallback_volume = 0.0
    missing = 0
    area = 0.0
    floors_with_area = 0
    for floor in model.floors():
        floor_area = floor_built_area_m2(floor)
        if floor_area:
            area += floor_area
            floors_with_area += 1
        for element in floor.elements:
            if element.kind not in CONCRETE_KINDS:
                continue
            volume = element_volume_m3(element, floor.height_m)
            if volume is None:
                missing += 1
                continue
            by_kind[element.kind] += volume
            if is_fallback_slab(element):
                fallback_volume += volume
    return {"by_kind": by_kind, "volume": sum(by_kind.values()), "area": area,
            "floors_with_area": floors_with_area, "fallback_volume": fallback_volume,
            "elements_not_estimated": missing}


# ── qty.concrete_per_floor_area ────────────────────────────────────

def concrete_per_floor_area(model: PlausibilityModel) -> Iterable[Finding]:
    """总混凝土体积 ÷ 总建筑面积，是否落在工程量级区间内。

    公式：``r = ΣV_构件 / ΣA_楼板(非兜底)``，单位 m³/m²。

    两侧的含义**不一样**，所以严重度也不一样：

    - 超上限 → `implausible`。多出来的混凝土没有地方去，只能是重复计数
      或把不是楼板的东西当了楼板（图框、总图外轮廓）。
    - 低于下限 → `suspect`。它说的是识别不全（本工程实测板 3.9%、柱 4.6%），
      是模型欠账，不是现实不可能。把它也报成 implausible 会让报告失去分辨力。

    误差来源（都写进 evidence，便于复核）：梁高常缺 → 体积偏低；
    柱按外接矩形算 → 异形柱偏高；不做构件间净扣减（梁柱交叠处重复计一次）→ 偏高。
    """
    lim, lo, hi = limit_range("quantity.concrete_per_floor_area_m3_m2")
    totals = _concrete_totals(model)
    if not totals["area"]:
        raise RuleNotApplicable("算不出建筑面积：模型里没有非兜底楼板")
    if not totals["volume"]:
        raise RuleNotApplicable("估不出混凝土体积：构件都缺截面或层高")

    ratio = totals["volume"] / totals["area"]
    if lo <= ratio <= hi:
        return []

    over = ratio > hi
    severity = degrade("implausible" if over else "suspect", lim)
    detail = (f"单位建筑面积混凝土用量 {ratio:.3f} m³/m²，"
              + (f"高于常规上限 {hi}" if over else f"低于常规下限 {lo}")
              + ("；多出的量只能来自重复计数或把非楼板当成了楼板"
                 if over else "；更像是构件识别不全造成的欠账"))
    evidence = {
        "ratio_m3_m2": round(ratio, 4),
        "total_volume_m3": round(totals["volume"], 2),
        "total_area_m2": round(totals["area"], 2),
        "lower_m3_m2": lo, "upper_m3_m2": hi,
        "fallback_slab_volume_m3": round(totals["fallback_volume"], 2),
        "elements_not_estimated": totals["elements_not_estimated"],
        "floors_with_area": totals["floors_with_area"],
    }
    evidence.update({f"volume_{k}_m3": round(v, 2) for k, v in totals["by_kind"].items()})
    return [Finding(rule="qty.concrete_per_floor_area", severity=severity, kind="model",
                    target="model", detail=detail, evidence=evidence,
                    basis=basis_of(
                        formula_basis(
                            "r = ΣV / ΣA（m³/m²）；V 按柱=截面×层高、板=面积×厚、"
                            "墙=长×宽×层高、梁=长×宽×高估算。板面积用鞋带公式，"
                            "柱截面用最小面积外接矩形（柱轮廓实测 31% 自交，"
                            "鞋带面积会相消成 0，对自交免疫的只有外接矩形）",
                            "geometry.shoelace_area", "geometry.min_area_rect"),
                        lim,
                        extra="兜底板计入分子不计入分母 —— 假板的指纹"))]


registry.register(Rule(
    id="qty.concrete_per_floor_area", title="单位建筑面积混凝土用量",
    scope="model", severity="implausible",
    basis="量纲守恒 + quantity.concrete_per_floor_area_m3_m2（"
          + formula_ref("geometry.shoelace_area", "geometry.min_area_rect") + "）",
    check=concrete_per_floor_area))


# ── qty.rebar_per_concrete ─────────────────────────────────────────

def rebar_per_concrete(model: PlausibilityModel) -> Iterable[Finding]:
    """每立方米混凝土的钢筋含量 ``ρ = W_钢筋 / V_混凝土``（kg/m³）。

    **scene 里没有钢筋量**，所以这条规则在当前数据上几乎总是 skip。
    保留它是因为接线点是确定的：钢筋量由 `core/economic/rebar_calculator`
    算得、走 QTO 链路，接进来只需把总重放进 `model.meta["rebar_kg"]`。
    在那之前**不编一个含钢量** —— 编出来的比值只会永远落在区间中央，
    等于凭空给模型发一张合格证。
    """
    lim, lo, hi = limit_range("quantity.rebar_per_concrete_kg_m3")
    rebar_kg = model.meta.get("rebar_kg")
    if rebar_kg is None:
        raise RuleNotApplicable("模型不带钢筋量（scene 无此字段），"
                                "接入点：model.meta['rebar_kg']")
    totals = _concrete_totals(model)
    if not totals["volume"]:
        raise RuleNotApplicable("估不出混凝土体积，钢筋含量无从比较")

    density = float(rebar_kg) / totals["volume"]
    if lo <= density <= hi:
        return []
    over = density > hi
    return [Finding(
        rule="qty.rebar_per_concrete", severity=degrade("implausible", lim), kind="model",
        target="model",
        detail=f"含钢量 {density:.1f} kg/m³，" + (f"高于 {hi}" if over else f"低于 {lo}"),
        evidence={"rebar_kg_m3": round(density, 2), "rebar_kg": float(rebar_kg),
                  "total_volume_m3": round(totals["volume"], 2),
                  "lower_kg_m3": lo, "upper_kg_m3": hi},
        basis=basis_of("ρ = W / V（kg/m³）", lim))]


registry.register(Rule(
    id="qty.rebar_per_concrete", title="单方混凝土含钢量",
    scope="model", severity="implausible",
    basis="量纲守恒 + quantity.rebar_per_concrete_kg_m3",
    check=rebar_per_concrete))


# ── qty.element_density ────────────────────────────────────────────

def element_density(model: PlausibilityModel) -> Iterable[Finding]:
    """每 100 m² 建筑面积上的柱数是否与常见柱网相称。

    推导：柱网近似 s×s 时，每根柱分摊 s² m²，故每 100 m² 的柱数 ``n = 100 / s²``。
    柱网跨度区间 (s_lo, s_hi) 取自 `grid.span_range_m`，跨度越大柱越稀，
    所以密度区间**反序**：``(100/s_hi², 100/s_lo²)``。
    例：柱网 4~12 m → 0.69~6.25 根/100 m²。

    误差来源：真实柱网不是正方形（长短向跨度常差一倍），此式按等跨近似，
    对长方形柱网会偏保守（算出的密度介于两个方向之间）。所以只当
    **数量级**判据用，严重度封顶在 `suspect`。
    """
    lim, span_lo, span_hi = limit_range("grid.span_range_m")
    lower = 100.0 / (span_hi ** 2)
    upper = 100.0 / (span_lo ** 2)

    findings: list[Finding] = []
    checked = 0
    for floor in model.floors():
        area = floor_built_area_m2(floor)
        if not area:
            continue
        checked += 1
        count = len(floor.of_kind("columns"))
        density = count / (area / 100.0)
        if lower <= density <= upper:
            continue
        over = density > upper
        detail = (f"{floor.label} 每 100 m² 有 {density:.2f} 根柱，"
                  + (f"密于柱网 {span_lo} m 对应的 {upper:.2f}" if over
                     else f"稀于柱网 {span_hi} m 对应的 {lower:.2f}"))
        findings.append(Finding(
            rule="qty.element_density", severity=degrade("suspect", lim), kind="columns",
            target=floor_target(floor), detail=detail,
            evidence={"columns_per_100m2": round(density, 3), "column_count": count,
                      "built_area_m2": round(area, 2),
                      "lower_per_100m2": round(lower, 3), "upper_per_100m2": round(upper, 3),
                      "span_lo_m": span_lo, "span_hi_m": span_hi},
            basis=basis_of("n = 100 / s²（根/100 m²），s 为柱网跨度", lim)))
    if not checked:
        raise RuleNotApplicable("没有一层算得出建筑面积（缺非兜底楼板）")
    return findings


registry.register(Rule(
    id="qty.element_density", title="柱密度与柱网跨度相称性",
    scope="floor", severity="suspect", basis="柱网跨度反推密度 + grid.span_range_m",
    check=element_density))


# ── qty.floor_area_vs_envelope ─────────────────────────────────────

def floor_area_vs_envelope(model: PlausibilityModel) -> Iterable[Finding]:
    """本层构件占地之和 ÷ 本层包络面积。

    ``k = Σ A_占地 / A_包络``。构件互相压叠，k 略大于 1 是常态；
    k 到 `FOOTPRINT_OVER_ENVELOPE_MAX`（3）只能是同一批构件被数了好几遍 ——
    这是「总图与分图重复导入」的整体指纹，逐构件看每一个都正常。
    k 小于 `FOOTPRINT_OVER_ENVELOPE_MIN`（0.02）则相反：包络那么大，
    里面几乎没有东西，说的是识别塌了。

    占地口径：面状取轮廓，线状按 `width` 加宽成矩形（见 `Element.footprint`）。
    误差来源：包络用**凸包**，凹形（L 形、口字形）楼层的包络会偏大，
    使 k 偏小 —— 即对上限判据偏保守（不容易误报），对下限判据偏敏感，
    所以下限只给 suspect。
    """
    findings: list[Finding] = []
    checked = 0
    for floor in model.floors():
        envelope = floor_envelope_area_m2(floor)
        if not envelope:
            continue
        rings = [element.footprint() for element in floor.elements]
        footprint = sum(geo.polygon_area(ring) for ring in rings if len(ring) >= 3)
        checked += 1
        ratio = footprint / envelope
        if FOOTPRINT_OVER_ENVELOPE_MIN <= ratio <= FOOTPRINT_OVER_ENVELOPE_MAX:
            continue
        over = ratio > FOOTPRINT_OVER_ENVELOPE_MAX
        findings.append(Finding(
            rule="qty.floor_area_vs_envelope",
            severity="implausible" if over else "suspect",
            kind="floor", target=floor_target(floor),
            detail=(f"{floor.label} 构件占地 {footprint:.1f} m²，包络只有 {envelope:.1f} m²，"
                    f"比值 {ratio:.2f} —— 同一批构件被重复计了多遍" if over else
                    f"{floor.label} 构件占地 {footprint:.2f} m² 仅占包络 {envelope:.1f} m² 的 "
                    f"{ratio * 100:.2f}% —— 本层构件几乎全丢"),
            evidence={"footprint_over_envelope": round(ratio, 5),
                      "footprint_area_m2": round(footprint, 2),
                      "envelope_area_m2": round(envelope, 2),
                      "element_count": len(floor.elements),
                      "upper": FOOTPRINT_OVER_ENVELOPE_MAX,
                      "lower": FOOTPRINT_OVER_ENVELOPE_MIN},
            basis=formula_basis(
                "k = ΣA占地 / A包络；EMPIRICAL：构件本就压叠故 k>1 正常，"
                f"k>{FOOTPRINT_OVER_ENVELOPE_MAX} 只能是重复计数，"
                f"k<{FOOTPRINT_OVER_ENVELOPE_MIN} 只能是识别塌了。"
                "包络取全部占地顶点的凸包，凹形（L 形、口字形）楼层的包络"
                "会偏大、k 随之偏小 —— 对上限判据偏保守，对下限判据偏敏感，"
                "故下限只给 suspect",
                "geometry.convex_hull_monotone_chain", "geometry.shoelace_area")))
    if not checked:
        raise RuleNotApplicable("没有一层有占地构件（构件既无 outline 也无 path+width）")
    return findings


registry.register(Rule(
    id="qty.floor_area_vs_envelope", title="楼层占地与包络的比值",
    scope="floor", severity="implausible",
    basis="面积守恒（EMPIRICAL 量级阈值；"
          + formula_ref("geometry.convex_hull_monotone_chain",
                        "geometry.shoelace_area") + "）",
    check=floor_area_vs_envelope))
