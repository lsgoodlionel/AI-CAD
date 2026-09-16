"""尺寸族合理性规则的用例 —— 「这个尺寸低于规范下限吗」。

这一族**依赖 `codes.LIMITS` 的取证状态**，所以用例必须锁住三件事：
1. 限值在 `check` 运行时读，不是在 import 时读（取证还在并行进行中）；
2. 限值未取证（`verified` 为 False）时**仍然出结论**，但降到 `suspect`；
3. 限值单位是毫米、构件坐标是米 —— 换算不能搞错。

用 `monkeypatch.setitem` 往限值表里塞值：既避免依赖取证进度，
也顺带证明了第 1 条（模块早在 patch 之前就 import 完了）。
"""
from __future__ import annotations

import pytest

from core.model3d.plausibility import codes
from core.model3d.plausibility import rules_dimension as rd
from core.model3d.plausibility.model import Building, Element, Floor, PlausibilityModel
from core.model3d.plausibility.types import RuleNotApplicable


# ── 构造工具 ────────────────────────────────────────────────────────

@pytest.fixture
def set_limit(monkeypatch):
    """往限值表里塞一条限值。`verified=False` 模拟「还没取证」。"""
    def _set(key: str, value, *, unit: str = "mm", verified: bool = True):
        original = codes.LIMITS[key]
        limit = codes.Limit(
            key=key, value=value, unit=unit,
            source="GB 50010-2010 §9.9.9" if verified else f"{codes.UNVERIFIED}:待取证",
            text="（用例注入的原文占位）" if verified else "",
            note=original.note)
        assert limit.verified is verified
        monkeypatch.setitem(codes.LIMITS, key, limit)
        return limit
    return _set


def _element(kind: str, raw: dict, uid: str = "e0") -> Element:
    return Element(uid=uid, kind=kind, building_key="B1", floor_key="F1", raw=raw)


def _square(side: float, x: float = 0.0, y: float = 0.0):
    return [(x, y), (x + side, y), (x + side, y + side), (x, y + side)]


def _rect(long_m: float, short_m: float):
    return [(0.0, 0.0), (long_m, 0.0), (long_m, short_m), (0.0, short_m)]


def _model(*elements: Element) -> PlausibilityModel:
    floor = Floor(key="F1", label="F1", order=0, building_key="B1",
                  elevation_m=0.0, elements=tuple(elements))
    return PlausibilityModel(buildings=(Building(key="B1", label="B1", floors=(floor,)),))


def _floors_model(*heights, estimated: bool = True) -> PlausibilityModel:
    floors = tuple(
        Floor(key=f"F{i + 1}", label=f"F{i + 1}", order=i, building_key="B1",
              elevation_m=float(i * 3), elevation_estimated=estimated, height_m=h)
        for i, h in enumerate(heights)
    )
    return PlausibilityModel(buildings=(Building(key="B1", label="B1", floors=floors),))


def _run(rule_id: str, model: PlausibilityModel) -> list:
    return list(rd.RULES[rule_id].check(model))


# ── dim.column_section_below_code ──────────────────────────────────

@pytest.mark.unit
def test_柱短边低于规范下限被判为不合理(set_limit):
    set_limit("column.min_section_mm", 300.0)
    model = _model(_element("columns", {"outline": _rect(0.6, 0.18)}, uid="c1"))

    findings = _run("dim.column_section_below_code", model)

    assert len(findings) == 1
    assert findings[0].severity == "implausible"
    assert findings[0].evidence["short_side_mm"] == pytest.approx(180.0)
    assert findings[0].evidence["limit_mm"] == pytest.approx(300.0)


@pytest.mark.unit
def test_柱短边达标不报(set_limit):
    set_limit("column.min_section_mm", 300.0)
    model = _model(_element("columns", {"outline": _square(0.4)}, uid="c1"))

    assert _run("dim.column_section_below_code", model) == []


@pytest.mark.unit
def test_柱截面判据的毫米米换算被锁住(set_limit):
    """限值 300mm = 0.3m：0.299m 必须命中，0.301m 必须不命中。

    换算写反（把 0.3 当 300）会让这两条同时失败，所以这一对用例
    比任何单边断言都更能钉死方向。
    """
    set_limit("column.min_section_mm", 300.0)

    hit = _run("dim.column_section_below_code",
               _model(_element("columns", {"outline": _rect(0.6, 0.299)}, uid="c1")))
    miss = _run("dim.column_section_below_code",
                _model(_element("columns", {"outline": _rect(0.6, 0.301)}, uid="c2")))

    assert len(hit) == 1
    assert miss == []


@pytest.mark.unit
def test_限值未取证时柱截面结论降级为suspect(set_limit):
    set_limit("column.min_section_mm", 300.0, verified=False)
    model = _model(_element("columns", {"outline": _rect(0.6, 0.18)}, uid="c1"))

    findings = _run("dim.column_section_below_code", model)

    assert len(findings) == 1
    assert findings[0].severity == "suspect"
    assert "待取证" in findings[0].basis


@pytest.mark.unit
def test_限值还是占位零时报缺数据而不是判全体合格(set_limit):
    set_limit("column.min_section_mm", 0.0, verified=False)
    model = _model(_element("columns", {"outline": _rect(0.6, 0.18)}, uid="c1"))

    with pytest.raises(RuleNotApplicable):
        _run("dim.column_section_below_code", model)


@pytest.mark.unit
def test_模型里没有柱时报缺数据(set_limit):
    set_limit("column.min_section_mm", 300.0)
    model = _model(_element("slabs", {"outline": _square(5.0), "thickness": 0.12}))

    with pytest.raises(RuleNotApplicable):
        _run("dim.column_section_below_code", model)


# ── dim.beam_width_below_code ──────────────────────────────────────

@pytest.mark.unit
def test_梁宽低于规范下限被判为不合理(set_limit):
    set_limit("beam.min_width_mm", 200.0)
    model = _model(_element("beams", {"path": [(0, 0), (6, 0)], "width": 0.15}, uid="b1"))

    findings = _run("dim.beam_width_below_code", model)

    assert len(findings) == 1
    assert findings[0].evidence["width_mm"] == pytest.approx(150.0)


@pytest.mark.unit
def test_梁宽达标不报(set_limit):
    set_limit("beam.min_width_mm", 200.0)
    model = _model(_element("beams", {"path": [(0, 0), (6, 0)], "width": 0.3}, uid="b1"))

    assert _run("dim.beam_width_below_code", model) == []


@pytest.mark.unit
def test_梁没有宽度字段时报缺数据(set_limit):
    set_limit("beam.min_width_mm", 200.0)
    model = _model(_element("beams", {"path": [(0, 0), (6, 0)]}, uid="b1"))

    with pytest.raises(RuleNotApplicable):
        _run("dim.beam_width_below_code", model)


# ── dim.slab_thickness_below_code ──────────────────────────────────

@pytest.mark.unit
def test_板厚低于规范下限被判为不合理(set_limit):
    set_limit("slab.min_thickness_mm", 80.0)
    model = _model(_element("slabs", {"outline": _square(5.0), "thickness": 0.06},
                            uid="s1"))

    findings = _run("dim.slab_thickness_below_code", model)

    assert len(findings) == 1
    assert findings[0].evidence["thickness_mm"] == pytest.approx(60.0)


@pytest.mark.unit
def test_板厚达标不报(set_limit):
    set_limit("slab.min_thickness_mm", 80.0)
    model = _model(_element("slabs", {"outline": _square(5.0), "thickness": 0.12}))

    assert _run("dim.slab_thickness_below_code", model) == []


@pytest.mark.unit
def test_限值未取证时板厚结论降级为suspect(set_limit):
    set_limit("slab.min_thickness_mm", 80.0, verified=False)
    model = _model(_element("slabs", {"outline": _square(5.0), "thickness": 0.06}))

    assert _run("dim.slab_thickness_below_code", model)[0].severity == "suspect"


# ── dim.wall_thickness_below_code ──────────────────────────────────

@pytest.mark.unit
def test_墙厚低于规范下限被判为不合理(set_limit):
    set_limit("wall.min_thickness_mm", 160.0)
    model = _model(_element("walls", {"path": [(0, 0), (6, 0)], "width": 0.10},
                            uid="w1"))

    findings = _run("dim.wall_thickness_below_code", model)

    assert len(findings) == 1
    assert findings[0].evidence["thickness_mm"] == pytest.approx(100.0)


@pytest.mark.unit
def test_墙厚达标不报(set_limit):
    set_limit("wall.min_thickness_mm", 160.0)
    model = _model(_element("walls", {"path": [(0, 0), (6, 0)], "width": 0.2}))

    assert _run("dim.wall_thickness_below_code", model) == []


# ── dim.story_height_out_of_range ──────────────────────────────────

@pytest.mark.unit
def test_层高超出合理区间被判为不合理(set_limit):
    set_limit("story.height_range_m", (2.2, 12.0), unit="m")
    model = _floors_model(3.0, 45.0)

    findings = _run("dim.story_height_out_of_range", model)

    assert len(findings) == 1
    assert findings[0].kind == "floor"
    assert findings[0].target == "B1/F2"
    assert findings[0].evidence["height_m"] == pytest.approx(45.0)
    assert findings[0].evidence["max_m"] == pytest.approx(12.0)


@pytest.mark.unit
def test_层高低于合理区间也被判为不合理(set_limit):
    set_limit("story.height_range_m", (2.2, 12.0), unit="m")

    findings = _run("dim.story_height_out_of_range", _floors_model(1.5, 3.0))

    assert len(findings) == 1
    assert findings[0].evidence["min_m"] == pytest.approx(2.2)


@pytest.mark.unit
def test_层高在区间内不报(set_limit):
    set_limit("story.height_range_m", (2.2, 12.0), unit="m")

    assert _run("dim.story_height_out_of_range", _floors_model(3.0, 4.5, 4.2)) == []


@pytest.mark.unit
def test_层高结论里带着标高是否估值(set_limit):
    """标高是估出来的，层高就是估出来的 —— 结论必须把这件事摆在证据里。"""
    set_limit("story.height_range_m", (2.2, 12.0), unit="m")

    findings = _run("dim.story_height_out_of_range",
                    _floors_model(45.0, 3.0, estimated=True))

    assert findings[0].evidence["elevation_estimated"] is True


@pytest.mark.unit
def test_所有楼层都算不出层高时报缺数据(set_limit):
    set_limit("story.height_range_m", (2.2, 12.0), unit="m")

    with pytest.raises(RuleNotApplicable):
        _run("dim.story_height_out_of_range", _floors_model(None, None))


@pytest.mark.unit
def test_层高区间还是占位时报缺数据(set_limit):
    set_limit("story.height_range_m", (0.0, 0.0), unit="m", verified=False)

    with pytest.raises(RuleNotApplicable):
        _run("dim.story_height_out_of_range", _floors_model(3.0, 4.5))


# ── dim.non_positive_story_height ──────────────────────────────────

@pytest.mark.unit
def test_层高为负被判为物理不可能():
    findings = _run("dim.non_positive_story_height", _floors_model(3.0, -0.5))

    assert len(findings) == 1
    assert findings[0].severity == "impossible"
    assert findings[0].evidence["height_m"] == pytest.approx(-0.5)


@pytest.mark.unit
def test_层高为零也被判为物理不可能():
    assert len(_run("dim.non_positive_story_height", _floors_model(0.0, 3.0))) == 1


@pytest.mark.unit
def test_层高为正不报物理不可能():
    assert _run("dim.non_positive_story_height", _floors_model(3.0, 4.5)) == []


@pytest.mark.unit
def test_没有任何层高时物理判据报缺数据():
    with pytest.raises(RuleNotApplicable):
        _run("dim.non_positive_story_height", _floors_model(None, None))


# ── dim.column_aspect_ratio ────────────────────────────────────────

@pytest.mark.unit
def test_长宽比超过四的柱被判为其实是墙():
    model = _model(_element("columns", {"outline": _rect(2.5, 0.2)}, uid="c1"))

    findings = _run("dim.column_aspect_ratio", model)

    assert len(findings) == 1
    assert findings[0].severity == "implausible"
    assert findings[0].evidence["aspect_ratio"] == pytest.approx(12.5)
    assert findings[0].evidence["limit"] == rd.COLUMN_MAX_ASPECT


@pytest.mark.unit
def test_方柱长宽比为一不报():
    model = _model(_element("columns", {"outline": _square(0.6)}, uid="c1"))

    assert _run("dim.column_aspect_ratio", model) == []


@pytest.mark.unit
def test_长宽比刚好等于阈值不报():
    """阈值是「超过」不是「达到」—— 400×1600 的柱正好 4:1，仍是柱。"""
    model = _model(_element("columns", {"outline": _rect(1.6, 0.4)}, uid="c1"))

    assert _run("dim.column_aspect_ratio", model) == []


@pytest.mark.unit
def test_没有柱时长宽比规则报缺数据():
    model = _model(_element("walls", {"path": [(0, 0), (6, 0)], "width": 0.2}))

    with pytest.raises(RuleNotApplicable):
        _run("dim.column_aspect_ratio", model)


# ── 注册 ────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_尺寸族规则全部已注册且依据非空():
    from core.model3d.plausibility import registry

    registered = {r.id: r for r in registry.all_rules()}
    for rule_id, rule in rd.RULES.items():
        assert registered.get(rule_id) is rule
        assert rule.basis
