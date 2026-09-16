"""几何族合理性规则的用例。

**这一族判的是「形状在数学上能不能成立」**，所以用例可以完全构造：
不需要图纸、不需要数据库，一个多边形就能把判据钉死。

两条规则对应的是**实测存在的真缺陷**，用例按实测形态构造：
- 自交轮廓（「蝴蝶结」）—— 存量 3166/10170 根柱因此混凝土量算成 0；
- 同层同类近乎完全重合 —— 总图与分图画同一区域，柱被算几遍。
"""
from __future__ import annotations

import time

import pytest

from core.model3d.plausibility import codes
from core.model3d.plausibility import rules_geometry as rg
from core.model3d.plausibility.model import Building, Element, Floor, PlausibilityModel
from core.model3d.plausibility.types import RuleNotApplicable


# ── 构造工具 ────────────────────────────────────────────────────────
# 直接拼 PlausibilityModel，不读库：判据是纯几何的，引入 IO 只会让失败原因变多。

def _element(kind: str, raw: dict, uid: str = "e0", floor_key: str = "F1") -> Element:
    return Element(uid=uid, kind=kind, building_key="B1", floor_key=floor_key, raw=raw)


def _model(*elements: Element, floor_key: str = "F1") -> PlausibilityModel:
    floor = Floor(key=floor_key, label=floor_key, order=0, building_key="B1",
                  elevation_m=0.0, elements=tuple(elements))
    return PlausibilityModel(buildings=(Building(key="B1", label="B1", floors=(floor,)),))


def _square(x: float, y: float, side: float = 1.0) -> list[tuple[float, float]]:
    return [(x, y), (x + side, y), (x + side, y + side), (x, y + side)]


def _run(rule_id: str, model: PlausibilityModel) -> list:
    return list(rg.RULES[rule_id].check(model))


# ── geom.self_intersecting_outline ─────────────────────────────────

@pytest.mark.unit
def test_自交轮廓被判为不可能():
    # Arrange：蝴蝶结 —— 两瓣符号相反，鞋带面积恰好相消为 0
    bowtie = [(0.0, 0.0), (1.0, 1.0), (1.0, 0.0), (0.0, 1.0)]
    model = _model(_element("columns", {"outline": bowtie}, uid="c1"))

    # Act
    findings = _run("geom.self_intersecting_outline", model)

    # Assert
    assert len(findings) == 1
    assert findings[0].severity == "impossible"
    assert findings[0].target == "c1"
    assert findings[0].evidence["area_m2"] == pytest.approx(0.0, abs=1e-9)
    assert findings[0].basis


@pytest.mark.unit
def test_普通矩形轮廓不报自交():
    model = _model(_element("columns", {"outline": _square(0.0, 0.0, 0.6)}, uid="c1"))

    assert _run("geom.self_intersecting_outline", model) == []


@pytest.mark.unit
def test_没有任何面状构件时自交规则报缺数据而不是返回空():
    # 只有线状构件（path），没有 outline —— 这条规则根本没得查
    model = _model(_element("walls", {"path": [(0.0, 0.0), (5.0, 0.0)], "width": 0.2}))

    with pytest.raises(RuleNotApplicable):
        _run("geom.self_intersecting_outline", model)


# ── geom.zero_area_with_extent ─────────────────────────────────────

@pytest.mark.unit
def test_回描轮廓面积为零但有两向跨度被判为不可能():
    # A→B→A→C：没有真正穿越（所以自交判据碰不到），但鞋带正负相消
    retrace = [(0.0, 0.0), (1.0, 1.0), (0.0, 0.0), (1.0, 0.0)]
    model = _model(_element("slabs", {"outline": retrace}, uid="s1"))

    findings = _run("geom.zero_area_with_extent", model)

    assert len(findings) == 1
    assert findings[0].severity == "impossible"
    assert findings[0].evidence["short_side_m"] > rg.DEGENERATE_SHORT_M


@pytest.mark.unit
def test_共线退化不由零面积规则报_留给退化规则():
    # 三点共线：面积确实为 0，但它是真退化，不是环序坏了
    model = _model(_element("slabs", {"outline": [(0.0, 0.0), (1.0, 1.0), (2.0, 2.0)]}))

    assert _run("geom.zero_area_with_extent", model) == []


@pytest.mark.unit
def test_正常板不报零面积():
    model = _model(_element("slabs", {"outline": _square(0.0, 0.0, 8.0)}))

    assert _run("geom.zero_area_with_extent", model) == []


# ── geom.degenerate_outline ────────────────────────────────────────

@pytest.mark.unit
def test_两点轮廓被判为退化():
    model = _model(_element("columns", {"outline": [(0.0, 0.0), (1.0, 0.0)]}, uid="c1"))

    findings = _run("geom.degenerate_outline", model)

    assert len(findings) == 1
    assert findings[0].severity == "impossible"
    assert findings[0].evidence["distinct_points"] == 2


@pytest.mark.unit
def test_全部共线的轮廓被判为退化():
    model = _model(_element("columns",
                            {"outline": [(0.0, 0.0), (1.0, 1.0), (2.0, 2.0), (3.0, 3.0)]},
                            uid="c1"))

    findings = _run("geom.degenerate_outline", model)

    assert len(findings) == 1
    assert findings[0].evidence["short_side_m"] == pytest.approx(0.0, abs=1e-9)


@pytest.mark.unit
def test_零长线段被判为退化():
    model = _model(_element("beams", {"path": [(3.0, 3.0), (3.0, 3.0)], "width": 0.3},
                            uid="b1"))

    findings = _run("geom.degenerate_outline", model)

    assert len(findings) == 1
    assert findings[0].evidence["length_m"] == pytest.approx(0.0)


@pytest.mark.unit
def test_正常构件不报退化():
    model = _model(_element("columns", {"outline": _square(0.0, 0.0, 0.6)}),
                   _element("beams", {"path": [(0.0, 0.0), (6.0, 0.0)], "width": 0.3},
                            uid="b1"))

    assert _run("geom.degenerate_outline", model) == []


@pytest.mark.unit
def test_既无轮廓也无路径时退化规则报缺数据():
    model = _model(_element("equipment", {"height": 1.2}))

    with pytest.raises(RuleNotApplicable):
        _run("geom.degenerate_outline", model)


# ── geom.non_positive_size ─────────────────────────────────────────

@pytest.mark.unit
def test_墙宽为零被判为不可能():
    model = _model(_element("walls", {"path": [(0.0, 0.0), (5.0, 0.0)], "width": 0.0},
                            uid="w1"))

    findings = _run("geom.non_positive_size", model)

    assert len(findings) == 1
    assert findings[0].severity == "impossible"
    assert findings[0].evidence["field"] == "width"
    assert findings[0].evidence["value"] == 0.0


@pytest.mark.unit
def test_负板厚被判为不可能():
    model = _model(_element("slabs", {"outline": _square(0, 0, 5), "thickness": -0.12},
                            uid="s1"))

    findings = _run("geom.non_positive_size", model)

    assert [f.evidence["value"] for f in findings] == [-0.12]


@pytest.mark.unit
def test_没有尺寸字段的构件被跳过而不是报错():
    # 柱只有 outline，没有 width/thickness/height —— 跳过这个构件，
    # 但同一模型里有别的构件带尺寸字段，规则仍然能跑
    model = _model(_element("columns", {"outline": _square(0, 0, 0.6)}, uid="c1"),
                   _element("walls", {"path": [(0, 0), (5, 0)], "width": 0.2}, uid="w1"))

    assert _run("geom.non_positive_size", model) == []


@pytest.mark.unit
def test_全模型无尺寸字段时报缺数据():
    model = _model(_element("columns", {"outline": _square(0, 0, 0.6)}))

    with pytest.raises(RuleNotApplicable):
        _run("geom.non_positive_size", model)


# ── geom.absurd_extent ─────────────────────────────────────────────

@pytest.mark.unit
def test_长边超出柱的工程上限被判为不合理():
    # 8m 长、0.5m 宽的「柱」—— 那是一道墙或一条带
    model = _model(_element("columns", {"outline": [(0.0, 0.0), (8.0, 0.0),
                                                    (8.0, 0.5), (0.0, 0.5)]}, uid="c1"))

    findings = _run("geom.absurd_extent", model)

    assert len(findings) == 1
    assert findings[0].severity == "implausible"
    assert findings[0].evidence["long_side_m"] == pytest.approx(8.0)
    assert findings[0].evidence["limit_m"] == codes.value("extent.max_by_kind_m")["columns"]


@pytest.mark.unit
def test_线状构件按路径长度量量级():
    long_beam = _element("beams",
                         {"path": [(0.0, 0.0), (codes.value("extent.max_by_kind_m")["beams"] + 10.0, 0.0)],
                          "width": 0.3}, uid="b1")

    findings = _run("geom.absurd_extent", _model(long_beam))

    assert len(findings) == 1
    assert findings[0].kind == "beams"


@pytest.mark.unit
def test_正常量级构件不报超限():
    model = _model(_element("columns", {"outline": _square(0, 0, 0.8)}),
                   _element("beams", {"path": [(0, 0), (9.0, 0)], "width": 0.3}, uid="b1"))

    assert _run("geom.absurd_extent", model) == []


# ── geom.low_solidity ──────────────────────────────────────────────

@pytest.mark.unit
def test_被撕成U形的轮廓实心度过低():
    # U 形：外廓 10×10 = 100，挖去 8×9 = 72 → 面积 28，凸包 100 → solidity 0.28
    u_shape = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (9.0, 10.0),
               (9.0, 1.0), (1.0, 1.0), (1.0, 10.0), (0.0, 10.0)]
    model = _model(_element("slabs", {"outline": u_shape}, uid="s1"))

    findings = _run("geom.low_solidity", model)

    assert len(findings) == 1
    assert findings[0].severity == "suspect"
    assert findings[0].evidence["solidity"] == pytest.approx(0.28, abs=0.01)


@pytest.mark.unit
def test_矩形轮廓实心度为一不报():
    model = _model(_element("columns", {"outline": _square(0, 0, 0.6)}))

    assert _run("geom.low_solidity", model) == []


# ── geom.duplicate_element ─────────────────────────────────────────

@pytest.mark.unit
def test_同层同类两个几乎完全重合的构件被判为重复():
    model = _model(_element("columns", {"outline": _square(0.0, 0.0, 0.6)}, uid="c1"),
                   _element("columns", {"outline": _square(0.01, 0.01, 0.6)}, uid="c2"))

    findings = _run("geom.duplicate_element", model)

    assert len(findings) == 1
    assert findings[0].severity == "implausible"
    assert findings[0].evidence["overlap_ratio"] > rg.DUPLICATE_OVERLAP_RATIO
    assert {findings[0].target, findings[0].evidence["duplicate_of"]} == {"c1", "c2"}


@pytest.mark.unit
def test_相邻不重叠的构件不算重复():
    model = _model(_element("columns", {"outline": _square(0.0, 0.0, 0.6)}, uid="c1"),
                   _element("columns", {"outline": _square(0.6, 0.0, 0.6)}, uid="c2"))

    assert _run("geom.duplicate_element", model) == []


@pytest.mark.unit
def test_不同类别的重合不算重复():
    """柱落在板上是正常的 —— 重复只在同层同类之间才有意义。

    模型里放两根**不重叠**的柱，好让这条规则确实有得比（否则它会正确地
    报「没有可比较的构件」，那测的就不是本条判据了）；板与其中一根柱完全重合。
    """
    model = _model(_element("columns", {"outline": _square(0.0, 0.0, 0.6)}, uid="c1"),
                   _element("columns", {"outline": _square(5.0, 0.0, 0.6)}, uid="c2"),
                   _element("slabs", {"outline": _square(0.0, 0.0, 0.6)}, uid="s1"))

    assert _run("geom.duplicate_element", model) == []


@pytest.mark.unit
def test_重复规则在三百个构件上不退化():
    # Arrange：20×15 的不重叠柱网，间距远大于自身边长
    elements = tuple(
        _element("columns", {"outline": _square(i * 5.0, j * 5.0, 0.6)}, uid=f"c{i}_{j}")
        for i in range(20) for j in range(15)
    )
    model = _model(*elements)

    # Act
    started = time.perf_counter()
    findings = _run("geom.duplicate_element", model)
    elapsed = time.perf_counter() - started

    # Assert：零结论，且不能因为两两比较把时间拖成秒级
    assert findings == []
    assert elapsed < 2.0, f"300 个构件耗时 {elapsed:.2f}s，两两比较没有被粗筛压住"


@pytest.mark.unit
def test_构件不足两个时重复规则报缺数据():
    model = _model(_element("columns", {"outline": _square(0, 0, 0.6)}))

    with pytest.raises(RuleNotApplicable):
        _run("geom.duplicate_element", model)


# ── 注册 ────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_几何族规则全部已注册且依据非空():
    from core.model3d.plausibility import registry

    registered = {r.id: r for r in registry.all_rules()}
    for rule_id, rule in rg.RULES.items():
        assert registered.get(rule_id) is rule
        assert rule.basis
