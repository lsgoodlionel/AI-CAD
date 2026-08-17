"""未分层图的分类要**出现在图纸管理里**（用户第 2 项要求）。

> 「无法定位的图在图纸管理里给出标签分类，然后人工处理，或人工补充说明后，
>   再次系统处理，循环直至处理完毕」

上一轮把 `classify_unzoned` 接进了 builder，但分类只写进 `scene.quality`
——只有工程模型页读得到，而人是在**图纸管理**里看图的。
这是「上游算对了下游不读」的又一次，只不过这次断点在前端入口。

本模块给出图纸管理页要的形态：一次拿到全项目的定位状态，
每张图带 `reason` / `action` / `needs_floor_input`，
让人能**按类筛选**、成批处理，而不是逐张翻。

聚合口径要与模型页一致 —— 两处报的数字不一样，人就不知道该信哪个。
"""
from __future__ import annotations

import pytest

from services.drawing_location_status import summarize_location_status


def _unclassified(drawing_id: str, title: str) -> dict:
    return {"drawing_id": drawing_id, "drawing_no": "", "title": title,
            "building_unit_key": "main", "reason": "story_unclassified"}


@pytest.mark.unit
def test_each_drawing_carries_its_reason_and_action():
    """人要知道**这一张**为什么判不出、该做什么。"""
    got = summarize_location_status([_unclassified("d1", "建筑-竣工图--台仓平面图")])
    item = got["items"][0]
    assert item["reason"] == "non_standard_floor_name"
    assert item["action"], "要说清人该做什么"
    assert item["needs_floor_input"] is True


@pytest.mark.unit
def test_counts_are_grouped_by_reason():
    """**按类计数**才能看出该先处理哪一类。"""
    got = summarize_location_status([
        _unclassified("d1", "建筑-竣工图--台仓平面图"),
        _unclassified("d2", "结构-竣工图--竖向构件定位图"),
        _unclassified("d3", "01施工总说明-dq-总说明"),
    ])
    by_reason = got["by_reason"]
    assert by_reason["non_standard_floor_name"] == 1
    assert by_reason["cross_floor"] == 1
    assert by_reason["no_floor_by_nature"] == 1


@pytest.mark.unit
def test_actionable_count_excludes_drawings_without_a_floor():
    """**「本就没有」不该计进待办** —— 这是 building_unit_fallback 的教训。

    当时原报「1866 张未分配」，拆开后 959 张本就无单体归属，
    真正要处理的只有 907 张 —— **虚高 2.1 倍**。
    混在一起报，会让人去处理一个不存在的问题。
    """
    got = summarize_location_status([
        _unclassified("d1", "建筑-竣工图--台仓平面图"),        # 要人填
        _unclassified("d2", "结构-竣工图--竖向构件定位图"),    # 跨层，不填
        _unclassified("d3", "01施工总说明-dq-总说明"),          # 本就无楼层
    ])
    assert got["actionable"] == 1, "只有真正需要人填楼层的才算待办"
    assert got["total"] == 3, "总数照实报，不藏"


@pytest.mark.unit
def test_empty_input_is_safe():
    got = summarize_location_status([])
    assert got["total"] == 0
    assert got["actionable"] == 0
    assert got["items"] == []
    assert got["by_reason"] == {}


@pytest.mark.unit
def test_none_input_is_safe():
    assert summarize_location_status(None)["total"] == 0


@pytest.mark.unit
def test_original_fields_are_preserved():
    """图号/图名要留着 —— 人靠它认图。"""
    got = summarize_location_status([
        {"drawing_id": "d1", "drawing_no": "A-10-01.1C",
         "title": "大歌剧厅台仓平面图", "building_unit_key": "main"},
    ])
    assert got["items"][0]["drawing_no"] == "A-10-01.1C"
    assert got["items"][0]["building_unit_key"] == "main"


@pytest.mark.unit
def test_placeholder_reason_is_replaced():
    """原先每条都是同一个占位值 `story_unclassified`，等于没说。"""
    got = summarize_location_status([_unclassified("d1", "建筑-竣工图--台仓平面图")])
    assert got["items"][0]["reason"] != "story_unclassified"


@pytest.mark.unit
def test_hint_is_carried_for_non_standard_names():
    """把识别到的非标准名回显 —— 人只需告知它对应哪一层，不必翻图。"""
    got = summarize_location_status([_unclassified("d1", "建筑-竣工图--台仓平面图")])
    assert got["items"][0].get("hint") == "台仓"
