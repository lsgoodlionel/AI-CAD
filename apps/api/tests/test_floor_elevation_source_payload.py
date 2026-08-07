"""`scene.floors[]` 必须带标高来源 —— 否则前端的门禁标签永远不显示。

**实测缺陷**(模型 v38):

前端 `BrowseModePanels.tsx` 按层显示「标高来自图纸」/「标高为默认值」,
读的是 `floor.elevation_estimated` 与 `floor.elevation_source`。
而 `scene.floors[]` 里**这两个字段都不存在**:

```
{"key": "F1", "label": "1层", "elevation_m": 0.0, ...}   ← 没有 elevation_source
```

`elevation_source` 只写进了 `scene.quality.story_tables[unit][]`,
那是**另一套结构**,前端楼层列表不读它。两个分支都是 falsy ⇒
**什么标签都不显示**,图纸值与默认值在界面上又长得一模一样
—— 正是这条门禁本来要解决的问题。

(上一轮记录里写的「已透传到 `scene.floors[].elevation_source`」是虚报。)

**合并规则要保守**:一层可能由多个单体贡献(如 south/north 各有 F1)。
只要**有任一单体的标高是默认值**,就必须标成 `elevation_estimated=True`
—— 部分是猜的,就不能说「来自图纸」。
"""
from __future__ import annotations

import pytest

from services.model_builder import attach_floor_elevation_source
from services.model_story import (
    ELEVATION_SOURCE_DEFAULT, ELEVATION_SOURCE_DRAWING, StoryLevel,
)


def _level(story_key: str, source: str, estimated: bool) -> StoryLevel:
    return StoryLevel(
        building_unit_key="u", story_key=story_key, display_name=story_key,
        story_order=1, elevation_m=0.0, height_m=4.5, source="x",
        confidence=1.0, display_building_name="U",
        elevation_source=source, elevation_estimated=estimated,
    )


class _Norm:
    def __init__(self, stories: dict) -> None:
        self.stories_by_building = stories


@pytest.mark.unit
def test_floor_gets_the_source_from_its_story_level():
    """**核心用例**:图纸来源的层要能被前端认出来。"""
    floors = [{"key": "F1", "building_units": ["u"]}]
    norm = _Norm({"u": [_level("F1", "level_elevation_pairing", False)]})

    attach_floor_elevation_source(floors, norm)

    assert floors[0]["elevation_source"] == "level_elevation_pairing"
    assert floors[0]["elevation_estimated"] is False


@pytest.mark.unit
def test_default_source_is_marked_estimated():
    floors = [{"key": "F9", "building_units": ["u"]}]
    norm = _Norm({"u": [_level("F9", ELEVATION_SOURCE_DEFAULT, True)]})

    attach_floor_elevation_source(floors, norm)

    assert floors[0]["elevation_estimated"] is True


@pytest.mark.unit
def test_any_estimated_unit_makes_the_whole_floor_estimated():
    """**保守合并**:south 读自图纸、north 是默认值 ⇒ 整层算「有默认值」。

    部分是猜的就不能说「来自图纸」—— 界面上不该给出比实际更强的保证。
    """
    floors = [{"key": "F1", "building_units": ["south", "north"]}]
    norm = _Norm({
        "south": [_level("F1", ELEVATION_SOURCE_DRAWING, False)],
        "north": [_level("F1", ELEVATION_SOURCE_DEFAULT, True)],
    })

    attach_floor_elevation_source(floors, norm)

    assert floors[0]["elevation_estimated"] is True


@pytest.mark.unit
def test_all_units_measured_stays_unestimated():
    floors = [{"key": "F1", "building_units": ["south", "north"]}]
    norm = _Norm({
        "south": [_level("F1", ELEVATION_SOURCE_DRAWING, False)],
        "north": [_level("F1", "level_elevation_pairing", False)],
    })

    attach_floor_elevation_source(floors, norm)

    assert floors[0]["elevation_estimated"] is False
    # 两个单体的来源不同 ⇒ 报 `mixed` + 明细（见下方多单体用例）。
    # 关键是**都不是估算**，所以不该出现「标高为默认值」的标签。
    assert floors[0]["elevation_source"] == "mixed"
    assert floors[0]["elevation_sources"] == [
        ELEVATION_SOURCE_DRAWING, "level_elevation_pairing"]


@pytest.mark.unit
def test_floor_without_a_matching_level_gets_no_field():
    """**判不出就不写** —— 写个假的比不写更糟,前端会显示错误的保证。"""
    floors = [{"key": "UNZONED", "building_units": ["u"]}]
    norm = _Norm({"u": [_level("F1", ELEVATION_SOURCE_DRAWING, False)]})

    attach_floor_elevation_source(floors, norm)

    assert "elevation_source" not in floors[0]
    assert "elevation_estimated" not in floors[0]


@pytest.mark.unit
def test_missing_building_units_is_safe():
    floors = [{"key": "F1"}]
    attach_floor_elevation_source(floors, _Norm({"u": [_level("F1", "d", False)]}))
    assert "elevation_source" not in floors[0]


@pytest.mark.unit
def test_empty_inputs_are_safe():
    attach_floor_elevation_source([], _Norm({}))
    floors = [{"key": "F1", "building_units": ["u"]}]
    attach_floor_elevation_source(floors, _Norm({}))
    assert "elevation_source" not in floors[0]


@pytest.mark.unit
def test_field_names_match_the_frontend_contract():
    """字段名必须与 `BrowseModePanels.tsx` 读的完全一致。

    这条测试的意义:改名会让标签**静默消失**,不会有任何报错。
    """
    floors = [{"key": "F1", "building_units": ["u"]}]
    attach_floor_elevation_source(
        floors, _Norm({"u": [_level("F1", ELEVATION_SOURCE_DRAWING, False)]}))
    assert set(floors[0]) >= {"elevation_source", "elevation_estimated"}


# ── 多单体时的来源表达 ────────────────────────────────────────

@pytest.mark.unit
def test_mixed_sources_are_reported_as_mixed():
    """**一层多单体、来源不同时不能只报第一个**。

    实测 v39:F1 报 `level_elevation_pairing`(来自 north),
    其余层报 `manual`(来自 main)—— 看不出这一层的**哪个单体**是图纸读的。
    只报第一个会让人以为整层都是那个来源。
    """
    floors = [{"key": "F1", "building_units": ["north", "south"]}]
    norm = _Norm({
        "north": [_level("F1", "level_elevation_pairing", False)],
        "south": [_level("F1", ELEVATION_SOURCE_DRAWING, False)],
    })
    attach_floor_elevation_source(floors, norm)
    assert floors[0]["elevation_source"] == "mixed"
    assert floors[0]["elevation_sources"] == [
        ELEVATION_SOURCE_DRAWING, "level_elevation_pairing"]


@pytest.mark.unit
def test_single_source_is_reported_directly():
    """来源一致时照常报该来源,不必让界面处理 `mixed`。"""
    floors = [{"key": "F1", "building_units": ["north", "south"]}]
    norm = _Norm({
        "north": [_level("F1", "level_elevation_pairing", False)],
        "south": [_level("F1", "level_elevation_pairing", False)],
    })
    attach_floor_elevation_source(floors, norm)
    assert floors[0]["elevation_source"] == "level_elevation_pairing"
    assert floors[0]["elevation_sources"] == ["level_elevation_pairing"]


@pytest.mark.unit
def test_sources_list_is_sorted_for_determinism():
    """列表必须定序 —— 集合迭代顺序会让同样的数据产出不同的 scene。"""
    floors_a = [{"key": "F1", "building_units": ["a", "b", "c"]}]
    floors_b = [{"key": "F1", "building_units": ["c", "b", "a"]}]
    norm = _Norm({
        "a": [_level("F1", "s3", False)], "b": [_level("F1", "s1", False)],
        "c": [_level("F1", "s2", False)],
    })
    attach_floor_elevation_source(floors_a, norm)
    attach_floor_elevation_source(floors_b, norm)
    assert floors_a[0]["elevation_sources"] == floors_b[0]["elevation_sources"]
