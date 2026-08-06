"""Phase H4 装配编排单测 —— scene → ComponentInstance 集(H2→H3→约束)。纯函数。"""
from services.component_constraints import _REAL_Z_SOURCES
from services.component_pipeline import (
    _compute_z_tops,
    assemble_scene_instances,
)


def _scene():
    axes = {"x": [{"label": "C", "coord": 12.0}], "y": [{"label": "3", "coord": 10.0}]}
    floor1 = {
        "key": "F1", "order": 1, "elevation_m": -5.9, "axes": axes,
        "elements": {
            "columns": [{"src": "d1", "source": "rule", "confidence": 0.9,
                         "outline": [[11, 9], [12, 10]]}],
            "walls": [], "beams": [], "slabs": [], "pipes": [], "equipment": [],
        },
    }
    floor2 = {
        "key": "F2", "order": 2, "elevation_m": -1.4, "axes": axes,
        "elements": {
            "columns": [{"src": "d2", "source": "rule", "confidence": 0.85,
                         "outline": [[11, 9], [12, 10]]}],
            "walls": [], "beams": [], "slabs": [], "pipes": [], "equipment": [],
        },
    }
    return {
        "floors": [floor1, floor2],
        "buildings": [{"key": "unitA", "floors": [{"key": "F1"}, {"key": "F2"}]}],
        "quality": {"story_tables": {"unitA": [
            {"story_key": "F1", "height_source": "section"},
            {"story_key": "F2", "height_source": "section"},
        ]}},
    }


def test_story_z_sources_maps_real_provenance():
    """竖向 provenance 取自 story_tables:default(默认套)不得冒充真实标高。"""
    from services.component_pipeline import story_z_sources
    scene = {"quality": {"story_tables": {
        "main": [{"story_key": "F1", "height_source": "manual"},
                 {"story_key": "F2", "height_source": "section"}],
        "north": [{"story_key": "F1", "height_source": "default"}],
    }}}
    m = story_z_sources(scene)
    assert m[("main", "F1")] == "manual"          # 人工录入 → 真实
    assert m[("main", "F2")] == "section"         # 剖面恢复 → 真实
    assert m[("north", "F1")] == "story_default"  # 默认套 → 不真实


def test_story_z_sources_unknown_defaults_to_story_default():
    """未知/缺失来源保守记 story_default(宁可低报真实率,不虚高)。"""
    from services.component_pipeline import story_z_sources
    m = story_z_sources({"quality": {"story_tables": {
        "b": [{"story_key": "F1"}, {"story_key": "F2", "height_source": "guess"}]}}})
    assert m[("b", "F1")] == "story_default"
    assert m[("b", "F2")] == "story_default"


def test_dimension_snapped_reported_by_pipeline():
    """管线返回模数化统计(真实度提升可观测)。"""
    result = assemble_scene_instances(_scene())
    assert "dimension_snapped" in result


def test_z_top_is_next_floor_elevation():
    scene = _scene()
    tops = _compute_z_tops(scene["floors"], {"F1": "unitA", "F2": "unitA"})
    assert tops["F1"] == -1.4     # 下一层标高
    assert tops["F2"] is None     # 顶层无上层


def test_assemble_scene_produces_instances_with_z_and_grid():
    result = assemble_scene_instances(_scene())
    insts = result["instances"]
    assert len(insts) == 2                       # 每层 1 柱
    f1 = next(i for i in insts if i["building_key"] == "unitA" and i["z_bottom_m"] == -5.9)
    assert f1["z_top_m"] == -1.4
    assert f1["z_source"] in _REAL_Z_SOURCES
    assert f1["grid_ref"] == "C-3"
    # 落轴网:质心吸附到 (12,10)
    pts = f1["outline_m"]
    cx = sum(p[0] for p in pts) / len(pts)
    cy = sum(p[1] for p in pts) / len(pts)
    assert (round(cx, 3), round(cy, 3)) == (12.0, 10.0)


def test_continuity_gap_detected_across_floors():
    """F1、F3 有柱 C-3,F2 缺 → 报缺口。"""
    scene = _scene()
    axes = scene["floors"][0]["axes"]
    scene["floors"][1]["elements"]["columns"] = []   # F2 无柱
    scene["floors"].append({
        "key": "F3", "order": 3, "elevation_m": 3.0, "axes": axes,
        "elements": {"columns": [{"src": "d3", "source": "rule", "confidence": 0.9,
                                   "outline": [[11, 9], [12, 10]]}],
                     "walls": [], "beams": [], "slabs": [], "pipes": [], "equipment": []},
    })
    scene["buildings"][0]["floors"].append({"key": "F3"})
    result = assemble_scene_instances(scene)
    gaps = [g for g in result["continuity_gaps"] if g["grid_ref"] == "C-3"]
    assert len(gaps) == 1
    assert gaps[0]["missing_orders"] == [2]


def test_vlm_observations_merged_into_assembly():
    """H5-C 接线:VLM 分区观测按楼层图纸并入,产出 engine=vlm 的实体。"""
    scene = _scene()
    # 给 F1 挂图纸列表(供 VLM 观测按图纸并入)
    scene["floors"][0]["drawings"] = [{"id": "dVLM"}]
    vlm_obs = {
        "dVLM": [{
            "drawing_id": "dVLM", "view_type": "plan", "engine": "vlm",
            "grid_cell": None, "local_coord": None, "world_coord": [50.0, 50.0],
            "archive_ref": None, "confidence": 0.7,
            "type": "equipment", "type_label": None, "label": None,
        }],
    }
    result = assemble_scene_instances(scene, vlm_obs_by_drawing=vlm_obs)
    vlm_insts = [i for i in result["instances"]
                 if any(o.get("engine") == "vlm" for o in i["observations"])]
    assert len(vlm_insts) == 1
    assert vlm_insts[0]["type"] == "equipment"


def test_floor_without_elevation_leaves_z_null():
    scene = _scene()
    scene["floors"][0]["elevation_m"] = None
    result = assemble_scene_instances(scene)
    f1 = next(i for i in result["instances"]
              if any(str(o["drawing_id"]) == "d1" for o in i["observations"]))
    assert f1["z_bottom_m"] is None
