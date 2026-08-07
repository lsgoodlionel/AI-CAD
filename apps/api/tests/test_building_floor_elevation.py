"""各单体的楼层要用**自己**的标高 —— 否则实测值到不了 3D。

**实测缺陷**(模型 v41,上海大歌剧院):

| 数据位置 | north 的 RF 标高 |
|---|---:|
| `quality.story_tables['north']` | **25.00**(图纸实测,已修好) |
| `scene.floors[]`(汇总层) | 33.90(main 的累加值) |
| **`buildings['north'].floors[]`** | **33.90** ← 3D 渲染读这里 |

`group_buildings` 按单体分了图纸，却写 `elevation_m: floor.get("elevation_m")`
—— **直接复制汇总层的值**。于是三个单体的同层号共用一个标高，
north 读出来的 25.00 从未到达渲染层。

大歌剧院的三个单体层高本就不同(north RF 25.00 vs main 33.90，差 **8.9 米**)，
共用一个数就是把两个单体摞错位置。

**规则**：单体楼层优先用该单体在 `stories_by_building` 里的标高；
查不到才退回汇总值，并且**不谎报 provenance**。
"""
from __future__ import annotations

import pytest

from services.model_elements import group_buildings


def _floor(key: str, order: int, elevation_m: float, drawing_ids: list[str]) -> dict:
    return {"key": key, "label": key, "elevation": order, "order": order,
            "elevation_m": elevation_m,
            "drawings": [{"drawing_id": d} for d in drawing_ids],
            "elements": {}}


def _drawing(did: str, unit: str) -> dict:
    return {"id": did, "drawing_no": did, "title": did,
            "building_unit_key": unit}


_ASSIGN = {
    "n1": {"building_unit_key": "north", "building_display_name": "小歌剧厅"},
    "m1": {"building_unit_key": "main", "building_display_name": "主楼"},
}


class _Level:
    def __init__(self, story_key: str, elevation_m: float,
                 estimated: bool = False) -> None:
        self.story_key = story_key
        self.elevation_m = elevation_m
        self.elevation_estimated = estimated
        self.elevation_source = "level_elevation_pairing"


@pytest.mark.unit
def test_each_unit_uses_its_own_measured_elevation():
    """**核心用例**:north 的 RF 用 25.00,不跟着 main 的 33.90 走。"""
    floors = [_floor("RF", 99, 33.9, ["n1", "m1"])]
    got = group_buildings(
        floors, [_drawing("n1", "north"), _drawing("m1", "main")], "歌剧院",
        normalized_assignments=_ASSIGN,
        stories_by_building={
            "north": [_Level("RF", 25.0)],
            "main": [_Level("RF", 33.9)],
        })
    by_key = {b["key"]: b for b in got}
    assert by_key["north"]["floors"][0]["elevation_m"] == pytest.approx(25.0)
    assert by_key["main"]["floors"][0]["elevation_m"] == pytest.approx(33.9)


@pytest.mark.unit
def test_unit_elevation_carries_its_own_provenance():
    floors = [_floor("F2", 2, 4.5, ["n1"])]
    got = group_buildings(
        floors, [_drawing("n1", "north")], "歌剧院",
        normalized_assignments=_ASSIGN,
        stories_by_building={"north": [_Level("F2", 3.05, estimated=False)]})
    floor = got[0]["floors"][0]
    assert floor["elevation_m"] == pytest.approx(3.05)
    assert floor["elevation_estimated"] is False
    assert floor["elevation_source"] == "level_elevation_pairing"


@pytest.mark.unit
def test_estimated_unit_elevation_is_marked():
    floors = [_floor("FD", -98, -16.8, ["n1"])]
    got = group_buildings(
        floors, [_drawing("n1", "north")], "歌剧院",
        normalized_assignments=_ASSIGN,
        stories_by_building={"north": [_Level("FD", -13.5, estimated=True)]})
    assert got[0]["floors"][0]["elevation_estimated"] is True


@pytest.mark.unit
def test_falls_back_to_the_aggregate_when_the_unit_has_no_level():
    """**查不到就退回汇总值,但不谎报 provenance**。

    退回来的值不属于这个单体,所以不能声称它是该单体的实测标高。
    """
    floors = [_floor("F9", 9, 40.0, ["n1"])]
    got = group_buildings(
        floors, [_drawing("n1", "north")], "歌剧院",
        normalized_assignments=_ASSIGN,
        stories_by_building={"north": [_Level("F1", 0.0)]})
    floor = got[0]["floors"][0]
    assert floor["elevation_m"] == pytest.approx(40.0)
    assert "elevation_source" not in floor


@pytest.mark.unit
def test_backward_compatible_without_stories():
    """不传 `stories_by_building` 时行为不变 —— 老调用方照常工作。"""
    floors = [_floor("F1", 1, 0.0, ["n1"])]
    got = group_buildings(floors, [_drawing("n1", "north")], "歌剧院",
                          normalized_assignments=_ASSIGN)
    assert got[0]["floors"][0]["elevation_m"] == pytest.approx(0.0)


@pytest.mark.unit
def test_empty_stories_map_is_safe():
    floors = [_floor("F1", 1, 0.0, ["n1"])]
    got = group_buildings(floors, [_drawing("n1", "north")], "歌剧院",
                          normalized_assignments=_ASSIGN,
                          stories_by_building={})
    assert got[0]["floors"][0]["elevation_m"] == pytest.approx(0.0)
