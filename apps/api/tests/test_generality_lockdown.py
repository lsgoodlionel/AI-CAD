"""通用性锁定 —— 用**合成第二工程**验证判据不依赖大歌剧院特征。

用户复核口径:「只是局部针对某特定工程提高无用,需要通用性识图信息
获取建模能力的系统级能力锁定」。

本文件模拟一个与大歌剧院处处相反的工程:
- 工程坐标是**正值**城市坐标系(X≈40000,大歌剧院是 −6300);
- 单体命名是住宅式「A栋/B栋」(大歌剧院是「××厅」);
- 柱网 6 米(大歌剧院共识 8 米)。
每个核心判据都必须在这个假想工程上同样成立。
"""
from __future__ import annotations

import pytest


# ── ① 坐标系分组不得假定工程坐标为负 ─────────────────────────────

@pytest.mark.unit
def test_world_range_is_derived_from_the_projects_own_anchors():
    """**实测特化点**:WORLD_RANGE=(-100000,-1000) 写死了「工程坐标为负」
    —— 那是大歌剧院的坐标系特征。正值城市坐标系(X≈40000)的工程,
    分组判据会把世界坐标全判成局部。

    通用原则:工程坐标区间从**项目自己的锚点**推导,不写死。
    """
    from services.axes_validation import world_range_from_anchors

    got = world_range_from_anchors([40012.5, 40180.2, 39950.0])
    lo, hi = got
    assert lo < 39950.0 < 40180.2 < hi, "区间要包住锚点并留余量"


@pytest.mark.unit
def test_negative_coordinate_projects_still_work():
    """大歌剧院(负值)同样从数据推出来 —— 新老工程一个口径。"""
    from services.axes_validation import world_range_from_anchors

    lo, hi = world_range_from_anchors([-6326.0, -6065.0])
    assert lo < -6326.0 and hi > -6065.0


@pytest.mark.unit
def test_no_anchors_means_single_system():
    """没有锚点 ⇒ 没有世界坐标 ⇒ 一切按局部处理(判不出就说判不出)。"""
    from services.axes_validation import world_range_from_anchors

    assert world_range_from_anchors([]) is None
    assert world_range_from_anchors(None) is None


@pytest.mark.unit
def test_classification_accepts_a_dynamic_range():
    from services.axes_validation import SYSTEM_LOCAL, SYSTEM_WORLD, coordinate_system_of

    positive = (39000.0, 41000.0)
    assert coordinate_system_of(40012.5, world_range=positive) == SYSTEM_WORLD
    assert coordinate_system_of(120.0, world_range=positive) == SYSTEM_LOCAL
    # 不传区间 ⇒ 无世界坐标概念,一律局部
    assert coordinate_system_of(-6200.0, world_range=None) == SYSTEM_LOCAL


# ── ② 子单体发现不得依赖「厅/馆」后缀词表 ────────────────────────

@pytest.mark.unit
def test_sub_units_are_discovered_from_level_name_prefixes():
    """住宅式命名「A栋3F」同样发现得出 —— 前缀在标准层 token 前、
    跨图一致出现即成子单体,零后缀词表。"""
    from services.sub_unit_discovery import discover_sub_units

    names = ["A栋3F", "A栋4F", "B栋3F", "B栋B1", "3F", "机房层"]
    got = discover_sub_units(names)
    assert got == {"A栋", "B栋"}


@pytest.mark.unit
def test_venue_style_names_are_discovered_the_same_way():
    """大歌剧院的「××厅」走同一条路 —— 不是为它单写的规则。"""
    from services.sub_unit_discovery import discover_sub_units

    # 小歌剧厅带两个不同楼层(真实数据里它有 1F/2F/4F/屋顶层 四个);
    # 只带一个楼层的前缀不成单体 —— 与住宅用例同一条孤证不立原则。
    names = ["大歌剧厅3F", "大歌剧厅4F", "小歌剧厅2F", "小歌剧厅4F"]
    got = discover_sub_units(names)
    assert "大歌剧厅" in got and "小歌剧厅" in got


@pytest.mark.unit
def test_single_occurrence_prefix_is_not_a_unit():
    """只出现一次的前缀不成单体 —— 孤证不立,防把笔误学成单体。"""
    from services.sub_unit_discovery import discover_sub_units

    assert discover_sub_units(["会议室3F", "4F", "5F"]) == set()


@pytest.mark.unit
def test_plain_floor_names_yield_nothing():
    from services.sub_unit_discovery import discover_sub_units

    assert discover_sub_units(["1F", "2F", "B1", "地下一层"]) == set()


# ── ③ 既有判据在第二工程参数下成立 ───────────────────────────────

@pytest.mark.unit
def test_axis_gap_anomaly_works_on_a_6m_grid_project():
    """柱网 6 米的工程:6.2 米正常、0.3 米照样判噪声 —— 判据是
    「工程合理区间+偏离共识」,不锚定 8 米。"""
    from services.axis_gap_anomaly import detect_gap_anomaly

    assert detect_gap_anomaly("d", 6.2, consensus_m=6.0, samples=10) is None
    got = detect_gap_anomaly("d", 0.3, consensus_m=6.0, samples=10)
    assert got and "噪声" in got["likely_cause"]


@pytest.mark.unit
def test_alias_learning_works_for_residential_naming():
    """图名共现学习对「A栋→1区」同样成立 —— 机制无词表。"""
    from services.level_elevation_consensus import learn_unit_aliases

    titled = [("1区A栋三层平面图", "zone1")] * 3 + [("2区B栋平面图", "zone2")] * 2
    got = learn_unit_aliases({"A栋", "B栋"}, titled)
    assert got == {"A栋": "zone1", "B栋": "zone2"}
