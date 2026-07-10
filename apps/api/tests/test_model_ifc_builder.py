"""model_ifc_builder 测试：验证生成的 IFC 合规、构件计数、楼层标高、单位。

若 CI 未安装 ifcopenshell，则整文件跳过（importorskip）。
"""
from __future__ import annotations

import pytest

ifcopenshell = pytest.importorskip("ifcopenshell")

from core.model3d.types import FloorElements  # noqa: E402
from services import model_ifc_builder  # noqa: E402
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


# ── A-17 补齐：管线 / 设备 / 逐类降级路径 ──────────────────────────


def _single_story(elements: FloorElements) -> IfcProjectInput:
    """把一层构件集合包成最小项目输入（1 单体 × 1 层）。"""
    stories = (IfcStoryInput("F1", "1层", 1, 0.0, 4.0, elements),)
    return IfcProjectInput("P", buildings=(IfcBuildingInput("main", "U", stories),))


def test_pipes_and_equipment_are_built():
    # Arrange：管线（圆截面拉伸）+ 设备（多边形拉伸）
    elements = FloorElements(
        pipes=[{"path": [[0, 0], [5, 0]], "dia": 0.15, "system": "给水"}],
        equipment=[
            {"outline": [[0, 0], [1, 0], [1, 1], [0, 1]], "height": 1.2, "label": "水泵"}
        ],
    )

    # Act
    result = build_ifc_from_model(_single_story(elements))
    model = ifcopenshell.file.from_string(result.ifc_bytes.decode("utf-8"))

    # Assert
    assert len(model.by_type("IfcFlowSegment")) == 1
    assert len(model.by_type("IfcBuildingElementProxy")) == 1
    assert result.counts["pipes"] == 1
    assert result.counts["equipment"] == 1


def test_pipe_diameter_below_minimum_is_clamped(caplog):
    # Arrange：直径远小于下限 → 钳制为下限并记 warning，但仍建模
    elements = FloorElements(pipes=[{"path": [[0, 0], [3, 0]], "dia": 0.001}])

    # Act
    import logging

    with caplog.at_level(logging.WARNING):
        result = build_ifc_from_model(_single_story(elements))

    # Assert
    assert result.counts["pipes"] == 1
    assert any("管线直径" in rec.message for rec in caplog.records)


def test_pipe_with_zero_length_path_skipped():
    # 首末点重合 → 长度为零 → 跳过（_pipe_solid 返回 None）
    elements = FloorElements(pipes=[{"path": [[2, 2], [2, 2]], "dia": 0.1}])
    result = build_ifc_from_model(_single_story(elements))
    assert result.counts.get("pipes", 0) == 0


def test_pipe_with_single_point_path_skipped():
    # path 点数不足 2 → _pipe_solid 返回 None → 跳过
    elements = FloorElements(pipes=[{"path": [[1, 1]], "dia": 0.1}])
    result = build_ifc_from_model(_single_story(elements))
    assert result.counts.get("pipes", 0) == 0


def test_wall_with_single_point_path_skipped():
    # path 点数不足 2 → _oriented_box_profile 返回 None → 墙被跳过
    elements = FloorElements(walls=[{"path": [[0, 0]], "width": 0.2}])
    result = build_ifc_from_model(_single_story(elements))
    assert result.counts.get("walls", 0) == 0


def test_beam_with_too_short_segment_skipped():
    # 首末点间距 < 最小段长 → 跳过
    elements = FloorElements(beams=[{"path": [[0, 0], [0.01, 0]], "width": 0.3}])
    result = build_ifc_from_model(_single_story(elements))
    assert result.counts.get("beams", 0) == 0


def test_slab_with_insufficient_outline_skipped():
    # 板轮廓点数不足 3 → _polygon_profile 返回 None → 跳过
    elements = FloorElements(slabs=[{"outline": [[0, 0], [1, 0]], "thickness": 0.1}])
    result = build_ifc_from_model(_single_story(elements))
    assert result.counts.get("slabs", 0) == 0


def test_equipment_with_bad_outline_skipped():
    elements = FloorElements(equipment=[{"outline": [[0, 0]], "label": "无效设备"}])
    result = build_ifc_from_model(_single_story(elements))
    assert result.counts.get("equipment", 0) == 0


def test_element_raising_exception_is_skipped(caplog):
    # 非数值坐标 → 单构件挂载抛异常 → 跳过并记 warning，不拖垮整体建模
    import logging

    elements = FloorElements(
        columns=[{"outline": [["a", "b"], ["c", "d"], ["e", "f"]]}]
    )
    with caplog.at_level(logging.WARNING):
        result = build_ifc_from_model(_single_story(elements))
    assert result.counts.get("columns", 0) == 0
    assert any("跳过异常" in rec.message for rec in caplog.records)


# ── A-17 补齐：私有几何 helper 边界（量集 / 面积）───────────────────


def test_add_base_quantities_noop_when_empty():
    # 无任何量 → 不创建 IfcElementQuantity（提前返回）
    model = ifcopenshell.api.project.create_file(version="IFC4")
    product = model.createIfcColumn(ifcopenshell.guid.new())
    model_ifc_builder._add_base_quantities(model, product, "Qto_Test")
    assert model.by_type("IfcElementQuantity") == []


def test_polygon_area_degenerate_returns_zero():
    # 点数不足 3 → 面积 0
    assert model_ifc_builder._polygon_area([(0.0, 0.0), (1.0, 0.0)]) == 0.0


def test_polygon_area_unit_square():
    square = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    assert model_ifc_builder._polygon_area(square) == pytest.approx(1.0)
