"""IFC-QTO 算量测试（B-16 混凝土净体积 / B-17 模板 / B-18 钢筋）。

纯几何计算可离线手算校验；IFC 写入用真实 ifcopenshell 最小模型验证挂载。
"""
import pytest

from core.economic.rebar_calculator import BarItem, optimize_cutting
from services.model_qto import (
    compute_quantities,
    compute_rebar_quantities,
)


def _column(cx, cy, half=0.25):
    return {"outline": [[cx - half, cy - half], [cx + half, cy - half],
                        [cx + half, cy + half], [cx - half, cy + half]]}


def _grid_scene():
    """4 柱 + 4 边梁 + 1 板（6×6），层高 3.0。"""
    columns = [_column(0, 0), _column(6, 0), _column(6, 6), _column(0, 6)]
    beams = [
        {"path": [[0, 0], [6, 0]], "width": 0.3, "depth": 0.6},
        {"path": [[6, 0], [6, 6]], "width": 0.3, "depth": 0.6},
        {"path": [[6, 6], [0, 6]], "width": 0.3, "depth": 0.6},
        {"path": [[0, 6], [0, 0]], "width": 0.3, "depth": 0.6},
    ]
    slabs = [{"outline": [[0, 0], [6, 0], [6, 6], [0, 6]], "thickness": 0.12}]
    return {"columns": columns, "beams": beams, "slabs": slabs, "walls": []}


def _q_by_type(quantities, element_type):
    return [q for q in quantities if q.element_type == element_type]


# ── B-16 混凝土净体积 ─────────────────────────────────────────

@pytest.mark.unit
def test_beam_gross_volume():
    quantities = compute_quantities(_grid_scene(), story_height_m=3.0)
    beam = _q_by_type(quantities, "beam")[0]
    # 6 × 0.3 × 0.6 = 1.08
    assert beam.gross_volume_m3 == pytest.approx(1.08, abs=0.001)


@pytest.mark.unit
def test_beam_net_deducts_column_overlap():
    quantities = compute_quantities(_grid_scene(), story_height_m=3.0)
    beam = _q_by_type(quantities, "beam")[0]
    # 两端各嵌入柱 side/2=0.25 → 扣 2×(0.25×0.3×0.6)=0.09 → 0.99
    assert beam.net_volume_m3 == pytest.approx(0.99, abs=0.001)
    assert beam.net_volume_m3 < beam.gross_volume_m3


@pytest.mark.unit
def test_slab_net_deducts_supporting_beams():
    quantities = compute_quantities(_grid_scene(), story_height_m=3.0)
    slab = _q_by_type(quantities, "slab")[0]
    # 毛 36×0.12=4.32；4 边梁各扣 6×0.3×0.12=0.216 → 扣 0.864 → 3.456
    assert slab.gross_volume_m3 == pytest.approx(4.32, abs=0.001)
    assert slab.net_volume_m3 == pytest.approx(3.456, abs=0.005)


@pytest.mark.unit
def test_column_net_equals_gross():
    quantities = compute_quantities(_grid_scene(), story_height_m=3.0)
    column = _q_by_type(quantities, "column")[0]
    # 0.5×0.5×3.0 = 0.75
    assert column.gross_volume_m3 == pytest.approx(0.75, abs=0.001)
    assert column.net_volume_m3 == pytest.approx(column.gross_volume_m3)


# ── B-17 模板侧面积 ───────────────────────────────────────────

@pytest.mark.unit
def test_isolated_beam_formwork_contact_and_free():
    beams = {"beams": [{"path": [[0, 0], [6, 0]], "width": 0.3, "depth": 0.6}],
             "columns": [], "slabs": [], "walls": []}
    beam = _q_by_type(compute_quantities(beams, story_height_m=3.0), "beam")[0]
    # 接触模板=两侧+底=(2×0.6+0.3)×6=9.0；自由面=顶(0.3×6)+两端(0.3×0.6×2)=2.16
    assert beam.formwork_contact_m2 == pytest.approx(9.0, abs=0.01)
    assert beam.formwork_free_m2 == pytest.approx(2.16, abs=0.01)


@pytest.mark.unit
def test_column_formwork_perimeter():
    scene = {"columns": [_column(0, 0)], "beams": [], "slabs": [], "walls": []}
    column = _q_by_type(compute_quantities(scene, story_height_m=3.0), "column")[0]
    # 周长 4×0.5=2.0 × 高 3.0 = 6.0
    assert column.formwork_contact_m2 == pytest.approx(6.0, abs=0.01)


@pytest.mark.unit
def test_estimated_flag_from_z_source():
    scene = {"beams": [{"path": [[0, 0], [6, 0]], "width": 0.3, "depth": 0.6, "z_source": "measured"}],
             "columns": [], "slabs": [], "walls": []}
    beam = _q_by_type(compute_quantities(scene, story_height_m=3.0), "beam")[0]
    assert beam.estimated is False


# ── B-18 钢筋量 ───────────────────────────────────────────────

@pytest.mark.unit
def test_rebar_quantity_matches_calculator_direct():
    inputs = [
        {"diameter": 20, "steel_grade": "HRB400", "required_length": 6000, "count": 4},
        {"diameter": 8, "steel_grade": "HPB300", "required_length": 2000, "count": 20},
    ]
    result = compute_rebar_quantities(inputs)

    bars = [BarItem(20, "HRB400", 6000, 4), BarItem(8, "HPB300", 2000, 20)]
    _patterns, summary = optimize_cutting(
        bars, [9000, 10000, 12000],
        {"d6_10": 0.06, "d12_16": 0.045, "d18_22": 0.04, "d25_plus": 0.035},
        4000.0, 0.015, 5000.0,
    )
    assert result["rebar_missing"] is False
    assert result["total_steel_kg"] == pytest.approx(summary["total_steel_kg"])


@pytest.mark.unit
def test_rebar_missing_when_no_input():
    result = compute_rebar_quantities([])
    assert result["rebar_missing"] is True
    assert result["total_steel_kg"] is None


# ── IFC 写入（真实 ifcopenshell）──────────────────────────────

@pytest.mark.unit
def test_write_concrete_quantities_attaches_qto():
    ifcopenshell = pytest.importorskip("ifcopenshell")
    model = ifcopenshell.file(schema="IFC4")
    beam = model.create_entity("IfcBeam", GlobalId=ifcopenshell.guid.new(), Name="梁")

    from services.model_qto import write_concrete_quantities

    scene = {"beams": [{"path": [[0, 0], [6, 0]], "width": 0.3, "depth": 0.6}],
             "columns": [], "slabs": [], "walls": []}
    written = write_concrete_quantities(model, scene, story_height_m=3.0)
    assert written >= 1

    qtos = model.by_type("IfcElementQuantity")
    assert any(q.Name == "Qto_BeamBaseQuantities" for q in qtos)
    net = [
        qty for q in qtos for qty in (q.Quantities or [])
        if qty.Name == "NetVolume"
    ]
    # 场景无柱 → 净=毛=6×0.3×0.6=1.08
    assert net and net[0].VolumeValue == pytest.approx(1.08, abs=0.01)


@pytest.mark.unit
def test_wall_quantity_volume_and_formwork():
    scene = {"walls": [{"path": [[0, 0], [5, 0]], "width": 0.2}],
             "columns": [], "beams": [], "slabs": []}
    wall = _q_by_type(compute_quantities(scene, story_height_m=3.0), "wall")[0]
    assert wall.gross_volume_m3 == pytest.approx(5 * 0.2 * 3.0, abs=0.01)  # 3.0
    assert wall.formwork_contact_m2 == pytest.approx(2 * 5 * 3.0, abs=0.01)  # 两大面 30.0


@pytest.mark.unit
def test_write_formwork_quantities_attaches_area_qto():
    ifcopenshell = pytest.importorskip("ifcopenshell")
    from services.model_qto import write_formwork_quantities

    model = ifcopenshell.file(schema="IFC4")
    model.create_entity("IfcBeam", GlobalId=ifcopenshell.guid.new(), Name="梁")
    scene = {"beams": [{"path": [[0, 0], [6, 0]], "width": 0.3, "depth": 0.6}],
             "columns": [], "slabs": [], "walls": []}
    written = write_formwork_quantities(model, scene, story_height_m=3.0)
    assert written >= 1
    areas = [qty for q in model.by_type("IfcElementQuantity") for qty in (q.Quantities or [])
             if qty.Name == "GrossSideArea"]
    assert areas and areas[0].AreaValue == pytest.approx(9.0, abs=0.01)


@pytest.mark.unit
def test_write_rebar_quantities_missing_marks_without_input():
    ifcopenshell = pytest.importorskip("ifcopenshell")
    from services.model_qto import write_rebar_quantities

    model = ifcopenshell.file(schema="IFC4")
    result = write_rebar_quantities(model, {}, rebar_inputs=None)
    assert result["rebar_missing"] is True
