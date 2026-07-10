"""ifc_mapping 测试：scene → 合规 IFC4，层级完整、构件计数一致、估算标注。

CI 未装 ifcopenshell 时整文件跳过（importorskip）。
"""
from __future__ import annotations

import pytest

ifcopenshell = pytest.importorskip("ifcopenshell")

import ifcopenshell.util.element  # noqa: E402

from services.ifc_mapping import build_ifc_from_scene  # noqa: E402


def _floor_elements() -> dict:
    """一层的构件字典（2 柱 + 1 墙 + 1 板 + 1 梁，几何均合法）。"""
    return {
        "columns": [
            {"outline": [[0, 0], [0.6, 0], [0.6, 0.6], [0, 0.6]]},
            {"outline": [[8, 0], [8.6, 0], [8.6, 0.6], [8, 0.6]]},
        ],
        "walls": [{"path": [[0, 0], [8, 0]], "width": 0.2}],
        "slabs": [{"outline": [[0, 0], [8, 0], [8, 8], [0, 8]], "thickness": 0.12}],
        "beams": [{"path": [[0, 0], [8, 0]], "width": 0.3, "depth": 0.6}],
        "pipes": [],
        "equipment": [],
    }


def _scene() -> dict:
    """最小 scene：单体 main，F1 带实测标高、F2 标高缺失（须估算）。"""
    return {
        "project": {"id": "p1", "name": "测试工程"},
        "buildings": [
            {
                "key": "main",
                "label": "主楼",
                "origin": [0, 0],
                "floors": [
                    {"key": "F1", "label": "1层", "order": 1, "elevation": 1,
                     "elevation_m": 0.0, "elements": _floor_elements()},
                    {"key": "F2", "label": "2层", "order": 2, "elevation": 2,
                     "elevation_m": None, "elements": _floor_elements()},
                ],
            }
        ],
    }


@pytest.fixture
def built_model():
    data = build_ifc_from_scene(_scene())
    return ifcopenshell.file.from_string(data.decode("utf-8")), data


def test_returns_reopenable_ifc4_bytes(built_model):
    model, data = built_model
    assert data.startswith(b"ISO-10303-21")
    assert model.schema == "IFC4"


def test_spatial_hierarchy_complete(built_model):
    model, _ = built_model
    assert len(model.by_type("IfcProject")) == 1
    assert len(model.by_type("IfcSite")) == 1
    assert len(model.by_type("IfcBuilding")) == 1
    assert len(model.by_type("IfcBuildingStorey")) == 2


def test_element_counts_match_scene(built_model):
    model, _ = built_model
    # 每层 2 柱/1 墙/1 板/1 梁 × 2 层
    assert len(model.by_type("IfcColumn")) == 4
    assert len(model.by_type("IfcWall")) == 2
    assert len(model.by_type("IfcSlab")) == 2
    assert len(model.by_type("IfcBeam")) == 2


def test_project_name_from_scene(built_model):
    model, _ = built_model
    assert model.by_type("IfcProject")[0].Name == "测试工程"


def test_estimated_elevation_stacks_default_height(built_model):
    model, _ = built_model
    # F1=0.0（实测），F2 标高缺失 → 0.0 + 默认层高 4.5
    elevations = sorted(s.Elevation for s in model.by_type("IfcBuildingStorey"))
    assert elevations == pytest.approx([0.0, 4.5])


def test_provenance_pset_marks_estimated(built_model):
    model, _ = built_model
    by_name = {s.Name: s for s in model.by_type("IfcBuildingStorey")}
    f1 = ifcopenshell.util.element.get_psets(by_name["1层"])["Pset_ModelProvenance"]
    f2 = ifcopenshell.util.element.get_psets(by_name["2层"])["Pset_ModelProvenance"]
    # F1 有实测标高 → 标高非估算，但层高恒为默认 → 整体仍估算
    assert f1["ElevationEstimated"] is False
    assert f1["HeightEstimated"] is True
    assert f1["IsEstimated"] is True
    # F2 标高缺失 → 标高估算
    assert f2["ElevationEstimated"] is True
    assert f2["IsEstimated"] is True


def test_default_project_name_when_missing():
    scene = {"buildings": [{"key": "main", "label": "主体", "floors": []}]}
    model = ifcopenshell.file.from_string(build_ifc_from_scene(scene).decode("utf-8"))
    assert model.by_type("IfcProject")[0].Name == "工程模型"


def test_project_name_override():
    model = ifcopenshell.file.from_string(
        build_ifc_from_scene(_scene(), project_name="覆盖名").decode("utf-8")
    )
    assert model.by_type("IfcProject")[0].Name == "覆盖名"


def test_multiple_buildings_mapped():
    scene = _scene()
    scene["buildings"].append({
        "key": "annex", "label": "附楼", "origin": [0, 0],
        "floors": [{"key": "F1", "label": "1层", "order": 1, "elevation": 1,
                    "elevation_m": 0.0, "elements": _floor_elements()}],
    })
    model = ifcopenshell.file.from_string(build_ifc_from_scene(scene).decode("utf-8"))
    assert len(model.by_type("IfcBuilding")) == 2
    assert len(model.by_type("IfcBuildingStorey")) == 3


def test_floors_fallback_when_no_buildings():
    scene = {
        "project": {"name": "回退工程"},
        "floors": [{"key": "F1", "label": "1层", "order": 1, "elevation_m": 0.0,
                    "elements": _floor_elements()}],
    }
    model = ifcopenshell.file.from_string(build_ifc_from_scene(scene).decode("utf-8"))
    assert len(model.by_type("IfcBuilding")) == 1
    assert len(model.by_type("IfcBuildingStorey")) == 1
    assert len(model.by_type("IfcColumn")) == 2


def test_non_dict_scene_raises():
    with pytest.raises(TypeError):
        build_ifc_from_scene([1, 2, 3])  # type: ignore[arg-type]
