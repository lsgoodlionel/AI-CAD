"""Phase H2 观测适配层单测 —— 构件记录 → component_observations 观测候选。

纯函数,离线可测(无 DB/引擎依赖)。
"""
from services.component_observations import (
    KIND_TO_TYPE,
    locate_in_grid,
    observation_from_element,
    observations_from_elements,
    region_to_world,
    regions_to_observations,
)


_TRANSFORM = {"scale": 0.0261535, "origin_x": 1188.26, "origin_y": 143.48,
              "page_h": 2384.0, "page_w": 3370.0}


def test_region_to_world_center_in_meters():
    # bbox 中心 (0.5,0.5) → pt (1685,1192) → 米
    wc = region_to_world([0.45, 0.45, 0.1, 0.1], _TRANSFORM)
    assert wc is not None and len(wc) == 2


def test_region_to_world_invalid():
    assert region_to_world([0.1, 0.1], _TRANSFORM) is None          # 长度错
    assert region_to_world([0, 0, 0.1, 0.1], {"scale": 0}) is None  # 缺参数


def test_regions_to_observations_format():
    regions = [{"type": "column", "bbox": [0.1, 0.1, 0.05, 0.05], "confidence": 0.7},
               {"type": "equipment", "bbox": [0.5, 0.5, 0.1, 0.1]}]
    obs = regions_to_observations(regions, "dV", _TRANSFORM)
    assert len(obs) == 2
    assert all(o["engine"] == "vlm" for o in obs)
    assert obs[0]["type"] == "column"
    assert obs[0]["drawing_id"] == "dV"
    assert obs[0]["world_coord"] is not None
    assert obs[1]["confidence"] == 0.5      # 缺省


def test_regions_to_observations_no_transform_null_world():
    obs = regions_to_observations([{"type": "wall", "bbox": [0, 0, 0.1, 0.1]}], "dV", None)
    assert obs[0]["world_coord"] is None
    assert obs[0]["engine"] == "vlm"


_AXES = {
    "x": [{"label": "A", "coord": 0.0}, {"label": "B", "coord": 6.0}, {"label": "C", "coord": 12.0}],
    "y": [{"label": "1", "coord": 0.0}, {"label": "2", "coord": 5.0}, {"label": "3", "coord": 10.0}],
}


def test_locate_in_grid_nearest_axes():
    # (11.5, 9.8) 最近 x=C(12)、y=3(10)
    assert locate_in_grid([11.5, 9.8], _AXES) == "C-3"
    # (0.2, 0.1) 最近 A-1
    assert locate_in_grid([0.2, 0.1], _AXES) == "A-1"


def test_locate_in_grid_none_without_axes_or_coord():
    assert locate_in_grid([1, 1], None) is None
    assert locate_in_grid(None, _AXES) is None
    assert locate_in_grid([1, 1], {"x": [], "y": []}) is None


def test_locate_in_grid_partial_axis_returns_marked_key():
    """只命中一根轴 → 部分键 "X-?"/"?-Y"(供显示/覆盖;装配器不以其作合并键防狂并)。"""
    from services.component_observations import is_full_grid
    only_x = {"x": [{"label": "C", "coord": 12.0}], "y": []}
    only_y = {"x": [], "y": [{"label": "3", "coord": 10.0}]}
    assert locate_in_grid([11.5, 9.8], only_x) == "C-?"
    assert locate_in_grid([11.5, 9.8], only_y) == "?-3"
    assert is_full_grid("C-3") is True
    assert is_full_grid("C-?") is False
    assert is_full_grid(None) is False


def test_observation_fills_grid_cell_when_axes_given():
    item = {"src": "dA", "source": "rule", "outline": [[11, 9], [12, 10]]}  # 质心 (11.5, 9.5)
    obs = observation_from_element(item, "columns", "plan", _AXES)
    assert obs["grid_cell"] == "C-3"


def test_observations_from_elements_threads_axes():
    elements = {"columns": [{"src": "d1", "outline": [[5.9, 4.9], [6.1, 5.1]]}],
                "walls": [], "beams": [], "slabs": [], "pipes": [], "equipment": []}
    obs = observations_from_elements(elements, view_type="plan", axes=_AXES)
    assert obs[0]["grid_cell"] == "B-2"


def test_centroid_world_coord_from_outline():
    """columns 用 outline,质心为世界坐标。"""
    item = {"src": "dA", "source": "rule", "confidence": 0.9,
            "outline": [[0, 0], [4, 0], [4, 2], [0, 2]]}
    obs = observation_from_element(item, "columns", "plan")
    assert obs is not None
    assert obs["type"] == "column"
    assert obs["drawing_id"] == "dA"
    assert obs["engine"] == "rule"
    assert obs["view_type"] == "plan"
    assert obs["world_coord"] == [2.0, 1.0]      # 质心
    assert obs["confidence"] == 0.9
    assert obs["grid_cell"] is None              # H3 填
    assert obs["archive_ref"] is None


def test_wall_uses_path_and_engine_from_source():
    """walls 用 path;engine 取 source。"""
    item = {"src": "dB", "source": "circle", "path": [[0, 0], [10, 0]]}
    obs = observation_from_element(item, "walls", "plan")
    assert obs["type"] == "wall"
    assert obs["engine"] == "circle"
    assert obs["world_coord"] == [5.0, 0.0]
    assert obs["local_coord"] == [[0, 0], [10, 0]]


def test_type_label_prefers_type_text():
    """type_text 优先于 type_label(与 collectSourceInfo 一致)。"""
    item = {"src": "dC", "outline": [[0, 0], [1, 1]],
            "type_label": "柱", "type_text": "钢立柱KZ1"}
    obs = observation_from_element(item, "columns", None)
    assert obs["type_label"] == "钢立柱KZ1"
    assert obs["engine"] == "rule"               # source 缺省 rule


def test_element_without_src_is_dropped():
    """观测必须可溯源:无 src 返回 None。"""
    assert observation_from_element({"outline": [[0, 0]]}, "columns", "plan") is None


def test_element_without_points_has_null_world_coord():
    item = {"src": "dD", "outline": []}
    obs = observation_from_element(item, "slabs", "plan")
    assert obs is not None
    assert obs["world_coord"] is None
    assert obs["local_coord"] is None


def test_observations_from_elements_covers_all_kinds_and_skips_no_src():
    elements = {
        "columns": [{"src": "d1", "outline": [[0, 0], [2, 2]]},
                    {"outline": [[0, 0]]}],          # 无 src → 跳过
        "walls": [{"src": "d1", "path": [[0, 0], [4, 0]]}],
        "beams": [], "slabs": [], "pipes": [],
        "equipment": [{"src": "d2", "outline": [[1, 1], [3, 3]], "label": "AHU-1"}],
    }
    obs = observations_from_elements(elements, view_type="plan")
    assert len(obs) == 3                             # 2 有效构件被跳 1
    types = sorted(o["type"] for o in obs)
    assert types == ["column", "equipment", "wall"]
    equip = next(o for o in obs if o["type"] == "equipment")
    assert equip["label"] == "AHU-1"
    assert equip["drawing_id"] == "d2"


def test_kind_to_type_matches_migration_types():
    """KIND_TO_TYPE 值须落在 migration 033 component_instances.type 允许集内。"""
    allowed = {"column", "wall", "beam", "slab", "pile", "door", "window", "pipe", "equipment"}
    assert set(KIND_TO_TYPE.values()) <= allowed
