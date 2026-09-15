"""总图与分图同时进一个楼层时，只留分图 —— 同一批构件不画几遍。

**实测**（大歌剧院 v85「1层」）：结构桶里同时有
`一层结构平面总图`（.01C）与它的分图 `（二）（三）（四）`，四张画的是同一个
南区，柱在模型里被算了多遍、摊成四片（全层 219×177 米）。

**为什么留分图、丢总图**：

| | 比例 | 实测依据 |
|---|---|---|
| 分图 | 图框 1:150，**对** | 柱中位边长 11.2pt × 1:150 = 0.59m |
| 总图 | 图框也写 1:150，**错** | 柱中位边长 6.9pt，只有分图的 0.6 倍 ⇒ 真值约 1:250 |

总图是概览，分图才是按图框比例画的正式图。丢掉总图还空出一个选图名额，
让本来挤不进来的分图（一）进来。

**没做的**：把分图配准到总图上。实测按柱图案做比例+平移投票，
各比例命中率 6~13%、没有峰 —— 柱候选的精确率只有 12%，噪声对不上图案。

判据只用 GB/T 50001 通用的图名写法（「总图」、「（一）」序号），不认图号。
"""
from __future__ import annotations

import pytest

from services.sheet_series import drop_superseded_overviews, is_overview, series_key

T_ALL = "结构-竣工图--南区（大、中歌剧厅）一层结构平面总图"
T_P3 = "结构-竣工图--南区（大、中歌剧厅）一层结构平面图（三）"
T_P4 = "结构-竣工图--南区（大、中歌剧厅）一层结构平面图（四）"
T_COL_ALL = "结构-竣工图--南区（大、中歌剧厅）一层以下墙柱平面总图"


def _d(i, title, discipline="structure"):
    return {"id": i, "title": title, "discipline": discipline}


@pytest.mark.unit
def test_overview_and_its_parts_share_a_series_key():
    assert series_key(T_ALL) == series_key(T_P3) == series_key(T_P4)


@pytest.mark.unit
def test_ascii_parenthesised_part_number_is_a_part():
    assert series_key("59 地下首层结构换撑平面布置图(1)") == series_key("59 地下首层结构换撑平面布置图(2)")


@pytest.mark.unit
def test_site_plan_is_not_an_overview():
    """「总平面图」是总图专业的场地图，不是某张平面的总图。"""
    assert not is_overview("建筑-竣工图--总平面图")
    assert is_overview(T_ALL)


@pytest.mark.unit
def test_general_plan_discipline_prefix_is_not_an_overview():
    """「总图」也是 GB/T 50001 的专业名 —— 专业前缀不能让一张普通平面变成总图，
    更不能让它被同系列的分图替掉。"""
    road = _d("road", "总图-竣工图--道路平面图")
    road_part = _d("road1", "总图-竣工图--道路平面图（一）")
    assert not is_overview(road["title"])
    kept, dropped = drop_superseded_overviews([road, road_part])
    assert dropped == []
    assert len(kept) == 2


@pytest.mark.unit
def test_overview_is_dropped_when_its_parts_are_present():
    drawings = [_d("all", T_ALL), _d("p3", T_P3), _d("p4", T_P4)]
    kept, dropped = drop_superseded_overviews(drawings)
    assert [d["id"] for d in kept] == ["p3", "p4"]
    assert [d["id"] for d in dropped] == ["all"]


@pytest.mark.unit
def test_overview_without_parts_is_kept():
    """墙柱平面总图在这一层没有分图 —— 它就是唯一来源，不能丢。"""
    drawings = [_d("colall", T_COL_ALL), _d("p3", T_P3)]
    kept, dropped = drop_superseded_overviews(drawings)
    assert {d["id"] for d in kept} == {"colall", "p3"}
    assert dropped == []


@pytest.mark.unit
def test_year_in_brackets_is_not_a_part_number():
    assert series_key("结构设计总说明(2015)") != series_key("结构设计总说明")
    assert not is_overview("某某平面图(2015)")


@pytest.mark.unit
def test_overview_is_kept_when_parts_exceed_the_quota():
    """梁配额 4 张、分图有 6 张：丢了总图，装不下的两张分图那片就没有梁了。"""
    parts = [_d(f"p{i}", f"一层梁配筋图（{'一二三四五六'[i]}）") for i in range(6)]
    overview = _d("all", "一层梁配筋总图")
    kept, dropped = drop_superseded_overviews([overview, *parts], max_parts=4)
    assert dropped == []
    assert overview in kept


@pytest.mark.unit
def test_protected_overview_is_kept():
    """有工程坐标定位的总图位置绝对可信，不丢。"""
    kept, dropped = drop_superseded_overviews(
        [_d("all", T_ALL), _d("p3", T_P3)], is_protected=lambda d: d["id"] == "all")
    assert dropped == []
    assert len(kept) == 2


@pytest.mark.unit
def test_input_is_not_mutated():
    drawings = [_d("all", T_ALL), _d("p3", T_P3)]
    snapshot = [dict(d) for d in drawings]
    drop_superseded_overviews(drawings)
    assert drawings == snapshot


@pytest.mark.unit
def test_pick_element_drawings_drops_overview_and_frees_quota():
    """总图 + 六张分图、结构配额 6：丢总图后六张分图全进，并报出被替代的总图。"""
    from services.model_elements import _MAX_STRUCTURE_PLANS, pick_element_drawings

    nums = "一二三四五六七八"
    parts = [_d(f"p{i}", f"结构-竣工图--南区一层结构平面图（{nums[i]}）")
             for i in range(_MAX_STRUCTURE_PLANS)]
    overview = _d("all", "结构-竣工图--南区一层结构平面总图")
    picked = pick_element_drawings([overview, *parts])
    ids = {d["id"] for d in picked["structure"]}
    assert "all" not in ids
    assert ids == {d["id"] for d in parts}
    assert [d["id"] for d in picked["superseded"]] == ["all"]
