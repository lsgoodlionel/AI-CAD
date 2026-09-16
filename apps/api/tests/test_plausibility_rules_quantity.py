"""量纲与量级守恒规则的单元测试。

最重要的一条是**回归测试** `test_混凝土用量_图框被当成楼板时被抓到`：
实测出现过兜底板把图框当楼板、折算方量 519,684 m³（真实量级在几万）。
这条规则存在的理由就是那次事故，所以它的用例按那次事故的形状写。
"""
from __future__ import annotations

import pytest

from core.model3d.plausibility import codes
from core.model3d.plausibility import rules_quantity as rq
from core.model3d.plausibility.model import Building, Element, Floor, PlausibilityModel
from core.model3d.plausibility.types import RuleNotApplicable

# ── 造模型的小工具 ──────────────────────────────────────────────────


def _rect(cx: float, cy: float, width: float, depth: float) -> list[tuple[float, float]]:
    return [(cx - width / 2, cy - depth / 2), (cx + width / 2, cy - depth / 2),
            (cx + width / 2, cy + depth / 2), (cx - width / 2, cy + depth / 2)]


def _column(uid: str, cx: float, cy: float, side: float) -> Element:
    return Element(uid=uid, kind="columns", building_key="b1", floor_key="f1",
                   raw={"outline": _rect(cx, cy, side, side)})


def _slab(uid: str, width: float, depth: float, thickness: float, basis: str = "",
          origin: tuple[float, float] = (0.0, 0.0)) -> Element:
    raw: dict = {"outline": _rect(origin[0] + width / 2, origin[1] + depth / 2, width, depth),
                 "thickness": thickness}
    if basis:
        raw["basis"] = basis
    return Element(uid=uid, kind="slabs", building_key="b1", floor_key="f1", raw=raw)


def _wall(uid: str, length: float, width: float, y: float = 0.0) -> Element:
    return Element(uid=uid, kind="walls", building_key="b1", floor_key="f1",
                   raw={"path": [(0.0, y), (length, y)], "width": width})


def _floor(*elements: Element, key: str = "f1", height_m: float | None = 3.0) -> Floor:
    return Floor(key=key, label=key, order=0, building_key="b1", elevation_m=0.0,
                 elevation_estimated=False, height_m=height_m, elements=tuple(elements))


def _model(*floors: Floor, meta: dict | None = None) -> PlausibilityModel:
    return PlausibilityModel(buildings=(Building(key="b1", label="主楼", floors=tuple(floors)),),
                             meta=meta or {})


def _normal_floor(**kwargs) -> Floor:
    """一层常规混凝土框架：板 400 m²×0.20 + 16 根 500 柱 + 80 m 长 200 厚墙。

    折算 (80 + 12 + 48) / 400 = 0.35 m³/m²，落在常规区间内。
    """
    columns = [_column(f"c{i}", 1.0 + i, 1.0, 0.5) for i in range(16)]
    walls = [_wall(f"w{i}", 20.0, 0.2, y=float(i) * 5.0) for i in range(4)]
    return _floor(_slab("s1", 20.0, 20.0, 0.20), *columns, *walls, **kwargs)


# ── 限值注入 ────────────────────────────────────────────────────────

def _verified(key: str, value, unit: str) -> codes.Limit:
    return codes.Limit(key, value, unit, codes.EMPIRICAL, "", "测试夹具：量级取自常规工程")


def _unverified(key: str, value, unit: str) -> codes.Limit:
    return codes.Limit(key, value, unit, f"{codes.UNVERIFIED}:测试夹具未取证", "", "")


REAL = {
    "quantity.concrete_per_floor_area_m3_m2": ((0.30, 0.55), "m³/m²"),
    "quantity.rebar_per_concrete_kg_m3": ((30.0, 250.0), "kg/m³"),
    "grid.span_range_m": ((4.0, 12.0), "m"),
}


@pytest.fixture()
def filled(monkeypatch):
    for key, (value, unit) in REAL.items():
        monkeypatch.setitem(codes.LIMITS, key, _verified(key, value, unit))
    return REAL


# ── qty.concrete_per_floor_area ────────────────────────────────────

@pytest.mark.unit
def test_混凝土用量_常规框架不报(filled):
    # Arrange
    model = _model(_normal_floor())

    # Act / Assert
    assert list(rq.concrete_per_floor_area(model)) == []


@pytest.mark.unit
def test_混凝土用量_图框被当成楼板时被抓到(filled):
    # Arrange：兜底板 3000 m²（图框量级）×0.15 厚 = 450 m³，
    #          而真实建筑面积只有 400 m² —— 这正是 519,684 m³ 那次事故的形状
    fake = _slab("fake", 60.0, 50.0, 0.15, basis="largest_polygon")
    floor = _normal_floor()
    model = _model(_floor(*floor.elements, fake))

    # Act
    findings = list(rq.concrete_per_floor_area(model))

    # Assert
    assert len(findings) == 1
    assert findings[0].severity == "implausible"
    assert findings[0].evidence["ratio_m3_m2"] > 1.0
    assert findings[0].evidence["fallback_slab_volume_m3"] == pytest.approx(450.0)
    # 分母没有被假板撑大：建筑面积只认非兜底板
    assert findings[0].evidence["total_area_m2"] == pytest.approx(400.0)


@pytest.mark.unit
def test_混凝土用量_远低于下限时按suspect报识别不全(filled):
    # Arrange：只认出一块 80 mm 的板，折算 0.08 m³/m²
    model = _model(_floor(_slab("s1", 20.0, 20.0, 0.08)))

    # Act
    findings = list(rq.concrete_per_floor_area(model))

    # Assert：低于下限说的是「识别欠缺」，不是「现实不可能」，故只到 suspect
    assert len(findings) == 1
    assert findings[0].severity == "suspect"
    assert findings[0].evidence["ratio_m3_m2"] == pytest.approx(0.08)


@pytest.mark.unit
def test_混凝土用量_限值未取证时降级为suspect(monkeypatch, filled):
    # Arrange
    monkeypatch.setitem(codes.LIMITS, "quantity.concrete_per_floor_area_m3_m2",
                        _unverified("quantity.concrete_per_floor_area_m3_m2",
                                    (0.30, 0.55), "m³/m²"))
    fake = _slab("fake", 60.0, 50.0, 0.15, basis="largest_polygon")
    model = _model(_floor(*_normal_floor().elements, fake))

    # Act
    finding = list(rq.concrete_per_floor_area(model))[0]

    # Assert
    assert finding.severity == "suspect"
    assert "待取证" in finding.basis


@pytest.mark.unit
def test_混凝土用量_没有可信楼板时抛RuleNotApplicable(filled):
    # Arrange：只有兜底板，建筑面积无从谈起
    model = _model(_floor(_slab("fake", 60.0, 50.0, 0.15, basis="largest_polygon")))

    # Act / Assert
    with pytest.raises(RuleNotApplicable, match="建筑面积"):
        list(rq.concrete_per_floor_area(model))


@pytest.mark.unit
def test_混凝土用量_限值仍是占位时抛RuleNotApplicable(monkeypatch, filled):
    # Arrange：取证任务还没填到这一条时，limit 是 (0, 0) 的占位
    monkeypatch.setitem(codes.LIMITS, "quantity.concrete_per_floor_area_m3_m2",
                        _unverified("quantity.concrete_per_floor_area_m3_m2",
                                    (0.0, 0.0), "m³/m²"))
    model = _model(_normal_floor())

    # Act / Assert
    with pytest.raises(RuleNotApplicable, match="占位"):
        list(rq.concrete_per_floor_area(model))


# ── qty.rebar_per_concrete ─────────────────────────────────────────

@pytest.mark.unit
def test_钢筋含量_模型不带钢筋量时如实skip(filled):
    # Arrange：scene 里本就没有钢筋量 —— 这是常态
    model = _model(_normal_floor())

    # Act / Assert
    with pytest.raises(RuleNotApplicable, match="钢筋"):
        list(rq.rebar_per_concrete(model))


@pytest.mark.unit
def test_钢筋含量_给了钢筋量且超出区间时报出(filled):
    # Arrange：140 m³ 混凝土配 140 t 钢筋 → 1000 kg/m³，远超 250
    model = _model(_normal_floor(), meta={"rebar_kg": 140_000.0})

    # Act
    findings = list(rq.rebar_per_concrete(model))

    # Assert
    assert len(findings) == 1
    assert findings[0].evidence["rebar_kg_m3"] > 250.0


@pytest.mark.unit
def test_钢筋含量_给了合理钢筋量时不报(filled):
    # Arrange：140 m³ 配 14 t → 100 kg/m³
    model = _model(_normal_floor(), meta={"rebar_kg": 14_000.0})

    # Act / Assert
    assert list(rq.rebar_per_concrete(model)) == []


# ── qty.element_density ────────────────────────────────────────────

@pytest.mark.unit
def test_柱密度_常规柱网不报(filled):
    # Arrange：400 m² 摆 16 根 → 4 根/100 m²，对应柱网 5 m
    model = _model(_normal_floor())

    # Act / Assert
    assert list(rq.element_density(model)) == []


@pytest.mark.unit
def test_柱密度_过密时报出(filled):
    # Arrange：400 m² 摆 100 根 → 25 根/100 m²，对应柱网 2 m
    columns = [_column(f"c{i}", 1.0 + i * 0.15, 1.0, 0.4) for i in range(100)]
    model = _model(_floor(_slab("s1", 20.0, 20.0, 0.20), *columns))

    # Act
    findings = list(rq.element_density(model))

    # Assert
    assert len(findings) == 1
    assert findings[0].evidence["columns_per_100m2"] == pytest.approx(25.0)
    assert findings[0].evidence["upper_per_100m2"] == pytest.approx(100 / 16.0)


@pytest.mark.unit
def test_柱密度_一根柱都没认出时报出(filled):
    # Arrange
    model = _model(_floor(_slab("s1", 20.0, 20.0, 0.20)))

    # Act
    findings = list(rq.element_density(model))

    # Assert
    assert len(findings) == 1
    assert findings[0].evidence["column_count"] == 0


@pytest.mark.unit
def test_柱密度_限值未取证时降级为suspect(monkeypatch, filled):
    # Arrange
    monkeypatch.setitem(codes.LIMITS, "grid.span_range_m",
                        _unverified("grid.span_range_m", (4.0, 12.0), "m"))
    model = _model(_floor(_slab("s1", 20.0, 20.0, 0.20)))

    # Act
    finding = list(rq.element_density(model))[0]

    # Assert
    assert finding.severity == "suspect"
    assert "待取证" in finding.basis


@pytest.mark.unit
def test_柱密度_没有可信楼板时抛RuleNotApplicable(filled):
    # Arrange
    model = _model(_floor(_column("c1", 1.0, 1.0, 0.5)))

    # Act / Assert
    with pytest.raises(RuleNotApplicable, match="建筑面积"):
        list(rq.element_density(model))


# ── qty.floor_area_vs_envelope ─────────────────────────────────────

@pytest.mark.unit
def test_占地与包络_常规楼层不报(filled):
    # Arrange：板铺满包络，柱墙落在板内 → 比值略大于 1
    model = _model(_normal_floor())

    # Act / Assert
    assert list(rq.floor_area_vs_envelope(model)) == []


@pytest.mark.unit
def test_占地与包络_同一块板被数了五遍时报出重复计数(filled):
    # Arrange：总图与分图重复导入的指纹 —— 占地是包络的 5 倍
    slabs = [_slab(f"s{i}", 20.0, 20.0, 0.20) for i in range(5)]
    model = _model(_floor(*slabs))

    # Act
    findings = list(rq.floor_area_vs_envelope(model))

    # Assert
    assert len(findings) == 1
    assert findings[0].severity == "implausible"
    assert findings[0].evidence["footprint_over_envelope"] == pytest.approx(5.0)


@pytest.mark.unit
def test_占地与包络_构件几乎全丢时报出(filled):
    # Arrange：20×20 的范围里只剩三根 100 mm 的柱
    columns = [_column("c0", 0.0, 0.0, 0.1), _column("c1", 20.0, 0.0, 0.1),
               _column("c2", 0.0, 20.0, 0.1)]
    model = _model(_floor(*columns))

    # Act
    findings = list(rq.floor_area_vs_envelope(model))

    # Assert
    assert len(findings) == 1
    assert findings[0].severity == "suspect"
    assert findings[0].evidence["footprint_over_envelope"] < 0.02


@pytest.mark.unit
def test_占地与包络_楼层没有占地构件时抛RuleNotApplicable(filled):
    # Arrange：构件都没有 outline 也没有 path+width
    ghost = Element(uid="g1", kind="columns", building_key="b1", floor_key="f1", raw={})
    model = _model(_floor(ghost))

    # Act / Assert
    with pytest.raises(RuleNotApplicable, match="占地"):
        list(rq.floor_area_vs_envelope(model))


# ── 估算口径本身 ────────────────────────────────────────────────────

@pytest.mark.unit
def test_建筑面积口径_兜底板不计入():
    # Arrange
    floor = _floor(_slab("real", 20.0, 20.0, 0.20),
                   _slab("fake", 60.0, 50.0, 0.15, basis="largest_polygon"))

    # Act / Assert
    assert rq.floor_built_area_m2(floor) == pytest.approx(400.0)


@pytest.mark.unit
def test_体积估算口径_柱按截面积乘层高():
    # Arrange：500×500 柱，层高 3 m
    floor = _floor(_column("c1", 1.0, 1.0, 0.5))

    # Act / Assert
    assert rq.element_volume_m3(floor.elements[0], 3.0) == pytest.approx(0.75)


@pytest.mark.unit
def test_体积估算口径_梁没有截面高度时不编数():
    # Arrange：只有 path + width 的梁
    beam = Element(uid="bm1", kind="beams", building_key="b1", floor_key="f1",
                   raw={"path": [(0.0, 0.0), (6.0, 0.0)], "width": 0.25})

    # Act / Assert
    assert rq.element_volume_m3(beam, 3.0) is None


# ── 注册 ────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_四条规则都注册进了规则表():
    # Arrange
    from core.model3d.plausibility import registry

    # Act
    ids = {rule.id for rule in registry.all_rules()}

    # Assert
    assert {"qty.concrete_per_floor_area", "qty.rebar_per_concrete",
            "qty.element_density", "qty.floor_area_vs_envelope"} <= ids
