"""选锚图与荐锚图 —— 两件事判据同源(J1 收尾 + J1-3)。

**选锚**:交点传播要从哪张图出发。**不得硬编码图号**
(`docs/MODELING_PIPELINE_BLUEPRINT.md` §7 约束 1),
判据只能来自内容:有没有真世界锚点、变换残差多大、覆盖多广。

**荐锚**:告诉人「该确认哪几张图最划算」。实测未匹配原因中
「对不上任何锚」占 **91%**、歧义仅 1% ⇒ 瓶颈是锚覆盖不足,
而人工确认一次的成本是固定的 —— 所以**该优先确认覆盖最广的图**。

两者共用同一个「覆盖力」度量:一张图能作锚的价值 ≈
它的轴距序列有多长、多少个方向、多少个分区。序列越长,
越多局部图能作为它的子序列匹配上。
"""
from __future__ import annotations

import pytest

from services.anchor_candidates import (
    MIN_ANCHOR_POINTS, pick_anchor_drawing, rank_anchor_candidates,
)


def _cand(did: str, *, points: int = 0, rmse: float | None = None,
          gaps: int = 0, directions: int = 0, zones: int = 0,
          confirmed: bool = False) -> dict:
    return {"drawing_id": did, "anchor_points": points, "rmse_m": rmse,
            "total_gaps": gaps, "directions": directions, "zones": zones,
            "zone_confirmed": confirmed}


# ── 选锚:谁能当传播的出发点 ──────────────────────────────────

@pytest.mark.unit
def test_picks_the_drawing_with_the_smallest_residual():
    """**残差最小者优先** —— 锚图的变换会传给所有下游,错了全错。"""
    got = pick_anchor_drawing([
        _cand("a", points=8, rmse=0.5),
        _cand("b", points=6, rmse=0.006),
    ])
    assert got == "b"


@pytest.mark.unit
def test_drawings_without_enough_points_cannot_be_anchors():
    """解相似变换至少要 2 个点。"""
    got = pick_anchor_drawing([_cand("a", points=MIN_ANCHOR_POINTS - 1, rmse=0.001)])
    assert got is None


@pytest.mark.unit
def test_missing_residual_is_not_an_anchor():
    """算不出残差 = 变换没解出来,不能当锚 —— **判不出就说判不出**。"""
    assert pick_anchor_drawing([_cand("a", points=9, rmse=None)]) is None


@pytest.mark.unit
def test_empty_input_is_safe():
    assert pick_anchor_drawing([]) is None
    assert pick_anchor_drawing(None) is None


@pytest.mark.unit
def test_ties_broken_deterministically():
    """残差相同时按 drawing_id 定序 —— 顺序依赖会让重建结果漂移。"""
    a = pick_anchor_drawing([_cand("b", points=5, rmse=0.01),
                             _cand("a", points=5, rmse=0.01)])
    b = pick_anchor_drawing([_cand("a", points=5, rmse=0.01),
                             _cand("b", points=5, rmse=0.01)])
    assert a == b == "a"


# ── 荐锚:该让人确认哪几张 ────────────────────────────────────

@pytest.mark.unit
def test_ranks_wider_coverage_first():
    """**覆盖越广越值得确认** —— 序列越长,越多局部图能匹配上它。"""
    got = rank_anchor_candidates([
        _cand("small", gaps=8, directions=1, zones=1),
        _cand("wide", gaps=60, directions=2, zones=3),
    ])
    assert [r["drawing_id"] for r in got][0] == "wide"


@pytest.mark.unit
def test_already_confirmed_drawings_are_excluded():
    """已确认的不必再推荐 —— 推荐列表是**待办**,不是排行榜。"""
    got = rank_anchor_candidates([
        _cand("done", gaps=60, directions=2, zones=3, confirmed=True),
        _cand("todo", gaps=20, directions=2, zones=1),
    ])
    assert [r["drawing_id"] for r in got] == ["todo"]


@pytest.mark.unit
def test_single_direction_drawings_rank_below_bidirectional():
    """**双向才能构成交点** —— 单向图确认了也拿不到世界坐标。

    实测 143 张匹配成功的图里 131 张是单向的,它们一个交点也产不出。
    """
    got = rank_anchor_candidates([
        _cand("one-way", gaps=80, directions=1, zones=2),
        _cand("two-way", gaps=40, directions=2, zones=2),
    ])
    assert [r["drawing_id"] for r in got][0] == "two-way"


@pytest.mark.unit
def test_drawings_without_usable_sequences_are_dropped():
    """轴线太少的图当不了锚,不该出现在推荐里。"""
    got = rank_anchor_candidates([_cand("thin", gaps=0, directions=0, zones=0)])
    assert got == []


@pytest.mark.unit
def test_result_carries_the_reason():
    """推荐要说明**为什么** —— 让人能判断值不值得花这一次确认。"""
    got = rank_anchor_candidates([_cand("wide", gaps=60, directions=2, zones=3)])
    assert got[0]["reason"]
    assert "60" in got[0]["reason"] or "3" in got[0]["reason"]


@pytest.mark.unit
def test_limit_is_respected():
    got = rank_anchor_candidates(
        [_cand(f"d{i}", gaps=10 + i, directions=2, zones=1) for i in range(10)],
        limit=3)
    assert len(got) == 3


@pytest.mark.unit
def test_ranking_is_deterministic():
    cands = [_cand(f"d{i}", gaps=20, directions=2, zones=1) for i in range(5)]
    assert ([r["drawing_id"] for r in rank_anchor_candidates(cands)]
            == [r["drawing_id"] for r in rank_anchor_candidates(cands[::-1])])


@pytest.mark.unit
def test_empty_ranking_is_safe():
    assert rank_anchor_candidates([]) == []
    assert rank_anchor_candidates(None) == []


# ── 覆盖力的度量必须防住符号场误检（实测暴露）──────────────────

@pytest.mark.unit
def test_many_zones_do_not_outrank_a_long_single_zone():
    """**实测缺陷**:把分区数当加分项,推荐榜首成了喷淋抗震支架图。

    实测推荐前 5 名全是机电图,报「11 个分区」「15 个分区」——
    而大歌剧院真值只有 **3 个分区**。分区多是**符号场误检**的特征
    (设备符号被当成轴号圈),不是覆盖广。

    而且分区多意味着轴网被切碎:11 分区 × 91 段 = 每组平均 4 段,
    匹配不了什么。**匹配是按组做的,该看最长的那组,不是总和**。
    """
    got = rank_anchor_candidates([
        _cand("mep-noise", gaps=91, directions=2, zones=11) | {"max_gaps": 8},
        _cand("real-grid", gaps=60, directions=2, zones=3) | {"max_gaps": 23},
    ])
    assert [r["drawing_id"] for r in got][0] == "real-grid"


@pytest.mark.unit
def test_absurd_zone_count_is_penalised():
    """分区数远超工程常识 ⇒ 判为误检,排到后面。"""
    got = rank_anchor_candidates([
        _cand("absurd", gaps=200, directions=2, zones=15) | {"max_gaps": 20},
        _cand("plain", gaps=40, directions=2, zones=2) | {"max_gaps": 20},
    ])
    assert [r["drawing_id"] for r in got][0] == "plain"


@pytest.mark.unit
def test_longest_run_drives_the_score():
    """同样的方向数与分区数下,最长组更长者优先。"""
    got = rank_anchor_candidates([
        _cand("short", gaps=50, directions=2, zones=2) | {"max_gaps": 6},
        _cand("long", gaps=50, directions=2, zones=2) | {"max_gaps": 25},
    ])
    assert [r["drawing_id"] for r in got][0] == "long"


@pytest.mark.unit
def test_overdetected_runs_are_capped_not_rewarded():
    """**「越大越好」的量会被过检刷榜** —— 必须封顶。

    实测基坑支撑图报「最长一组 194 段」占榜首,而真轴网定位图只有 23 段。
    查下来那 434 条轴线全部 `source=label_circle`:图上的圆形构件
    (立柱桩、钢立柱)被当成了轴号圈。

    我先试过「带轴号占比」判据,**无效** —— 轴号是 §8.0.3 推导出来的,
    系统给每条检出轴线都编号,占比恒为 1。它衡量的是系统自己的产出,
    不是图纸事实。
    """
    from services.anchor_candidates import MAX_SCORED_GAPS, coverage_score

    huge = coverage_score({"directions": 2, "max_gaps": 194})
    enough = coverage_score({"directions": 2, "max_gaps": MAX_SCORED_GAPS})
    assert huge == enough, "超过上限不再加分"


@pytest.mark.unit
def test_overdetection_is_flagged_in_the_reason():
    """封顶不等于藏起来 —— 要在理由里说明可能是误检,让人核对。"""
    from services.anchor_candidates import MAX_SCORED_GAPS

    got = rank_anchor_candidates([
        _cand("huge", gaps=400, directions=2, zones=2)
        | {"max_gaps": MAX_SCORED_GAPS * 3}])
    assert "圆形构件" in got[0]["reason"]
