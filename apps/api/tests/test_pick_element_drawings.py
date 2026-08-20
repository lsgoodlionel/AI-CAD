"""每层取哪几张图出构件 —— 这决定了模型位置对不对。

**这是用户报告「模型轴线和结构位置不对」的第二个根因。**

实测:每层 1248 张图进了楼层，却**只有 2 张出构件**（`_MAX_STRUCTURE_PLANS = 2`），
而且取的是**前 2 张、顺序任意**。后果:

| 层 | 来源图数 | 两图构件中心散布 |
|---|---:|---:|
| F2 | 2 | **103 米** |
| F3 | 2 | **83 米** |
| F5 | 2 | 38 米 |

三个问题:

1. **顺序任意** —— 按 DB 返回顺序取前 N 张;
2. **不看变换质量** —— 有标准比例变换的图可能排在后面被丢掉，
   取到的却是无变换、位置靠估的那张;
3. **不按单体分组** —— 南区与北区的图各有各的坐标系原点，
   取一张南区一张北区拼在一起，**必然差几十米**。

第 3 条正是 F2/F3 错位 83~103 米的直接原因。
"""
from __future__ import annotations

import pytest

from services.model_elements import pick_element_drawings


def _d(did: str, title: str, discipline: str = "structure") -> dict:
    return {"id": did, "drawing_no": did, "title": title,
            "discipline": discipline}


class _T:
    """`DrawingTransform` 的最小替身。"""

    def __init__(self, scale_m_pt: float) -> None:
        self.scale_m_pt = scale_m_pt
        self.origin_x = 0.0
        self.origin_y = 0.0
        self.page_h = 2384.0
        self.confidence = 1.0


_STANDARD = _T(150 * 25.4 / 72 / 1000)      # 1:150
_ODD = _T(137 * 25.4 / 72 / 1000)           # 非标准分母


@pytest.mark.unit
def test_drawings_with_a_standard_scale_transform_come_first():
    """**有标准比例变换的图优先** —— 它们的位置才靠得住。"""
    drawings = [_d("no-tf", "一层结构平面图A"),
                _d("odd", "一层结构平面图B"),
                _d("std", "一层结构平面图C")]
    picked = pick_element_drawings(
        drawings, transforms={"std": _STANDARD, "odd": _ODD})
    assert picked["structure"][0]["id"] == "std"


@pytest.mark.unit
def test_drawings_without_a_transform_come_last():
    """无变换的图位置只能靠估,排最后。"""
    drawings = [_d("no-tf", "一层结构平面图A"), _d("odd", "一层结构平面图B")]
    picked = pick_element_drawings(drawings, transforms={"odd": _ODD})
    assert [d["id"] for d in picked["structure"]] == ["odd", "no-tf"]


@pytest.mark.unit
def test_picks_stay_within_one_building_unit():
    """**核心用例**:南区与北区的图不能混着取。

    两区坐标系原点不同,拼在一起会差几十米 —— 实测 F2 差 103 米。
    """
    drawings = [
        _d("s1", "南区（大歌剧厅）一层结构平面图"),
        _d("s2", "南区（大歌剧厅）一层墙柱平面图"),
        _d("n1", "北区（小歌剧厅）一层结构平面图"),
        _d("n2", "北区（小歌剧厅）一层墙柱平面图"),
    ]
    picked = pick_element_drawings(drawings, transforms={
        "s1": _STANDARD, "s2": _STANDARD, "n1": _STANDARD, "n2": _STANDARD})
    units = {d["id"][0] for d in picked["structure"]}
    assert len(units) == 1, f"取到了多个单体的图:{picked['structure']}"


@pytest.mark.unit
def test_the_richest_unit_wins():
    """取图纸最多的那个单体 —— 它最可能是本层主体。"""
    drawings = [
        _d("n1", "北区一层结构平面图"),
        _d("s1", "南区一层结构平面图"),
        _d("s2", "南区一层墙柱平面图"),
        _d("s3", "南区一层模板平面图"),
    ]
    picked = pick_element_drawings(drawings, transforms={})
    assert all(d["id"].startswith("s") for d in picked["structure"])


@pytest.mark.unit
def test_backward_compatible_without_transforms():
    """不传 transforms 时行为不崩 —— 老调用方照常工作。"""
    drawings = [_d("a", "一层结构平面图"), _d("b", "一层墙柱平面图")]
    picked = pick_element_drawings(drawings)
    assert len(picked["structure"]) == 2


@pytest.mark.unit
def test_mep_and_beam_buckets_still_work():
    drawings = [_d("m", "一层给排水平面图", "mep"),
                _d("bm", "一层主梁配筋图")]
    picked = pick_element_drawings(drawings, transforms={})
    assert [d["id"] for d in picked["mep"]] == ["m"]
    assert [d["id"] for d in picked["beam"]] == ["bm"]


@pytest.mark.unit
def test_empty_input_is_safe():
    picked = pick_element_drawings([], transforms={})
    # 契约变更：新增 `architecture` 桶（建筑/装修平面图此前被整张丢弃，
    # 实测 81 张，而它们是墙与门窗的主要来源）。
    assert picked == {"structure": [], "beam": [], "mep": [],
                      "architecture": []}


# ── 有世界坐标的图必须优先（v44 实测断点）────────────────────

@pytest.mark.unit
def test_drawings_with_a_world_placement_come_first():
    """**实测断点**:交点传播算出 12 张图的世界坐标,`placed_drawings` 仍是 0。

    日志确认「12 张图按工程坐标绝对定位」,但 `placed` 只在**被选中出构件的图**
    上计数 —— 而每层只取 2 张结构图,选图逻辑不看有没有 placement,
    那 12 张一张也没被选中。**算出来的世界坐标进不了模型。**

    有 placement 的图位置是**绝对**可信的(锚点求解、残差毫米级),
    优先级应高于「有标准比例变换」——后者只保证图内比例对,不保证摆在哪。
    """
    drawings = [_d("std", "一层结构平面图A"), _d("placed", "一层结构平面图B")]
    picked = pick_element_drawings(
        drawings, transforms={"std": _STANDARD, "placed": _STANDARD},
        placements={"placed": {"rmse_m": 0.006}})
    assert picked["structure"][0]["id"] == "placed"


@pytest.mark.unit
def test_placement_beats_standard_scale_even_without_transform():
    """有世界坐标但无标准变换,仍优于只有标准变换的图。"""
    drawings = [_d("std", "一层结构平面图A"), _d("placed", "一层结构平面图B")]
    picked = pick_element_drawings(
        drawings, transforms={"std": _STANDARD},
        placements={"placed": {"rmse_m": 0.01}})
    assert picked["structure"][0]["id"] == "placed"


@pytest.mark.unit
def test_suspect_placement_does_not_get_priority():
    """残差过大的摆放**不算数** —— 宁可用相对配准,不用错的绝对坐标。

    `bad` **没有变换**(本该排最后)。若 suspect 的摆放仍被当作绝对定位,
    它会被提到第一 —— 那正是本用例要挡住的。
    """
    drawings = [_d("std", "一层结构平面图A"), _d("bad", "一层结构平面图B")]
    picked = pick_element_drawings(
        drawings, transforms={"std": _STANDARD},
        placements={"bad": {"rmse_m": 5.0, "suspect": True}})
    assert picked["structure"][0]["id"] == "std"


@pytest.mark.unit
def test_backward_compatible_without_placements():
    drawings = [_d("a", "一层结构平面图"), _d("b", "一层墙柱平面图")]
    picked = pick_element_drawings(drawings, transforms={})
    assert len(picked["structure"]) == 2


@pytest.mark.unit
def test_placed_drawings_do_not_consume_the_regular_quota():
    """**世界坐标是稀缺资源,不该被选图上限浪费掉**。

    实测 F2 层有 **8 张**图带世界坐标,却只摆放了 **3** 张 ——
    `_MAX_STRUCTURE_PLANS = 2` 把其余挡在外面。
    全项目 2309 张里只有 19 张有世界坐标,把它们全用上的成本可控
    (每图识别 10~40 秒),而它们的位置是**绝对**可信的。

    规则:有摆放的图**全部纳入**,常规配额只用来填充其余。
    """
    placed_ids = {f"p{i}": {"rmse_m": 0.01} for i in range(5)}
    drawings = ([_d(f"p{i}", f"一层结构平面图P{i}") for i in range(5)]
                + [_d(f"n{i}", f"一层结构平面图N{i}") for i in range(3)])
    picked = pick_element_drawings(drawings, transforms={},
                                   placements=placed_ids)
    ids = [d["id"] for d in picked["structure"]]
    assert set(ids) >= set(placed_ids), f"有世界坐标的图必须全进:{ids}"


@pytest.mark.unit
def test_regular_quota_still_caps_unplaced_drawings():
    """没有摆放的图仍受上限约束 —— 构建时长要可控。"""
    drawings = [_d(f"n{i}", f"一层结构平面图N{i}") for i in range(6)]
    picked = pick_element_drawings(drawings, transforms={}, placements={})
    assert len(picked["structure"]) == 2


@pytest.mark.unit
def test_suspect_placements_do_not_get_extra_quota():
    """存疑的摆放不享受额外配额 —— 它的绝对坐标本就不可信。"""
    drawings = [_d(f"b{i}", f"一层结构平面图B{i}") for i in range(5)]
    picked = pick_element_drawings(
        drawings, transforms={},
        placements={f"b{i}": {"suspect": True} for i in range(5)})
    assert len(picked["structure"]) == 2


# ── 有世界坐标却进不了任何桶（J1 任务 2,v49 实测）──────────────

@pytest.mark.unit
def test_located_drawing_outside_the_three_buckets_is_still_used():
    """**实测**:19 张有世界坐标的图里,3 张进不了任何桶。

    | 图 | 专业 | 为什么落空 |
    |---|---|---|
    | 屋顶花园排水组织图 | architecture | 标题无结构/梁词,专业非 mep/structure |
    | 二层隔声隔振平面图 | architecture | 同上 |
    | 四层夹层平面图 | architecture | 同上 |

    分桶逻辑是 `mep → beam → structure`，三者都不匹配就**整张丢弃**。
    可这些图的位置是**绝对可信**的（锚点求解、残差毫米级），
    白白浪费掉太可惜 —— 建筑平面图上本就有墙、柱、门窗。

    **只对有世界坐标的图开这个口子**：位置不可信的图进来只会添噪声。
    """
    drawings = [_d("plain", "屋顶花园排水组织图", "architecture")]
    picked = pick_element_drawings(
        drawings, transforms={}, placements={"plain": {"rmse_m": 0.01}})
    assert [d["id"] for d in picked["structure"]] == ["plain"]


@pytest.mark.unit
def test_unlocated_drawing_outside_the_buckets_is_still_dropped():
    """没有世界坐标的图照旧丢弃 —— 不能借这个口子把噪声放进来。"""
    drawings = [_d("plain", "屋顶花园排水组织图", "architecture")]
    picked = pick_element_drawings(drawings, transforms={}, placements={})
    assert picked["structure"] == []


@pytest.mark.unit
def test_suspect_placement_does_not_open_the_door():
    """存疑的摆放不享受此待遇 —— 它的位置本就不可信。"""
    drawings = [_d("plain", "屋顶花园排水组织图", "architecture")]
    picked = pick_element_drawings(
        drawings, transforms={}, placements={"plain": {"suspect": True}})
    assert picked["structure"] == []


@pytest.mark.unit
def test_mep_located_drawings_still_go_to_mep():
    """已有归属的不改桶 —— 只兜底真正无处可去的。"""
    drawings = [_d("m", "二层火灾自动报警平面图", "mep")]
    picked = pick_element_drawings(
        drawings, transforms={}, placements={"m": {"rmse_m": 0.01}})
    assert [d["id"] for d in picked["mep"]] == ["m"]
    assert picked["structure"] == []
