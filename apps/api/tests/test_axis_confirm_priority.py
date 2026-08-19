"""待确认分区按**解锁价值**排序 —— 让人先确认最值钱的那几张。

**实测投入产出比**(大歌剧院 v73):

| 无轴网层 | 柱数 | 待确认图 |
|---|---:|---:|
| B1 | **2681** | 5 |
| F6 | 800 | 1 |
| F5 | 522 | 4 |
| FD/B3 | 121 | 3 |

**13 次人工确认 → 4064 根柱获得轴网定位**。

分区号必须人工确认是**有意设计**(§8.0.5 的分区号无法从图内推导),
但「先确认哪张」不该靠人自己翻 —— 那 13 张藏在 800+ 张待确认里。
"""
from __future__ import annotations

import pytest

from services.axis_confirm_priority import rank_pending_zones


@pytest.mark.unit
def test_ranks_by_unlocked_component_count():
    """**核心用例**:解锁构件多的排前面。"""
    rows = [
        {"drawing_id": "d1", "title": "F5 平面", "floor_key": "F5",
         "component_count": 522, "zone_count": 3},
        {"drawing_id": "d2", "title": "B1 平面", "floor_key": "B1",
         "component_count": 2681, "zone_count": 5},
    ]
    ranked = rank_pending_zones(rows)
    assert [r["drawing_id"] for r in ranked] == ["d2", "d1"]
    assert ranked[0]["unlocks"] == 2681


@pytest.mark.unit
def test_same_floor_drawings_share_the_floor_value():
    """**同层多图不重复计价** —— 一层的柱数不因用了 5 张图就算 5 遍。

    否则「确认这张解锁 2681 根」会在 5 张上各报一次,
    人以为能拿 13405 根,实际只有 2681。**高估投入产出比比不排序更糟**。
    """
    rows = [
        {"drawing_id": f"d{i}", "title": f"B1 分图{i}", "floor_key": "B1",
         "component_count": 2681, "zone_count": 2} for i in range(5)
    ]
    ranked = rank_pending_zones(rows)
    assert sum(r["unlocks"] for r in ranked) == 2681, "同层总解锁量不得重复累计"
    assert all(r["floor_key"] == "B1" for r in ranked)


@pytest.mark.unit
def test_effort_is_the_zone_count_not_the_drawing_count():
    """**工作量按分区数算** —— 确认一张 5 分区的图要点 5 次。"""
    rows = [{"drawing_id": "d1", "title": "t", "floor_key": "B1",
             "component_count": 100, "zone_count": 5}]
    assert rank_pending_zones(rows)[0]["effort"] == 5


@pytest.mark.unit
def test_empty_and_malformed_rows_do_not_raise():
    """**脏数据不炸** —— 这是给人看的清单,少一行胜过整页 500。"""
    assert rank_pending_zones([]) == []
    ranked = rank_pending_zones([{"drawing_id": "d1"}, {}, None])
    assert all(r["unlocks"] == 0 for r in ranked)


# ── 端点：从 scene 取楼层构件数，与待确认分区做关联 ──────────────

@pytest.mark.unit
def test_pending_rows_from_scene_and_recognition():
    """**接线用例**:scene 的楼层构件数 × 识别表的待确认分区。

    构件数只能从 scene 取 —— 识别表不知道自己那张图被哪层用了。
    """
    from services.axis_confirm_priority import pending_rows_from_scene

    scene = {"floors": [
        {"key": "B1", "axes": None,
         "elements": {"columns": [{"src": "d1"}, {"src": "d1"}, {"src": "d2"}]}},
        {"key": "F1", "axes": {"x": [["1", 0.0]], "y": []},   # 已有轴网 → 跳过
         "elements": {"columns": [{"src": "d3"}]}},
    ]}
    pending = {"d1": {"title": "B1 分图一", "zone_count": 3},
               "d2": {"title": "B1 分图二", "zone_count": 2},
               "d3": {"title": "F1 平面", "zone_count": 4}}

    rows = pending_rows_from_scene(scene, pending)
    assert {r["drawing_id"] for r in rows} == {"d1", "d2"}, "有轴网的层不该再排队"
    assert all(r["floor_key"] == "B1" for r in rows)
    assert all(r["component_count"] == 3 for r in rows)


@pytest.mark.unit
def test_scene_without_floors_yields_nothing():
    """没有 scene / 没有楼层 → 空清单,不抛异常。"""
    from services.axis_confirm_priority import pending_rows_from_scene

    assert pending_rows_from_scene(None, {}) == []
    assert pending_rows_from_scene({"floors": []}, {"d": {}}) == []
