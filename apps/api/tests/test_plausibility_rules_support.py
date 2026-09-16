"""「重力与支承」族规则的单元测试。

**为什么测试先写**：这一族会给构件下 `impossible` 的判决，误判的代价是把
真构件说成不可能存在。每条规则的**命中**与**不命中**都必须先用最小样例钉死，
再去实现 —— 否则阈值会被实现反过来「凑」到能过。

测试数据全部手拼 `PlausibilityModel`，不碰数据库：合理性分析本身就该是
纯计算、零外部依赖的一道闸。
"""
from __future__ import annotations

import time
from dataclasses import replace

import pytest

from core.model3d.plausibility import formulas
from core.model3d.plausibility import rules_support as rs
from core.model3d.plausibility.model import Building, Element, Floor, PlausibilityModel
from core.model3d.plausibility.types import RuleNotApplicable

pytestmark = pytest.mark.unit


# ── 公式出处：两个方向都由用例自己造 ──────────────────────────────────
#
# 出处正由另一条工作线逐条填。把它当下的状态写进断言，这批用例就会随那条
# 工作线红/绿，而失败原因与被测逻辑无关 —— 所以「已取证」「未取证」各造一次。

@pytest.fixture
def cite(monkeypatch):
    def _cite(*keys: str) -> None:
        for key in keys:
            monkeypatch.setitem(
                formulas.FORMULAS, key,
                replace(formulas.formula(key), source="用例注入出处 p.1",
                        quote="（用例注入的原文抽取样）"))
    return _cite


@pytest.fixture
def uncite(monkeypatch):
    def _uncite(*keys: str) -> None:
        for key in keys:
            monkeypatch.setitem(
                formulas.FORMULAS, key,
                replace(formulas.formula(key),
                        source=f"{formulas.UNCITED}:用例注入", quote=""))
    return _uncite


# ── 构造辅助 ──────────────────────────────────────────────────────────
def _square(cx: float, cy: float, size: float) -> list[tuple[float, float]]:
    half = size / 2.0
    return [(cx - half, cy - half), (cx + half, cy - half),
            (cx + half, cy + half), (cx - half, cy + half)]


def _poly(kind: str, uid: str, cx: float, cy: float, size: float, **extra) -> Element:
    raw = {"outline": _square(cx, cy, size)}
    raw.update(extra)
    return Element(uid=uid, kind=kind, building_key="B", floor_key="F", raw=raw)


def _col(uid: str, cx: float, cy: float, size: float = 0.8) -> Element:
    return _poly("columns", uid, cx, cy, size)


def _equip(uid: str, cx: float, cy: float, size: float = 1.0) -> Element:
    return _poly("equipment", uid, cx, cy, size)


def _slab(uid: str, cx: float, cy: float, size: float, **extra) -> Element:
    return _poly("slabs", uid, cx, cy, size, thickness=0.12, **extra)


def _linear(kind: str, uid: str, a, b, width: float) -> Element:
    return Element(uid=uid, kind=kind, building_key="B", floor_key="F",
                   raw={"path": [list(a), list(b)], "width": width})


def _wall(uid: str, a, b, width: float = 0.2) -> Element:
    return _linear("walls", uid, a, b, width)


def _beam(uid: str, a, b, width: float = 0.3) -> Element:
    return _linear("beams", uid, a, b, width)


def _floor(key: str, order: int, elements, *, elevation=None,
           estimated: bool = True, height=None) -> Floor:
    return Floor(key=key, label=key, order=order, building_key="B",
                 elevation_m=elevation, elevation_estimated=estimated,
                 height_m=height, elements=tuple(elements))


def _model(*floors: Floor) -> PlausibilityModel:
    return PlausibilityModel(buildings=(Building(key="B", label="B", floors=tuple(floors)),))


def _ids(findings) -> list[str]:
    return [f.target for f in findings]


# ── support.floating_column ───────────────────────────────────────────
@pytest.mark.unit
def test_floating_column_reports_column_with_nothing_underneath(cite):
    # Arrange：一层四根柱，二层四根柱 —— 其中一根平移到 50 米外，下方空无一物
    cite(*rs._FLOATING_FORMULAS)
    below = _floor("F1", 0, [_col(f"c{i}", i * 8.0, 0.0) for i in range(4)])
    above = _floor("F2", 1, [_col("u0", 0.0, 0.0), _col("u1", 8.0, 0.0),
                             _col("u2", 16.0, 0.0), _col("u3", 50.0, 50.0)])

    # Act
    findings = list(rs.RULE_FLOATING_COLUMN.check(_model(below, above)))

    # Assert
    assert _ids(findings) == ["u3"]
    assert findings[0].severity == "impossible"
    assert findings[0].kind == "columns"
    assert findings[0].evidence["support_overlap_ratio"] == 0.0


@pytest.mark.unit
def test_floating_column_accepts_column_resting_on_wall_below():
    # Arrange：下层没有柱，但有一道墙正好穿过上层柱的位置
    below = _floor("F1", 0, [_wall("w0", (-10.0, 0.0), (10.0, 0.0), width=0.4),
                             _col("c0", 8.0, 0.0), _col("c1", -8.0, 0.0)])
    above = _floor("F2", 1, [_col("u0", 0.0, 0.0), _col("u1", 8.0, 0.0),
                             _col("u2", -8.0, 0.0)])

    # Act
    findings = list(rs.RULE_FLOATING_COLUMN.check(_model(below, above)))

    # Assert
    assert findings == []


@pytest.mark.unit
def test_floating_column_downgrades_to_suspect_when_whole_floor_is_discontinuous():
    # Arrange：二层 10 根柱全部错开半个柱网 —— 整层不连续，转换层的典型形态
    below = _floor("F1", 0, [_col(f"c{i}", i * 8.0, 0.0) for i in range(10)])
    above = _floor("F2", 1, [_col(f"u{i}", i * 8.0 + 4.0, 0.0) for i in range(10)])

    # Act
    findings = list(rs.RULE_FLOATING_COLUMN.check(_model(below, above)))

    # Assert：不刷 10 条 impossible，而是一条整层的 suspect
    assert len(findings) == 1
    assert findings[0].severity == "suspect"
    assert findings[0].kind == "floor"
    assert "转换层" in findings[0].detail
    assert findings[0].evidence["floating"] == 10
    assert findings[0].evidence["columns"] == 10


@pytest.mark.unit
def test_floating_column_still_impossible_when_only_a_minority_floats(cite):
    # Arrange：10 根柱里只有 2 根悬空 —— 低于转换层比例，不该被降级
    cite(*rs._FLOATING_FORMULAS)
    below = _floor("F1", 0, [_col(f"c{i}", i * 8.0, 0.0) for i in range(10)])
    above = _floor("F2", 1, [_col(f"u{i}", i * 8.0, 0.0) for i in range(8)]
                   + [_col("x0", 500.0, 500.0), _col("x1", 600.0, 600.0)])

    # Act
    findings = list(rs.RULE_FLOATING_COLUMN.check(_model(below, above)))

    # Assert
    assert sorted(_ids(findings)) == ["x0", "x1"]
    assert {f.severity for f in findings} == {"impossible"}


@pytest.mark.unit
def test_floating_column_degrades_to_implausible_when_formula_is_uncited(uncite):
    """凭一条找不到出处的公式说「不可能」，是把没有依据说成了最强的依据。"""
    # Arrange：只抹掉静力平衡这一条的出处
    uncite("mechanics.static_equilibrium")
    below = _floor("F1", 0, [_col(f"c{i}", i * 8.0, 0.0) for i in range(4)])
    above = _floor("F2", 1, [_col("u0", 0.0, 0.0), _col("u1", 8.0, 0.0),
                             _col("u2", 16.0, 0.0), _col("u3", 50.0, 50.0)])

    # Act
    findings = list(rs.RULE_FLOATING_COLUMN.check(_model(below, above)))

    # Assert
    assert _ids(findings) == ["u3"]
    assert findings[0].severity == "implausible"
    assert "公式出处待取证" in findings[0].basis
    assert "mechanics.static_equilibrium" in findings[0].basis


@pytest.mark.unit
def test_floating_column_basis_cites_static_equilibrium_with_its_conditions(cite):
    # Arrange
    cite(*rs._FLOATING_FORMULAS)
    below = _floor("F1", 0, [_col(f"c{i}", i * 8.0, 0.0) for i in range(4)])
    above = _floor("F2", 1, [_col("u0", 0.0, 0.0), _col("u1", 8.0, 0.0),
                             _col("u2", 16.0, 0.0), _col("u3", 50.0, 50.0)])

    # Act
    basis = list(rs.RULE_FLOATING_COLUMN.check(_model(below, above)))[0].basis

    # Assert：公式的表达式、来处、成立条件三样都要在场
    equilibrium = formulas.formula("mechanics.static_equilibrium")
    assert equilibrium.expression in basis
    assert equilibrium.source in basis
    assert equilibrium.conditions in basis


@pytest.mark.unit
def test_floating_column_not_applicable_when_building_has_single_floor():
    # Arrange：只有一层 —— 没有「下一层」可查，不是「查过了没问题」
    model = _model(_floor("F1", 0, [_col("c0", 0.0, 0.0)]))

    # Act & Assert
    with pytest.raises(RuleNotApplicable):
        list(rs.RULE_FLOATING_COLUMN.check(model))


# ── support.beam_without_support ──────────────────────────────────────
@pytest.mark.unit
def test_beam_without_support_reports_beam_floating_between_nothing():
    # Arrange：一根梁两端 50 米内都没有柱或墙
    floor = _floor("F1", 0, [_col("c0", 0.0, 0.0), _col("c1", 8.0, 0.0),
                             _beam("b_ok", (0.0, 0.0), (8.0, 0.0)),
                             _beam("b_bad", (100.0, 100.0), (108.0, 100.0))])

    # Act
    findings = list(rs.RULE_BEAM_WITHOUT_SUPPORT.check(_model(floor)))

    # Assert
    assert _ids(findings) == ["b_bad"]
    assert findings[0].severity == "implausible"
    assert findings[0].evidence["search_radius_m"] == rs.SUPPORT_SEARCH_RADIUS_M


@pytest.mark.unit
def test_beam_with_one_end_on_a_column_is_accepted_as_cantilever():
    # Arrange：一端搭在柱上、另一端悬空 —— 悬挑梁合法，不该报
    floor = _floor("F1", 0, [_col("c0", 0.0, 0.0),
                             _beam("b0", (0.0, 0.0), (3.0, 0.0))])

    # Act
    findings = list(rs.RULE_BEAM_WITHOUT_SUPPORT.check(_model(floor)))

    # Assert
    assert findings == []


@pytest.mark.unit
def test_beam_rule_not_applicable_without_any_beam_path():
    # Arrange：有柱没梁
    floor = _floor("F1", 0, [_col("c0", 0.0, 0.0)])

    # Act & Assert
    with pytest.raises(RuleNotApplicable):
        list(rs.RULE_BEAM_WITHOUT_SUPPORT.check(_model(floor)))


# ── support.beam_support_count ────────────────────────────────────────
@pytest.mark.unit
def test_beam_support_count_reports_beam_with_only_one_end_supported():
    # Arrange：梁起端搭在柱上，终端 1 米内什么都没有
    floor = _floor("F1", 0, [_col("c0", 0.0, 0.0),
                             _beam("b0", (0.0, 0.0), (6.0, 0.0))])

    # Act
    findings = list(rs.RULE_BEAM_SUPPORT_COUNT.check(_model(floor)))

    # Assert：只存疑不否定 —— 悬挑梁合法，嵌固端在平面图上看不出来
    assert _ids(findings) == ["b0"]
    assert findings[0].severity == "suspect"
    assert findings[0].evidence["supported_ends"] == 1
    assert findings[0].evidence["required_ends"] == 2
    assert "悬挑" in findings[0].detail and "缺支座" in findings[0].detail


@pytest.mark.unit
def test_beam_support_count_accepts_beam_supported_at_both_ends():
    # Arrange：两端各有一根柱 —— 简支，静定
    floor = _floor("F1", 0, [_col("c0", 0.0, 0.0), _col("c1", 8.0, 0.0),
                             _beam("b0", (0.0, 0.0), (8.0, 0.0))])

    # Act & Assert
    assert list(rs.RULE_BEAM_SUPPORT_COUNT.check(_model(floor))) == []


@pytest.mark.unit
def test_beam_support_count_leaves_fully_unsupported_beam_to_the_other_rule():
    """两端皆无支承是 `support.beam_without_support` 的射程。

    两条规则都报，报告里同一根梁就有两条「独立证据」，而它们其实是同一件事。
    """
    # Arrange
    floor = _floor("F1", 0, [_col("c0", 0.0, 0.0), _col("c1", 8.0, 0.0),
                             _beam("b_bad", (100.0, 100.0), (108.0, 100.0))])

    # Act
    counted = list(rs.RULE_BEAM_SUPPORT_COUNT.check(_model(floor)))
    without = list(rs.RULE_BEAM_WITHOUT_SUPPORT.check(_model(floor)))

    # Assert
    assert counted == []
    assert _ids(without) == ["b_bad"]


@pytest.mark.unit
def test_beam_support_count_basis_cites_static_equilibrium(cite):
    # Arrange
    cite(*rs._BEAM_FORMULAS)
    floor = _floor("F1", 0, [_col("c0", 0.0, 0.0),
                             _beam("b0", (0.0, 0.0), (6.0, 0.0))])

    # Act
    basis = list(rs.RULE_BEAM_SUPPORT_COUNT.check(_model(floor)))[0].basis

    # Assert
    equilibrium = formulas.formula("mechanics.static_equilibrium")
    assert equilibrium.expression in basis
    assert equilibrium.source in basis
    assert "两个支座" in basis


@pytest.mark.unit
def test_beam_support_count_not_applicable_without_any_beam_path():
    # Arrange：有柱没梁 —— 缺数据不是「查过了没问题」
    floor = _floor("F1", 0, [_col("c0", 0.0, 0.0)])

    # Act & Assert
    with pytest.raises(RuleNotApplicable):
        list(rs.RULE_BEAM_SUPPORT_COUNT.check(_model(floor)))


# ── support.slab_without_edge_support ─────────────────────────────────
@pytest.mark.unit
def test_slab_without_edge_support_reports_slab_hanging_in_the_air():
    # Arrange：一块 10×10 的板，四周 100 米内没有梁/墙/柱
    floor = _floor("F1", 0, [_slab("s_bad", 200.0, 200.0, 10.0),
                             _col("c0", 0.0, 0.0), _col("c1", 10.0, 0.0),
                             _col("c2", 10.0, 10.0), _col("c3", 0.0, 10.0),
                             _slab("s_ok", 5.0, 5.0, 10.0)])

    # Act
    findings = list(rs.RULE_SLAB_WITHOUT_EDGE_SUPPORT.check(_model(floor)))

    # Assert：角点落在柱上的那块不报，孤板报
    assert _ids(findings) == ["s_bad"]
    assert findings[0].severity == "implausible"
    assert findings[0].evidence["supported_samples"] == 0


@pytest.mark.unit
def test_slab_resting_on_edge_walls_is_accepted():
    # Arrange：板四边都有墙
    ring = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]
    walls = [_wall(f"w{i}", ring[i], ring[(i + 1) % 4]) for i in range(4)]
    floor = _floor("F1", 0, [*walls, _slab("s0", 5.0, 5.0, 10.0)])

    # Act
    findings = list(rs.RULE_SLAB_WITHOUT_EDGE_SUPPORT.check(_model(floor)))

    # Assert
    assert findings == []


@pytest.mark.unit
def test_slab_rule_not_applicable_without_slabs():
    with pytest.raises(RuleNotApplicable):
        list(rs.RULE_SLAB_WITHOUT_EDGE_SUPPORT.check(
            _model(_floor("F1", 0, [_col("c0", 0.0, 0.0)]))))


# ── support.interpenetration ──────────────────────────────────────────
@pytest.mark.unit
def test_interpenetration_reports_equipment_sharing_a_column_footprint():
    # Arrange：设备与柱占同一块楼面
    floor = _floor("F1", 0, [_col("c0", 0.0, 0.0, 0.8), _equip("e0", 0.0, 0.0, 1.0),
                             _col("c1", 20.0, 20.0), _equip("e1", 30.0, 30.0)])

    # Act
    findings = list(rs.RULE_INTERPENETRATION.check(_model(floor)))

    # Assert
    assert len(findings) == 1
    assert findings[0].severity == "implausible"
    assert set(findings[0].evidence["pair"]) == {"c0", "e0"}
    assert findings[0].evidence["overlap_ratio"] > rs.INTERPENETRATION_MIN_RATIO


@pytest.mark.unit
def test_interpenetration_basis_says_convex_clipping_overestimates_overlap():
    """Sutherland–Hodgman 只对凸裁剪多边形成立，实现里先取凸包再裁。

    高估会让重叠比偏大，所以阈值必须定在明显的量级上 —— 这层关系不写进依据，
    读报告的人就会拿它当精确面积用。
    """
    # Arrange
    floor = _floor("F1", 0, [_col("c0", 0.0, 0.0, 0.8), _equip("e0", 0.0, 0.0, 1.0)])

    # Act
    basis = list(rs.RULE_INTERPENETRATION.check(_model(floor)))[0].basis

    # Assert
    clipping = formulas.formula("geometry.polygon_clipping_convex_requirement")
    assert clipping.expression in basis
    assert clipping.conditions in basis
    assert "高估" in basis


@pytest.mark.unit
def test_interpenetration_ignores_same_kind_overlap():
    # Arrange：两根柱完全重叠 —— 同类重复由 geom.duplicate_element 负责，这里不能抢
    floor = _floor("F1", 0, [_col("c0", 0.0, 0.0), _col("c1", 0.1, 0.0),
                             _equip("e0", 20.0, 20.0)])

    # Act
    findings = list(rs.RULE_INTERPENETRATION.check(_model(floor)))

    # Assert
    assert findings == []


@pytest.mark.unit
def test_interpenetration_not_applicable_without_comparable_pair():
    # Arrange：整层只有柱，白名单里的跨类对一个都凑不出
    with pytest.raises(RuleNotApplicable):
        list(rs.RULE_INTERPENETRATION.check(
            _model(_floor("F1", 0, [_col("c0", 0.0, 0.0), _col("c1", 8.0, 0.0)]))))


# ── support.isolated_element ──────────────────────────────────────────
def _grid_columns(n_x: int, n_y: int, step: float = 8.0) -> list[Element]:
    return [_col(f"g{i}_{j}", i * step, j * step)
            for i in range(n_x) for j in range(n_y)]


@pytest.mark.unit
def test_isolated_element_reports_the_drawing_that_blows_up_the_envelope():
    # Arrange：规整柱网 + 一根落在 5 公里外的柱（实测过的离群图形态）
    floor = _floor("F1", 0, [*_grid_columns(4, 4), _col("far", 5000.0, 5000.0)])

    # Act
    findings = list(rs.RULE_ISOLATED_ELEMENT.check(_model(floor)))

    # Assert
    assert _ids(findings) == ["far"]
    assert findings[0].severity == "suspect"
    assert findings[0].evidence["nearest_neighbor_m"] > findings[0].evidence["threshold_m"]


@pytest.mark.unit
def test_isolated_element_accepts_a_regular_grid():
    # Arrange：只有规整柱网，没有离群
    floor = _floor("F1", 0, _grid_columns(4, 4))

    # Act
    findings = list(rs.RULE_ISOLATED_ELEMENT.check(_model(floor)))

    # Assert
    assert findings == []


@pytest.mark.unit
def test_isolated_element_respects_the_absolute_floor_on_dense_layouts():
    # Arrange：密排构件（间距 0.5 m，如座椅阵列）的中位数间距极小，
    # 10 倍也才 5 m —— 若没有绝对下限，40 米外的正常构件就会被判离群。
    dense = [_col(f"d{i}", i * 0.5, 0.0, 0.3) for i in range(12)]
    floor = _floor("F1", 0, [*dense, _col("apart", 45.0, 0.0, 0.3)])

    # Act
    findings = list(rs.RULE_ISOLATED_ELEMENT.check(_model(floor)))

    # Assert：40 米在一栋楼里是正常距离，下限（50 m）正是为此设的
    assert findings == []


@pytest.mark.unit
def test_isolated_element_not_applicable_on_too_few_elements():
    # Arrange：三个构件 —— 「主群」无从谈起，分布判据没有统计意义
    floor = _floor("F1", 0, [_col("c0", 0.0, 0.0), _col("c1", 8.0, 0.0),
                             _col("c2", 5000.0, 0.0)])

    # Act & Assert
    with pytest.raises(RuleNotApplicable):
        list(rs.RULE_ISOLATED_ELEMENT.check(_model(floor)))


# ── support.story_z_overlap ───────────────────────────────────────────
@pytest.mark.unit
def test_story_z_overlap_reports_lower_floor_top_above_upper_floor_bottom():
    # Arrange：一层底 0.0 层高 5.0（顶 5.0），二层底 4.0 —— 1 米被两层同时占用
    lower = _floor("F1", 0, [], elevation=0.0, estimated=False, height=5.0)
    upper = _floor("F2", 1, [], elevation=4.0, estimated=False, height=4.0)

    # Act
    findings = list(rs.RULE_STORY_Z_OVERLAP.check(_model(lower, upper)))

    # Assert
    assert len(findings) == 1
    assert findings[0].severity == "impossible"
    assert findings[0].kind == "floor"
    assert findings[0].evidence["overlap_m"] == pytest.approx(1.0)


@pytest.mark.unit
def test_story_z_overlap_reports_non_monotonic_elevation():
    # Arrange：楼层序号递增而标高反降
    lower = _floor("F1", 0, [], elevation=10.0, estimated=False, height=4.0)
    upper = _floor("F2", 1, [], elevation=3.0, estimated=False, height=4.0)

    # Act
    findings = list(rs.RULE_STORY_Z_OVERLAP.check(_model(lower, upper)))

    # Assert
    assert len(findings) == 1
    assert findings[0].evidence["elevation_delta_m"] == pytest.approx(-7.0)


@pytest.mark.unit
def test_story_z_overlap_accepts_stacked_floors():
    # Arrange：一层 0.0 + 4.5，二层 4.5 —— 正好首尾相接
    lower = _floor("F1", 0, [], elevation=0.0, estimated=False, height=4.5)
    upper = _floor("F2", 1, [], elevation=4.5, estimated=False, height=4.2)

    # Act
    findings = list(rs.RULE_STORY_Z_OVERLAP.check(_model(lower, upper)))

    # Assert
    assert findings == []


@pytest.mark.unit
def test_story_z_overlap_not_applicable_when_elevation_is_estimated():
    # Arrange：标高是估出来的 —— 不能拿估值去否定楼层，必须降级可见
    lower = _floor("F1", 0, [], elevation=0.0, estimated=True, height=5.0)
    upper = _floor("F2", 1, [], elevation=4.0, estimated=True, height=4.0)

    # Act & Assert
    with pytest.raises(RuleNotApplicable) as excinfo:
        list(rs.RULE_STORY_Z_OVERLAP.check(_model(lower, upper)))
    assert "估" in str(excinfo.value)


@pytest.mark.unit
def test_story_z_overlap_not_applicable_when_elevation_missing():
    lower = _floor("F1", 0, [], elevation=None, estimated=False, height=5.0)
    upper = _floor("F2", 1, [], elevation=4.0, estimated=False, height=4.0)

    with pytest.raises(RuleNotApplicable):
        list(rs.RULE_STORY_Z_OVERLAP.check(_model(lower, upper)))


# ── 规模 ──────────────────────────────────────────────────────────────
@pytest.mark.unit
def test_rules_do_not_degrade_on_a_thousand_element_floor():
    # Arrange：上下各 600 根柱（25×24 柱网，8 米跨），上层一根被挪到 10 公里外
    positions = [(i * 8.0, j * 8.0) for i in range(25) for j in range(24)]
    below = _floor("F1", 0, [_col(f"c{k}", x, y) for k, (x, y) in enumerate(positions)])
    upper_pos = list(positions)
    upper_pos[-1] = (10000.0, 10000.0)
    above = _floor("F2", 1, [_col(f"u{k}", x, y) for k, (x, y) in enumerate(upper_pos)])
    model = _model(below, above)
    assert len(positions) == 600

    # Act
    start = time.perf_counter()
    floating = list(rs.RULE_FLOATING_COLUMN.check(model))
    isolated = list(rs.RULE_ISOLATED_ELEMENT.check(model))
    elapsed = time.perf_counter() - start

    # Assert：结论正确，且 1200 构件不退化成两两比较
    assert _ids(floating) == ["u599"]
    assert _ids(isolated) == ["u599"]
    assert elapsed < 15.0, f"1200 构件跑了 {elapsed:.1f}s —— 粗筛没生效"


# ── 注册 ──────────────────────────────────────────────────────────────
@pytest.mark.unit
def test_all_support_rules_are_registered_with_a_basis():
    # Arrange & Act
    ids = {rule.id for rule in rs.RULES}

    # Assert
    assert ids == {"support.floating_column", "support.beam_without_support",
                   "support.beam_support_count",
                   "support.slab_without_edge_support", "support.interpenetration",
                   "support.isolated_element", "support.story_z_overlap"}
    assert all(rule.basis for rule in rs.RULES)
    assert all(rule.scope in ("element", "floor", "model") for rule in rs.RULES)


@pytest.mark.unit
def test_every_finding_carries_numeric_evidence_and_a_basis():
    # Arrange：一个同时触发多条规则的模型
    below = _floor("F1", 0, [_col(f"c{i}", i * 8.0, 0.0) for i in range(4)],
                   elevation=0.0, estimated=False, height=9.0)
    above = _floor("F2", 1, [*(_col(f"u{i}", i * 8.0, 0.0) for i in range(4)),
                             _col("x", 900.0, 900.0), _equip("e", 900.0, 900.0),
                             _beam("b", (700.0, 0.0), (707.0, 0.0)),
                             _slab("s", 800.0, 800.0, 6.0),
                             *_grid_columns(3, 3)],
                  elevation=4.0, estimated=False, height=4.0)
    model = _model(below, above)

    # Act
    findings = [f for rule in rs.RULES for f in rule.check(model)]

    # Assert
    assert findings
    for finding in findings:
        assert finding.basis
        assert finding.evidence
        assert all(isinstance(v, (int, float, str, list, tuple))
                   for v in finding.evidence.values())
