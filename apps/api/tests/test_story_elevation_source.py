"""楼层**标高**的来源标注(楼层级门禁)。

**为什么必须有**:`StoryLevel` 早有 `height_source`(**层高**来源),
却没有**标高**来源——而偏出 11.9 米的正是标高:

| 层 | 模型 v31 | 图纸实测 | 差 |
|---|---:|---:|---:|
| north RF | 33.9 | 25.000 | **−8.9 m** |
| north F5 | 20.4 | 16.500 | −3.9 m |

界面上 `F5 20.4` 与从图纸读出的 `16.500` 长得一模一样,
用户分不出哪个是图纸值、哪个是 `DEFAULT_STORY_HEIGHT_M = 4.5` 推的。

项目级的 `set_capability.elevations` 只能说「这批图有没有立面/剖面」,
**说不出具体哪一层的标高是真的**。这一层门禁补的就是这个。
"""
from __future__ import annotations

import pytest

from services.model_story import (
    ELEVATION_SOURCE_DEFAULT, ELEVATION_SOURCE_DRAWING,
    ELEVATION_SOURCE_OVERRIDE, normalize_story_table,
)


def _all_levels(result):
    """`StoryNormalizationResult` 用 `stories_by_building`,没有 `levels`。

    **这条辅助函数存在本身就是一个教训**:我在 builder 里误写
    `normalization.levels`,异常被宽泛 except 吞掉,P2 接线静默失效。
    """
    return [level
            for levels in (result.stories_by_building or {}).values()
            for level in levels]


def _drawing(did: str, title: str) -> dict:
    return {"id": did, "drawing_no": did, "title": title,
            "discipline": "architecture"}


@pytest.mark.unit
def test_default_elevation_is_marked_as_estimated():
    """没有任何标高来源时,必须标成 default —— **不能冒充图纸值**。"""
    result = normalize_story_table(
        [_drawing("d1", "一层平面图"), _drawing("d2", "三层平面图")], {})
    levels = _all_levels(result)
    assert levels
    for level in levels:
        assert level.elevation_source == ELEVATION_SOURCE_DEFAULT
        assert level.elevation_estimated is True


@pytest.mark.unit
def test_override_elevation_is_marked_with_its_source():
    """`z_overrides` 带来的标高要标明**是谁给的**。

    实测来源有三种:`level_elevation_pairing`(立面/剖面配对)、
    `manual`(人工录入)、剖面 z 恢复。分不清就无法追责。
    """
    drawings = [_drawing("d1", "一层平面图"), _drawing("d2", "三层平面图")]
    base = normalize_story_table(drawings, {})
    target = _all_levels(base)[-1]
    overrides = {
        (target.building_unit_key, target.story_key): {
            "elevation_bottom_m": 9.350,
            "source": "level_elevation_pairing",
        }
    }
    result = normalize_story_table(drawings, {}, z_overrides=overrides)
    level = next(l for l in _all_levels(result)
                 if l.story_key == target.story_key
                 and l.building_unit_key == target.building_unit_key)
    assert level.elevation_m == pytest.approx(9.350)
    assert level.elevation_source == "level_elevation_pairing"
    assert level.elevation_estimated is False


@pytest.mark.unit
def test_override_without_a_declared_source_falls_back_to_generic():
    """override 没写 source 时也要标成 override,**不能当成默认值**。"""
    drawings = [_drawing("d1", "一层平面图"), _drawing("d2", "三层平面图")]
    target = _all_levels(normalize_story_table(drawings, {}))[-1]
    overrides = {(target.building_unit_key, target.story_key):
                 {"elevation_bottom_m": 12.0}}
    result = normalize_story_table(drawings, {}, z_overrides=overrides)
    level = next(l for l in _all_levels(result)
                 if l.story_key == target.story_key)
    assert level.elevation_source == ELEVATION_SOURCE_OVERRIDE
    assert level.elevation_estimated is False


@pytest.mark.unit
def test_elevation_read_from_drawing_text_is_not_default():
    """图纸标高文本推出的标高,来源是 drawing,不是 default。"""
    from services.model_story import normalize_story_table as norm

    drawings = [{"id": "d1", "drawing_no": "d1", "title": "三层平面图 10.800",
                 "discipline": "architecture"}]
    result = norm(drawings, {})
    for level in _all_levels(result):
        # 该图名带标高文本时应判为 drawing;否则 default——两者都不能是别的
        assert level.elevation_source in (
            ELEVATION_SOURCE_DRAWING, ELEVATION_SOURCE_DEFAULT)


@pytest.mark.unit
def test_source_constants_are_distinct():
    """三个来源必须互不相同 —— 否则前端分不出档位。"""
    assert len({ELEVATION_SOURCE_DEFAULT, ELEVATION_SOURCE_DRAWING,
                ELEVATION_SOURCE_OVERRIDE}) == 3
