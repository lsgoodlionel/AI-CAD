"""model_ifc_builder 测试：验证生成的 IFC 合规、构件计数、楼层标高、单位。

若 CI 未安装 ifcopenshell，则整文件跳过（importorskip）。
"""
from __future__ import annotations

import pytest

ifcopenshell = pytest.importorskip("ifcopenshell")

from core.model3d.types import FloorElements  # noqa: E402
from services.model_ifc_builder import (  # noqa: E402
    IfcBuildingInput,
    IfcProjectInput,
    IfcStoryInput,
    build_ifc_from_model,
)


def _elements() -> FloorElements:
    return FloorElements(
        columns=[
            {"outline": [[0, 0], [0.6, 0], [0.6, 0.6], [0, 0.6]]},
            {"outline": [[8, 0], [8.6, 0], [8.6, 0.6], [8, 0.6]]},
        ],
        walls=[{"path": [[0, 0], [8, 0]], "width": 0.2}],
        slabs=[{"outline": [[0, 0], [8, 0], [8, 8], [0, 8]], "thickness": 0.12}],
        beams=[{"path": [[0, 0], [8, 0]], "width": 0.3, "depth": 0.6}],
    )


def _project() -> IfcProjectInput:
    stories = (
        IfcStoryInput("F1", "1层", 1, 0.0, 4.5, _elements()),
        IfcStoryInput("F2", "2层", 2, 4.5, 4.5, _elements()),
    )
    building = IfcBuildingInput("main", "测试单体", stories)
    return IfcProjectInput(project_name="测试工程", buildings=(building,))


@pytest.fixture
def built_model(tmp_path):
    out = tmp_path / "model.ifc"
    result = build_ifc_from_model(_project(), output_path=str(out))
    return ifcopenshell.open(result.path), result


def test_writes_reopenable_ifc(built_model):
    model, result = built_model
    assert result.path is not None
    assert model.schema == "IFC4"


def test_bytes_output_when_no_path():
    result = build_ifc_from_model(_project())
    assert result.path is None
    assert result.ifc_bytes and result.ifc_bytes.startswith(b"ISO-10303-21")


def test_spatial_hierarchy_counts(built_model):
    model, _ = built_model
    assert len(model.by_type("IfcProject")) == 1
    assert len(model.by_type("IfcSite")) == 1
    assert len(model.by_type("IfcBuilding")) == 1
    assert len(model.by_type("IfcBuildingStorey")) == 2


def test_element_counts_match_input(built_model):
    model, result = built_model
    # 每层 2 柱 + 1 墙 + 1 板 + 1 梁 ×2 层
    assert len(model.by_type("IfcColumn")) == 4
    assert len(model.by_type("IfcWall")) == 2
    assert len(model.by_type("IfcSlab")) == 2
    assert len(model.by_type("IfcBeam")) == 2
    assert result.counts["columns"] == 4
    assert result.counts["walls"] == 2


def test_storey_elevations_correct(built_model):
    model, _ = built_model
    elevations = sorted(s.Elevation for s in model.by_type("IfcBuildingStorey"))
    assert elevations == pytest.approx([0.0, 4.5])


def test_length_unit_is_metre(built_model):
    model, _ = built_model
    units = model.by_type("IfcUnitAssignment")[0].Units
    length = next(u for u in units if getattr(u, "UnitType", None) == "LENGTHUNIT")
    assert length.Name == "METRE"
    assert length.Prefix is None


def test_base_quantities_attached(built_model):
    model, _ = built_model
    qto_names = {q.Name for q in model.by_type("IfcElementQuantity")}
    assert "Qto_WallBaseQuantities" in qto_names
    assert "Qto_ColumnBaseQuantities" in qto_names
    assert "Qto_SlabBaseQuantities" in qto_names


def test_invalid_geometry_skipped():
    bad = FloorElements(columns=[{"outline": [[0, 0], [1, 0]]}])  # 点数不足
    stories = (IfcStoryInput("F1", "1层", 1, 0.0, 4.5, bad),)
    project = IfcProjectInput("P", buildings=(IfcBuildingInput("main", "U", stories),))
    result = build_ifc_from_model(project)
    assert result.counts.get("columns", 0) == 0


def test_empty_project_name_raises():
    with pytest.raises(ValueError):
        build_ifc_from_model(IfcProjectInput(project_name=""))
