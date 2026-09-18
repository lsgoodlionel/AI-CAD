"""场景 JSON → 规范化视图：不补数、不改形，缺什么就是缺什么。

这一层最容易犯的错是「顺手把数补圆」—— 标高缺了就按 4.5m 估一个、
层高算不出就填个默认值。那样一来，下游规则拿到的是**我编的数**，
它判出的「合理」也就毫无意义。
"""
from __future__ import annotations

import pytest

from core.model3d.plausibility.model import ELEMENT_KINDS, Element, from_scene


def _scene(floors):
    return {"version": 7, "buildings": [{"key": "main", "label": "主楼", "floors": floors}]}


@pytest.mark.unit
def test_elements_get_stable_uids_and_keep_their_raw_fields():
    scene = _scene([{"key": "F1", "label": "一层", "order": 0, "elevation_m": 0.0,
                     "elements": {"columns": [{"outline": [[0, 0], [1, 0], [1, 1], [0, 1]]}],
                                  "walls": [{"path": [[0, 0], [5, 0]], "width": 0.2}]}}])
    model = from_scene(scene)
    uids = [e.uid for e in model.elements()]

    assert uids == ["main/F1/columns/0", "main/F1/walls/0"]
    wall = next(model.elements("walls"))
    assert wall.width_m == 0.2
    assert wall.length_m() == pytest.approx(5.0)


@pytest.mark.unit
def test_floor_height_comes_from_the_elevation_difference():
    model = from_scene(_scene([
        {"key": "F1", "order": 0, "elevation_m": 0.0, "elements": {}},
        {"key": "F2", "order": 1, "elevation_m": 4.5, "elements": {}},
        {"key": "F3", "order": 2, "elevation_m": 9.0, "elements": {}},
    ]))
    heights = [f.height_m for f in model.floors()]
    assert heights[:2] == [pytest.approx(4.5), pytest.approx(4.5)]
    assert heights[2] == pytest.approx(4.5), "顶层沿用下一层（没有更上层可减）"


@pytest.mark.unit
def test_missing_elevation_leaves_height_unknown_instead_of_guessing():
    model = from_scene(_scene([
        {"key": "F1", "order": 0, "elevation_m": None, "elements": {}},
        {"key": "F2", "order": 1, "elevation_m": 4.5, "elements": {}},
    ]))
    assert [f.height_m for f in model.floors()][0] is None


@pytest.mark.unit
def test_estimated_elevation_flag_is_carried_through():
    """标高是估的还是实测的，必须一路带到规则手里 —— 拿估的标高去否定
    构件，等于用自己编的数做判据。"""
    model = from_scene(_scene([
        {"key": "F1", "order": 0, "elevation_m": 0.0, "elevation_estimated": False,
         "elements": {}}]))
    assert next(model.floors()).elevation_estimated is False


@pytest.mark.unit
def test_min_area_rect_is_used_for_sides_not_the_axis_aligned_box():
    """实测教训：标高符号 `∨` 的一条斜笔画按轴对齐盒量是 0.52×0.59m
    近方形（像柱），按最小外接矩形量是 0.70×0.12m（不像柱）。"""
    stroke = Element("u", "columns", "main", "F1",
                     {"outline": [[0, 0], [0.7, 0.12], [0.69, 0.13], [-0.01, 0.01]]})
    long_side, short_side = stroke.sides_m()
    assert long_side > 0.6 and short_side < 0.2


@pytest.mark.unit
def test_line_elements_get_a_footprint_from_their_width():
    wall = Element("u", "walls", "main", "F1", {"path": [[0, 0], [4, 0]], "width": 0.3})
    ring = wall.footprint()
    assert len(ring) == 4
    assert max(p[1] for p in ring) - min(p[1] for p in ring) == pytest.approx(0.3)


@pytest.mark.unit
def test_malformed_scene_yields_an_empty_model_instead_of_raising():
    for bad in (None, {}, {"buildings": None}, {"buildings": [{"floors": [{"elements": 5}]}]}):
        assert from_scene(bad).count() == 0


@pytest.mark.unit
def test_every_declared_kind_is_collected():
    elements = {kind: [{"outline": [[0, 0], [1, 0], [1, 1]]}] for kind in ELEMENT_KINDS}
    model = from_scene(_scene([{"key": "F1", "order": 0, "elements": elements}]))
    assert model.count() == len(ELEMENT_KINDS)
