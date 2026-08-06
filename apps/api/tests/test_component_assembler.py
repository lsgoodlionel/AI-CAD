"""Phase H3 装配器单测 —— 观测 → ComponentInstance(数据关联 + 聚合 + 冲突)。

纯函数,离线可测。
"""
from services.component_assembler import (
    CONFLICT_CONFIDENCE,
    _combine_confidence,
    assemble_instances,
)


def _obs(type_, grid=None, wc=None, conf=0.9, drawing="d1", label=None, outline=None):
    return {
        "type": type_, "grid_cell": grid, "world_coord": wc,
        "confidence": conf, "drawing_id": drawing, "type_label": label,
        "local_coord": outline,
    }


def test_same_type_and_grid_merge_into_one_instance():
    """同 (type, grid_cell) 的多观测 → 1 实体、2 观测(跨图累积证据)。"""
    obs = [
        _obs("column", grid="C-3", conf=0.9, drawing="planF1"),
        _obs("column", grid="C-3", conf=0.8, drawing="planF3"),
    ]
    insts = assemble_instances(obs)
    assert len(insts) == 1
    assert len(insts[0]["observations"]) == 2
    assert insts[0]["grid_ref"] == "C-3"
    # 语义键含单体+楼层维度,防跨层碰撞(实测 pipe@3-BY 曾触发唯一约束冲突)
    assert insts[0]["semantic_key"].startswith("column@::C-3")


def test_semantic_key_includes_building_and_floor():
    """同轴网格在不同楼层是不同构件 → 语义键须可区分(仅点状构件有语义键)。"""
    a = assemble_instances([_obs("column", grid="3-BY")], building_key="main", floor_key="F1")
    b = assemble_instances([_obs("column", grid="3-BY")], building_key="main", floor_key="F2")
    assert a[0]["semantic_key"].startswith("column@main:F1:3-BY")
    assert a[0]["semantic_key"] != b[0]["semantic_key"]


def test_non_point_types_not_keyed_by_grid():
    """**关键回归**:墙/管线一格内有多个,不得用轴网格作合并主键。
    (实测误用导致整格并成一个:实体 8094→1059、单实体 722 观测)"""
    obs = [
        _obs("pipe", grid="3-BY", wc=[0.0, 0.0], drawing="a"),
        _obs("pipe", grid="3-BY", wc=[5.0, 5.0], drawing="b"),   # 同格但相距 7m
    ]
    insts = assemble_instances(obs)
    assert len(insts) == 2                       # 不合并
    assert insts[0]["semantic_key"] is None      # 非点状构件无唯一语义键
    assert insts[0]["grid_ref"] == "3-BY"        # 但仍记录轴网(追溯/显示)


def test_point_types_merge_by_grid_when_close():
    """柱:同格 + 位置相近 → 合并(跨图证据累积)。"""
    obs = [
        _obs("column", grid="C-3", wc=[10.0, 10.0], drawing="a"),
        _obs("column", grid="C-3", wc=[10.2, 10.1], drawing="b"),
    ]
    insts = assemble_instances(obs)
    assert len(insts) == 1
    assert len(insts[0]["observations"]) == 2


def test_point_types_same_grid_far_apart_not_merged():
    """**关键**:一格多柱(轴网格约 6×6m)——同格但相距远 → 不同柱。
    (实测柱 3469 根 vs 网格 ~440 个,仅凭格合并会并成 341 根)"""
    obs = [
        _obs("column", grid="C-3", wc=[0.0, 0.0], drawing="a"),
        _obs("column", grid="C-3", wc=[5.0, 5.0], drawing="b"),   # 相距 7m
    ]
    insts = assemble_instances(obs)
    assert len(insts) == 2


def test_point_types_no_coords_fall_back_to_grid():
    """无米坐标时退回"同格即同构件"(无更强证据可用)。"""
    obs = [_obs("column", grid="C-3", wc=None, drawing="a"),
           _obs("column", grid="C-3", wc=None, drawing="b")]
    assert len(assemble_instances(obs)) == 1


def test_partial_grid_recorded_but_not_merge_key():
    """部分网格 "5-?":记录为 grid_ref(配准覆盖),但不作合并键——远处同键不狂并。"""
    obs = [
        _obs("column", grid="5-?", wc=[0.0, 0.0], drawing="a"),
        _obs("column", grid="5-?", wc=[80.0, 80.0], drawing="b"),   # 远 → 不合并
    ]
    insts = assemble_instances(obs)
    assert len(insts) == 2                       # 部分键不狂并
    assert insts[0]["grid_ref"] == "5-?"         # 但记录了部分配准
    assert insts[0]["semantic_key"] is None      # 部分键不作语义键(防唯一碰撞)


def test_partial_grid_close_still_metric_merges():
    """部分网格 + 米坐标近 → 仍按米坐标合并(不因部分键阻断)。"""
    obs = [
        _obs("column", grid="5-?", wc=[10.0, 10.0], drawing="a"),
        _obs("column", grid="5-?", wc=[10.2, 10.1], drawing="b"),
    ]
    insts = assemble_instances(obs)
    assert len(insts) == 1


def test_different_type_same_grid_stay_separate():
    """类型门控:同 grid 不同类型 → 2 实体(不错并)。"""
    obs = [_obs("column", grid="C-3"), _obs("wall", grid="C-3")]
    insts = assemble_instances(obs)
    assert len(insts) == 2


def test_metric_fallback_merges_close_same_type_without_grid():
    """无 grid_cell:同类型 + 米坐标近邻(阈值内)→ 合并。"""
    obs = [
        _obs("column", wc=[10.0, 10.0], drawing="a"),
        _obs("column", wc=[10.3, 10.2], drawing="b"),   # 0.36m < 1.0m → 合并
    ]
    insts = assemble_instances(obs)
    assert len(insts) == 1
    assert len(insts[0]["observations"]) == 2


def test_metric_fallback_keeps_far_apart_separate():
    """无 grid_cell:同类型但米坐标超阈值 → 2 实体(宁分裂待人审)。"""
    obs = [
        _obs("column", wc=[0.0, 0.0]),
        _obs("column", wc=[50.0, 50.0]),   # 远 → 不同构件
    ]
    insts = assemble_instances(obs)
    assert len(insts) == 2


def test_confidence_rises_with_more_observations():
    """概率 OR:多观测置信高于单观测。"""
    single = assemble_instances([_obs("column", grid="A-1", conf=0.8)])
    double = assemble_instances([
        _obs("column", grid="A-1", conf=0.8, drawing="a"),
        _obs("column", grid="A-1", conf=0.8, drawing="b"),
    ])
    assert single[0]["confidence"] == 0.8
    assert double[0]["confidence"] > single[0]["confidence"]   # 0.96 > 0.8


def test_low_confidence_flagged_conflict():
    """置信低于阈值 → review_state=conflict(进人审)。"""
    insts = assemble_instances([_obs("beam", grid="B-2", conf=0.3)])
    assert insts[0]["confidence"] < CONFLICT_CONFIDENCE
    assert insts[0]["review_state"] == "conflict"
    high = assemble_instances([_obs("beam", grid="B-2", conf=0.9)])
    assert high[0]["review_state"] == "auto"


def test_type_label_majority_vote():
    obs = [
        _obs("column", grid="C-3", label="钢立柱", drawing="a"),
        _obs("column", grid="C-3", label="钢立柱", drawing="b"),
        _obs("column", grid="C-3", label="混凝土柱", drawing="c"),
    ]
    insts = assemble_instances(obs)
    assert insts[0]["type_label"] == "钢立柱"


def test_z_fields_left_null_never_defaulted():
    """竖向真实率可度量:Z 一律留空,不默认套。"""
    insts = assemble_instances([_obs("column", grid="C-3")])
    assert insts[0]["z_bottom_m"] is None
    assert insts[0]["z_top_m"] is None
    assert insts[0]["z_source"] is None


def test_representative_outline_from_highest_confidence():
    obs = [
        _obs("slab", grid="A-1", conf=0.6, outline=[[0, 0], [1, 1]], drawing="a"),
        _obs("slab", grid="A-1", conf=0.95, outline=[[2, 2], [3, 3]], drawing="b"),
    ]
    insts = assemble_instances(obs)
    assert insts[0]["outline_m"] == [[2, 2], [3, 3]]   # 取高置信观测轮廓


def test_combine_confidence_formula():
    assert _combine_confidence([0.9]) == 0.9
    assert _combine_confidence([0.5, 0.5]) == 0.75
    assert _combine_confidence([]) == 0.0


def test_semantic_key_unique_for_multiple_columns_in_same_grid():
    """一格多柱 → 语义键须各自唯一(实测 column@north:FD:40-BY 曾唯一约束冲突)。"""
    obs = [
        _obs("column", grid="C-3", wc=[0.0, 0.0], drawing="a"),
        _obs("column", grid="C-3", wc=[5.0, 5.0], drawing="b"),
    ]
    insts = assemble_instances(obs, building_key="north", floor_key="FD")
    keys = [i["semantic_key"] for i in insts]
    assert len(keys) == len(set(keys)) == 2
