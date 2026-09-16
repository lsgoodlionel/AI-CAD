"""力学数量级校核规则的单元测试。

**为什么每个用例都自己塞限值**：`codes.LIMITS` 正在被取证任务逐条填，
值会变。用例若依赖它当下的取值，取证一改这批测试就会莫名其妙地红/绿，
而失败原因与被测逻辑无关。所以每个用例 `monkeypatch` 出自己那一套 ——
这同时也在守「限值必须**运行时**取」这条约定：若规则在 import 时快照限值，
monkeypatch 就不生效，这批测试会立刻发现。
"""
from __future__ import annotations

import math

import pytest

from core.model3d.plausibility import codes
from core.model3d.plausibility import registry
from core.model3d.plausibility import rules_statics as rs
from core.model3d.plausibility.model import Building, Element, Floor, PlausibilityModel
from core.model3d.plausibility.types import RuleNotApplicable

# ── 造模型的小工具（不读库，全部手拼）──────────────────────────────


def _rect(cx: float, cy: float, width: float, depth: float) -> list[tuple[float, float]]:
    return [(cx - width / 2, cy - depth / 2), (cx + width / 2, cy - depth / 2),
            (cx + width / 2, cy + depth / 2), (cx - width / 2, cy + depth / 2)]


def _column(uid: str, cx: float, cy: float, b: float, h: float) -> Element:
    return Element(uid=uid, kind="columns", building_key="b1", floor_key="f1",
                   raw={"outline": _rect(cx, cy, b, h)})


def _slab(uid: str, width: float, depth: float, thickness: float, basis: str = "") -> Element:
    raw = {"outline": _rect(width / 2, depth / 2, width, depth), "thickness": thickness}
    if basis:
        raw["basis"] = basis
    return Element(uid=uid, kind="slabs", building_key="b1", floor_key="f1", raw=raw)


def _beam(uid: str, length: float, width: float, depth: float | None = None) -> Element:
    raw: dict = {"path": [(0.0, 0.0), (length, 0.0)], "width": width}
    if depth is not None:
        raw["depth"] = depth
    return Element(uid=uid, kind="beams", building_key="b1", floor_key="f1", raw=raw)


def _wall(uid: str, length: float, width: float) -> Element:
    return Element(uid=uid, kind="walls", building_key="b1", floor_key="f1",
                   raw={"path": [(0.0, 0.0), (length, 0.0)], "width": width})


def _model(*elements: Element, height_m: float | None = 3.0, meta: dict | None = None,
           floors: tuple[Floor, ...] | None = None) -> PlausibilityModel:
    if floors is None:
        floors = (Floor(key="f1", label="一层", order=0, building_key="b1",
                        elevation_m=0.0, elevation_estimated=False,
                        height_m=height_m, elements=tuple(elements)),)
    return PlausibilityModel(buildings=(Building(key="b1", label="主楼", floors=floors),),
                             meta=meta or {})


def _grid_of_columns(count: int, side: float) -> list[Element]:
    """在 20×20 的范围里摆 count 根 side×side 的柱。"""
    return [_column(f"c{i}", 1.0 + i, 1.0, side, side) for i in range(count)]


# ── 限值注入 ────────────────────────────────────────────────────────

def _verified(key: str, value, unit: str) -> codes.Limit:
    # EMPIRICAL 来源只要写了 note 就算已取证（见 codes.Limit.verified）
    return codes.Limit(key, value, unit, codes.EMPIRICAL, "", "测试夹具：量级取自常规工程")


def _unverified(key: str, value, unit: str) -> codes.Limit:
    return codes.Limit(key, value, unit, f"{codes.UNVERIFIED}:测试夹具未取证", "", "")


REAL = {
    "load.dead_floor_kn_m2": (5.0, "kN/m²"),
    "load.live_floor_kn_m2": (2.0, "kN/m²"),
    "concrete.fc_mpa_by_grade": ({"C30": 14.3, "C40": 19.1}, "MPa"),
    "column.max_axial_ratio": ({"一级": 0.65, "二级": 0.75, "三级": 0.85}, "-"),
    "column.max_slenderness": (30.0, "-"),
    "beam.span_depth_ratio": ((1 / 18, 1 / 8), "-"),
}


@pytest.fixture()
def filled(monkeypatch):
    """把占位限值换成有值且已取证的一套。"""
    for key, (value, unit) in REAL.items():
        monkeypatch.setitem(codes.LIMITS, key, _verified(key, value, unit))
    return REAL


# ── statics.column_axial_ratio ─────────────────────────────────────

@pytest.mark.unit
def test_柱轴压比_从属面积过大时报出超限(filled):
    # Arrange：400 m² 只有 1 根 300×300 的柱，从属面积 400 m²
    model = _model(_slab("s1", 20.0, 20.0, 0.12), _column("c1", 10.0, 10.0, 0.3, 0.3),
                   meta={"concrete_grade": "C30"})

    # Act
    findings = list(rs.column_axial_ratio(model))

    # Assert
    assert len(findings) == 1
    assert findings[0].rule == "statics.column_axial_ratio"
    assert findings[0].severity == "implausible"
    assert findings[0].evidence["tributary_area_m2"] == pytest.approx(400.0)
    assert findings[0].evidence["axial_ratio"] > 1.0


@pytest.mark.unit
def test_柱轴压比_柱网正常时不报(filled):
    # Arrange：400 m² 摆 16 根 500×500，从属面积 25 m²
    model = _model(_slab("s1", 20.0, 20.0, 0.12), *_grid_of_columns(16, 0.5),
                   meta={"concrete_grade": "C30"})

    # Act / Assert
    assert list(rs.column_axial_ratio(model)) == []


@pytest.mark.unit
def test_柱轴压比_逐项代入量全部进入evidence(filled):
    # Arrange
    model = _model(_slab("s1", 20.0, 20.0, 0.12), _column("c1", 10.0, 10.0, 0.3, 0.3),
                   meta={"concrete_grade": "C30"})

    # Act
    evidence = list(rs.column_axial_ratio(model))[0].evidence

    # Assert：没有中间量就无法复核结论
    for key in ("tributary_area_m2", "floors_carried", "load_kn_m2",
                "axial_force_kn", "section_area_m2", "fc_mpa", "axial_ratio", "limit"):
        assert key in evidence, key
    assert all(isinstance(v, (int, float)) for v in evidence.values())


@pytest.mark.unit
def test_柱轴压比_混凝土等级未知时降级为suspect(filled):
    # Arrange：不给 concrete_grade，fc 只能按表中最小值假定
    model = _model(_slab("s1", 20.0, 20.0, 0.12), _column("c1", 10.0, 10.0, 0.3, 0.3))

    # Act
    finding = list(rs.column_axial_ratio(model))[0]

    # Assert
    assert finding.severity == "suspect"
    assert finding.evidence["fc_assumed"] == 1


@pytest.mark.unit
def test_柱轴压比_限值未取证时降级为suspect并在依据里注明(monkeypatch, filled):
    # Arrange：把轴压比限值换成未取证的同值限值
    monkeypatch.setitem(codes.LIMITS, "column.max_axial_ratio",
                        _unverified("column.max_axial_ratio", {"三级": 0.85}, "-"))
    model = _model(_slab("s1", 20.0, 20.0, 0.12), _column("c1", 10.0, 10.0, 0.3, 0.3),
                   meta={"concrete_grade": "C30"})

    # Act
    finding = list(rs.column_axial_ratio(model))[0]

    # Assert
    assert finding.severity == "suspect"
    assert "待取证" in finding.basis


@pytest.mark.unit
def test_柱轴压比_没有柱时抛RuleNotApplicable(filled):
    # Arrange
    model = _model(_slab("s1", 20.0, 20.0, 0.12))

    # Act / Assert
    with pytest.raises(RuleNotApplicable, match="柱"):
        list(rs.column_axial_ratio(model))


@pytest.mark.unit
def test_柱轴压比_没有可信楼板时抛RuleNotApplicable(filled):
    # Arrange：有柱没板 —— 建筑面积无从谈起（兜底板不算数，它正是被查的对象）
    model = _model(_column("c1", 10.0, 10.0, 0.3, 0.3),
                   _slab("s1", 20.0, 20.0, 0.12, basis="largest_polygon"))

    # Act / Assert
    with pytest.raises(RuleNotApplicable, match="建筑面积"):
        list(rs.column_axial_ratio(model))


@pytest.mark.unit
def test_柱轴压比_限值仍是占位时抛RuleNotApplicable(monkeypatch, filled):
    # Arrange：取证任务还没填到这一条时，荷载限值是 0 的占位
    monkeypatch.setitem(codes.LIMITS, "load.dead_floor_kn_m2",
                        _unverified("load.dead_floor_kn_m2", 0.0, "kN/m²"))
    model = _model(_slab("s1", 20.0, 20.0, 0.12), _column("c1", 10.0, 10.0, 0.3, 0.3))

    # Act / Assert
    with pytest.raises(RuleNotApplicable, match="占位"):
        list(rs.column_axial_ratio(model))


@pytest.mark.unit
def test_柱轴压比_承担层数随楼层升高递减(filled):
    # Arrange：三层，每层一根柱、一块板
    floors = tuple(
        Floor(key=f"f{i}", label=f"{i + 1}层", order=i, building_key="b1",
              elevation_m=float(i) * 3.0, elevation_estimated=False, height_m=3.0,
              elements=(_slab(f"s{i}", 20.0, 20.0, 0.12),
                        _column(f"c{i}", 10.0, 10.0, 1.2, 1.2)))
        for i in range(3))
    model = _model(floors=floors, meta={"concrete_grade": "C30"})

    # Act：把承担层数按 uid 取出来
    carried = {f.target: f.evidence["floors_carried"]
               for f in rs._axial_ratio_rows(model)}

    # Assert：底层扛 3 层，顶层只扛 1 层
    assert carried["c0"] == 3
    assert carried["c2"] == 1


@pytest.mark.unit
def test_柱轴压比_荷载限值填成区间或分档表时取最小的一档(monkeypatch, filled):
    # Arrange：取证任务按条款原文填，恒载是区间、活载是按房间用途的分档表
    monkeypatch.setitem(codes.LIMITS, "load.dead_floor_kn_m2",
                        _verified("load.dead_floor_kn_m2", (5.0, 9.0), "kN/m²"))
    monkeypatch.setitem(codes.LIMITS, "load.live_floor_kn_m2",
                        _verified("load.live_floor_kn_m2", {"住宅": 2.0, "商店": 4.0}, "kN/m²"))
    model = _model(_slab("s1", 20.0, 20.0, 0.12), _column("c1", 10.0, 10.0, 0.3, 0.3),
                   meta={"concrete_grade": "C30"})

    # Act
    evidence = list(rs.column_axial_ratio(model))[0].evidence

    # Assert：取 5.0 + 2.0 而不是 9.0 + 4.0 —— 荷载取小才不会把正常柱判成超限
    assert evidence["load_kn_m2"] == pytest.approx(7.0)


@pytest.mark.unit
def test_柱长细比_限值被填成区间时如实skip(monkeypatch, filled):
    # Arrange：长细比只接受标量，形状不对不能猜
    monkeypatch.setitem(codes.LIMITS, "column.max_slenderness",
                        _verified("column.max_slenderness", (20.0, 30.0), "-"))
    model = _model(_column("c1", 0.0, 0.0, 0.3, 0.3), height_m=3.0)

    # Act / Assert
    with pytest.raises(RuleNotApplicable, match="标量"):
        list(rs.column_slenderness(model))


# ── statics.beam_span_depth ────────────────────────────────────────

@pytest.mark.unit
def test_梁高跨比_过于细柔时报出(filled):
    # Arrange：6 m 跨、梁高 200 mm → h/l0 = 0.033，低于 1/18
    model = _model(_beam("bm1", 6.0, 0.25, depth=0.2))

    # Act
    findings = list(rs.beam_span_depth(model))

    # Assert
    assert len(findings) == 1
    assert findings[0].evidence["span_depth_ratio"] == pytest.approx(0.2 / 6.0, abs=1e-5)


@pytest.mark.unit
def test_梁高跨比_常规梁不报(filled):
    # Arrange：6 m 跨、梁高 600 mm → h/l0 = 0.10，落在 1/18~1/8 之间
    model = _model(_beam("bm1", 6.0, 0.25, depth=0.6))

    # Act / Assert
    assert list(rs.beam_span_depth(model)) == []


@pytest.mark.unit
def test_梁高跨比_梁没有截面高度时抛RuleNotApplicable(filled):
    # Arrange：scene 里的梁本来只有 path + width，这是常态而非异常
    model = _model(_beam("bm1", 6.0, 0.25))

    # Act / Assert
    with pytest.raises(RuleNotApplicable, match="截面高度"):
        list(rs.beam_span_depth(model))


@pytest.mark.unit
def test_梁高跨比_限值未取证时降级为suspect(monkeypatch, filled):
    # Arrange
    monkeypatch.setitem(codes.LIMITS, "beam.span_depth_ratio",
                        _unverified("beam.span_depth_ratio", (1 / 18, 1 / 8), "-"))
    model = _model(_beam("bm1", 6.0, 0.25, depth=0.2))

    # Act
    finding = list(rs.beam_span_depth(model))[0]

    # Assert
    assert finding.severity == "suspect"
    assert "待取证" in finding.basis


# ── statics.column_slenderness ─────────────────────────────────────

@pytest.mark.unit
def test_柱长细比_细柱超限时报出(filled):
    # Arrange：层高 3 m、截面 300×300 → i = 0.3/√12，λ ≈ 34.6 > 30
    model = _model(_column("c1", 0.0, 0.0, 0.3, 0.3), height_m=3.0)

    # Act
    findings = list(rs.column_slenderness(model))

    # Assert
    assert len(findings) == 1
    assert findings[0].evidence["slenderness"] == pytest.approx(3.0 / (0.3 / math.sqrt(12)))


@pytest.mark.unit
def test_柱长细比_粗柱不报(filled):
    # Arrange：层高 3 m、截面 600×600 → λ ≈ 17.3
    model = _model(_column("c1", 0.0, 0.0, 0.6, 0.6), height_m=3.0)

    # Act / Assert
    assert list(rs.column_slenderness(model)) == []


@pytest.mark.unit
def test_柱长细比_没有层高时抛RuleNotApplicable(filled):
    # Arrange
    model = _model(_column("c1", 0.0, 0.0, 0.3, 0.3), height_m=None)

    # Act / Assert
    with pytest.raises(RuleNotApplicable, match="层高"):
        list(rs.column_slenderness(model))


@pytest.mark.unit
def test_柱长细比_限值未取证时降级为suspect(monkeypatch, filled):
    # Arrange
    monkeypatch.setitem(codes.LIMITS, "column.max_slenderness",
                        _unverified("column.max_slenderness", 30.0, "-"))
    model = _model(_column("c1", 0.0, 0.0, 0.3, 0.3), height_m=3.0)

    # Act
    finding = list(rs.column_slenderness(model))[0]

    # Assert
    assert finding.severity == "suspect"
    assert "待取证" in finding.basis


# ── statics.absurd_span 已并入 geom.absurd_extent ──────────────────
# 两条判的是同一件事，几何族覆盖更全（六类各有上限，线状构件按 footprint
# 量长边）。同一根构件被两条各报一次只会让命中数虚高，故本族不再实现，
# 用例随之移除；上限的出处见 codes.extent.max_by_kind_m。


def test_本族注册的规则都进了全局规则表():
    """注册是 import 的副作用；漏注册的规则永远不会被 `registry.run` 跑到。"""
    # Arrange & Act
    ids = {rule.id for rule in registry.all_rules()}

    # Assert
    assert {"statics.column_axial_ratio", "statics.beam_span_depth",
            "statics.column_slenderness"} <= ids
    assert "statics.absurd_span" not in ids, "已并入 geom.absurd_extent，不该再注册"
