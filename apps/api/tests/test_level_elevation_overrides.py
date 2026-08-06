"""楼层名↔标高配对 → 建模 z_overrides。

**这是 P2 接线**(见 `docs/MODELING_PIPELINE_BLUEPRINT.md`):
配对模块已经能从立面/剖面图读出**带名字**的楼层标高,
但一直没接进 `model_story`,于是模型 13 层里 10 层仍是
`DEFAULT_STORY_HEIGHT_M = 4.5` 硬推的,最大偏差 **11.9 米**。

**为什么按名字匹配而不是按位置**:现有 `section_z_recovery` 用序列窗口对齐
(第 n 个标高配第 n 层),一旦某层漏读整条就错位。
而立面图上写的是 `6F（设备层） 36.800` —— 名字直接给出归属,不用猜。
"""
from __future__ import annotations

import pytest

from services.level_elevation_overrides import (
    build_z_overrides, story_key_for_level_name,
)


# ── 楼层名 → story_key ────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.parametrize("name,key", [
    ("1F", "F1"), ("F1", "F1"), ("4F", "F4"), ("12F", "F12"),
    ("B1", "B1"), ("B2", "B2"), ("RF", "RF"),
])
def test_plain_marks_map_to_story_keys(name, key):
    assert story_key_for_level_name(name) == key


@pytest.mark.unit
@pytest.mark.parametrize("name,key", [
    ("大歌剧厅4F", "F4"),          # 实测 A-20-02A 南立面标高链
    ("大歌剧厅3F", "F3"),
    ("6F（设备层）", "F6"),         # 括号后缀
    ("6F（设备层", "F6"),           # OCR 丢了右括号 —— 实测就是这样
    ("5F （设备层）", "F5"),        # 中间有空格
    ("小歌剧厅B1", "B1"),
])
def test_prefixed_and_suffixed_marks_are_stripped(name, key):
    """实测的楼层名都带部位前缀或功能后缀,且 OCR 常丢括号。"""
    assert story_key_for_level_name(name) == key


@pytest.mark.unit
@pytest.mark.parametrize("name,key", [
    ("地下二层", "B2"), ("地下一层", "B1"), ("首层", "F1"),
    ("三层", "F3"), ("屋面", "RF"), ("屋顶层", "RF"),
])
def test_chinese_level_names_map_too(name, key):
    assert story_key_for_level_name(name) == key


@pytest.mark.unit
@pytest.mark.parametrize("name", [
    "前厅", "男卫", "屋面做法了", "大歌剧厅顶板", "", "不上人屋面区域做法3",
])
def test_non_level_text_maps_to_nothing(name):
    """**不能硬凑**:配不上就返回 None。

    `屋面做法了` 是 OCR 错字的「屋面做法3」,`大歌剧厅顶板` 是构件名 ——
    它们都不是楼层,给它们配标高会让整层构件放错高度。
    """
    assert story_key_for_level_name(name) is None


# ── 配对 → z_overrides ────────────────────────────────────────────

STORIES = [
    {"building_unit_key": "main", "story_key": "B1"},
    {"building_unit_key": "main", "story_key": "F1"},
    {"building_unit_key": "main", "story_key": "F3"},
    {"building_unit_key": "main", "story_key": "F4"},
    {"building_unit_key": "main", "story_key": "F6"},
]


@pytest.mark.unit
def test_builds_overrides_for_matched_stories():
    pairs = [{"level_name": "大歌剧厅3F", "elevation_m": 10.300},
             {"level_name": "大歌剧厅4F", "elevation_m": 16.100},
             {"level_name": "6F（设备层", "elevation_m": 36.800}]
    # 显式放开佐证门槛:本例测的是**映射逻辑**,不是佐证强度
    got = build_z_overrides(pairs, STORIES, min_samples=1)
    assert got[("main", "F3")]["elevation_bottom_m"] == pytest.approx(10.300)
    assert got[("main", "F4")]["elevation_bottom_m"] == pytest.approx(16.100)
    assert got[("main", "F6")]["elevation_bottom_m"] == pytest.approx(36.800)


@pytest.mark.unit
def test_override_declares_its_source():
    """来源必须可追溯 —— 否则分不清哪些标高是图纸读的、哪些是默认值。"""
    got = build_z_overrides([{"level_name": "1F", "elevation_m": 0.0}],
                            STORIES, min_samples=1)
    assert got[("main", "F1")]["source"] == "level_elevation_pairing"


@pytest.mark.unit
def test_unknown_story_key_is_dropped():
    """配出的层不在楼层表里 —— 丢掉,不凭空造层。"""
    got = build_z_overrides(
        [{"level_name": "9F", "elevation_m": 99.0}], STORIES)
    assert got == {}


@pytest.mark.unit
def test_conflicting_values_for_one_story_are_rejected():
    """同一层配出两个差得远的标高 —— **两个都不要**。

    实测:大歌剧厅 3F=10.300,而 7-7 剖面 3F=10.800 ——
    一个项目有多套标高体系。混着取会得到一个既不是这个也不是那个的值,
    宁可留默认值等人工,也不能瞎选一个。
    """
    pairs = [{"level_name": "3F", "elevation_m": 10.300},
             {"level_name": "大歌剧厅3F", "elevation_m": 10.800}]
    assert ("main", "F3") not in build_z_overrides(pairs, STORIES)


@pytest.mark.unit
def test_close_duplicates_are_averaged_not_rejected():
    """同一层多张图读出几乎一样的值 —— 取均值,不算冲突。"""
    pairs = [{"level_name": "3F", "elevation_m": 10.300},
             {"level_name": "大歌剧厅3F", "elevation_m": 10.302}]
    got = build_z_overrides(pairs, STORIES)
    assert got[("main", "F3")]["elevation_bottom_m"] == pytest.approx(10.301)


@pytest.mark.unit
def test_empty_inputs_are_safe():
    assert build_z_overrides([], STORIES) == {}
    assert build_z_overrides([{"level_name": "1F", "elevation_m": 0.0}], []) == {}


# ── 单体上下文 + 国标一致性校验 ────────────────────────────────

@pytest.mark.unit
def test_same_story_in_different_units_does_not_conflict():
    """**这是 P2 能跑通的关键**:同一层号在不同单体是不同标高,不是冲突。

    实测:north(小歌剧厅)F3=9.350(12 张图一致),
    south(大歌剧厅)F3=10.300。混在一起会被判成冲突而**两个都丢掉**。
    """
    stories = [{"building_unit_key": "north", "story_key": "F3"},
               {"building_unit_key": "south", "story_key": "F3"}]
    pairs = [{"level_name": "3F", "elevation_m": 9.350, "building_unit_key": "north"},
             {"level_name": "3F", "elevation_m": 10.300, "building_unit_key": "south"}]
    got = build_z_overrides(pairs, stories, min_samples=1)
    assert got[("north", "F3")]["elevation_bottom_m"] == pytest.approx(9.350)
    assert got[("south", "F3")]["elevation_bottom_m"] == pytest.approx(10.300)


@pytest.mark.unit
def test_pairs_without_a_unit_go_to_every_matching_story():
    """没带单体的配对退回原行为(所有同名层),保持向后兼容。"""
    stories = [{"building_unit_key": "main", "story_key": "F3"}]
    got = build_z_overrides([{"level_name": "3F", "elevation_m": 10.3}],
                            stories, min_samples=1)
    assert ("main", "F3") in got


@pytest.mark.unit
@pytest.mark.parametrize("story_key,bad_elevation", [
    ("B1", 5.500), ("B2", 9.300), ("B3", 3.800),
])
def test_basement_elevation_must_be_negative(story_key, bad_elevation):
    """**地下层标高必须 ≤ 0**(相对 ±0.000 的定义)。

    实测 north 的 `B1` 同时读出 **−5.500 和 +5.500**、
    `B2` 读出 **−9.300 和 +9.300** —— 正值显然是把别的东西
    (轴号 `B1`、编号)当成了楼层标记。
    国标 §11.8.5 规定负数标高注「−」,正负号本身就是信息。
    """
    stories = [{"building_unit_key": "north", "story_key": story_key}]
    pairs = [{"level_name": story_key, "elevation_m": bad_elevation,
              "building_unit_key": "north"}]
    assert build_z_overrides(pairs, stories) == {}


@pytest.mark.unit
def test_negative_basement_elevation_is_accepted():
    stories = [{"building_unit_key": "north", "story_key": "B1"}]
    pairs = [{"level_name": "B1", "elevation_m": -5.500,
              "building_unit_key": "north"}]
    got = build_z_overrides(pairs, stories, min_samples=1)
    assert got[("north", "B1")]["elevation_bottom_m"] == pytest.approx(-5.5)


@pytest.mark.unit
def test_ground_floor_must_be_near_zero():
    """`F1` 是 ±0.000 基准层,读出 1.000 说明配错了。"""
    stories = [{"building_unit_key": "north", "story_key": "F1"}]
    assert build_z_overrides(
        [{"level_name": "1F", "elevation_m": 1.000,
          "building_unit_key": "north"}], stories) == {}
    assert build_z_overrides(
        [{"level_name": "1F", "elevation_m": 0.100,
          "building_unit_key": "north"}], stories, min_samples=1)


@pytest.mark.unit
def test_upper_floor_must_be_above_ground():
    """`F2` 及以上必须 > 0。"""
    stories = [{"building_unit_key": "north", "story_key": "F2"}]
    assert build_z_overrides(
        [{"level_name": "2F", "elevation_m": -3.0,
          "building_unit_key": "north"}], stories) == {}


@pytest.mark.unit
def test_single_sample_is_rejected_by_default():
    """**孤证不立**。

    实测:north 的 F2/F3/F5/RF 各有 **12 张图**给出完全一致的值,
    而 main 的 `F3=2.944`、`RF=23.400` 只有 **1 张**佐证 ——
    2.944 明显不像楼层标高,更像某个尺寸被配错了。

    一张图配出来的值没有交叉印证,风险高于留默认值。
    """
    stories = [{"building_unit_key": "main", "story_key": "F3"}]
    pairs = [{"level_name": "3F", "elevation_m": 2.944,
              "building_unit_key": "main"}]
    assert build_z_overrides(pairs, stories) == {}


@pytest.mark.unit
def test_two_consistent_samples_are_accepted():
    stories = [{"building_unit_key": "north", "story_key": "F3"}]
    pairs = [{"level_name": "3F", "elevation_m": 9.350, "building_unit_key": "north"},
             {"level_name": "三层", "elevation_m": 9.350, "building_unit_key": "north"}]
    got = build_z_overrides(pairs, stories)
    assert got[("north", "F3")]["sample_count"] == 2


@pytest.mark.unit
def test_min_samples_is_configurable():
    """强证据要求可调 —— 小项目可能每层只有一张立面图。"""
    stories = [{"building_unit_key": "main", "story_key": "F3"}]
    pairs = [{"level_name": "3F", "elevation_m": 10.3, "building_unit_key": "main"}]
    assert build_z_overrides(pairs, stories, min_samples=1)
