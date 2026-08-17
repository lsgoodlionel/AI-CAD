"""层内坐标系矛盾要**报出来**，不能自动二选一（用户第 3 项口径）。

> 「相对配准与绝对摆放，没有矛盾的时候直接存在，矛盾时出矛盾点，
>   提交人工判断，并为判断提供原始依据及数据文字信息支撑」

实测 v53：B1 / F2 / RF 三层构件横跨两个坐标系，同层跨度虚报 6300+ 米。
"""
from __future__ import annotations

import pytest

from services.coordinate_conflict import (
    MIN_CONFLICT_DISTANCE_M, detect_floor_conflict, summarize_conflicts,
)


@pytest.mark.unit
def test_mixed_floor_is_reported():
    """**核心用例**：同层既有绝对摆放又有相对配准 ⇒ 矛盾。"""
    got = detect_floor_conflict("B1", ["a", "b", "c"], ["d", "e", "f"],
                                (-6200.0, -6200.0), (100.0, 100.0))
    assert got is not None
    assert got["floor"] == "B1"
    assert got["placed_count"] == 3
    assert got["unplaced_count"] == 3


@pytest.mark.unit
def test_all_placed_is_not_a_conflict():
    """全部绝对摆放 ⇒ 坐标系自洽，不是矛盾。"""
    assert detect_floor_conflict("F1", ["a", "b"], []) is None


@pytest.mark.unit
def test_all_unplaced_is_not_a_conflict():
    """全部相对配准 ⇒ 同样自洽（只是不带工程坐标）。"""
    assert detect_floor_conflict("F1", [], ["a", "b"]) is None


@pytest.mark.unit
def test_agreeing_groups_are_not_a_conflict():
    """**「没有矛盾的时候直接存在」** —— 两组落在一起就不必惊动人。

    这是用户口径的关键一半：不能因为「有两类图」就一律报矛盾。
    """
    got = detect_floor_conflict("F3", ["a"], ["b"], (10.0, 10.0), (12.0, 11.0))
    assert got is None


@pytest.mark.unit
def test_distance_threshold_separates_the_two_systems():
    """判据要能可靠区分「同系内离散」与「跨坐标系」。"""
    near = detect_floor_conflict("F3", ["a"], ["b"], (0.0, 0.0),
                                 (MIN_CONFLICT_DISTANCE_M - 1, 0.0))
    far = detect_floor_conflict("F3", ["a"], ["b"], (0.0, 0.0),
                                (MIN_CONFLICT_DISTANCE_M + 1, 0.0))
    assert near is None
    assert far is not None


@pytest.mark.unit
def test_conflict_carries_the_evidence():
    """**要给判断依据** —— 光说「有矛盾」，人无从下手。"""
    got = detect_floor_conflict("B1", ["a"], ["b", "c"],
                                (-6200.0, 0.0), (100.0, 0.0))
    assert got["placed_drawings"] == ["a"], "要能点开具体是哪几张"
    assert got["unplaced_drawings"] == ["b", "c"]
    assert got["distance_m"] and got["distance_m"] > 6000
    assert "米" in got["explanation"]


@pytest.mark.unit
def test_explanation_offers_concrete_actions():
    """要说清**人能做什么**，而且两条路都要给（补锚点 / 就用局部）。"""
    got = detect_floor_conflict("B1", ["a"], ["b"], (-6200.0, 0.0), (0.0, 0.0))
    assert "补世界锚点" in got["explanation"]
    assert "保持局部配准" in got["explanation"]


@pytest.mark.unit
def test_resolution_states_what_the_system_did():
    """**降级必须可见**：系统自己做了什么要写明，不能默默退回。"""
    got = detect_floor_conflict("B1", ["a"], ["b"], (-6200.0, 0.0), (0.0, 0.0))
    assert got["resolution"]


@pytest.mark.unit
def test_unplaced_list_is_capped():
    """未摆放的可能上百张，列表要截断，否则 scene 会被撑爆。"""
    got = detect_floor_conflict("B1", ["a"], [f"d{i}" for i in range(100)],
                                (-6200.0, 0.0), (0.0, 0.0))
    assert len(got["unplaced_drawings"]) <= 20
    assert got["unplaced_count"] == 100, "计数要照实，截断的只是明细"


@pytest.mark.unit
def test_summary_is_serialisable():
    conflicts = [detect_floor_conflict("B1", ["a"], ["b"], (-6200.0, 0.0), (0.0, 0.0)),
                 None]
    got = summarize_conflicts(conflicts)
    assert got["count"] == 1
    assert got["floors"] == ["B1"]


@pytest.mark.unit
def test_summary_of_nothing_is_safe():
    assert summarize_conflicts([])["count"] == 0
    assert summarize_conflicts([None, None])["count"] == 0


# ── 判定要真的用上距离（我接线时把这一半丢了）─────────────────────

@pytest.mark.unit
def test_placement_offset_estimates_where_the_group_lands():
    """判定发生在摆放**之前**，拿不到构件中心 —— 用 placement 把原点映射到哪来估。

    **实测缺陷**：v57 的 5 条矛盾点里 `distance_m` 全是 null，
    因为接线时没传 centre ⇒「两组本就在一起就不算矛盾」这一半
    **完全没生效**，退化成「只要有两类图就报」。
    这正是我在本文件里写明的关键一半，自己接线时却漏了。
    """
    from services.coordinate_conflict import placement_offset

    # 把本图原点搬到工程坐标 (−6200, −6300) 的摆放
    placement = {"scale": 1.0, "rotation_deg": 0.0,
                 "tx": -6200.0, "ty": -6300.0}
    got = placement_offset(placement)
    assert got is not None
    assert got[0] == pytest.approx(-6200.0, abs=1.0)


@pytest.mark.unit
def test_no_placement_yields_no_offset():
    from services.coordinate_conflict import placement_offset

    assert placement_offset(None) is None
    assert placement_offset({}) is None


@pytest.mark.unit
def test_offset_survives_an_unexpected_placement_shape():
    """摆放结构由上游决定，**算不出就说算不出**，不能炸掉整个构建。"""
    from services.coordinate_conflict import placement_offset

    assert placement_offset({"unexpected": 1}) is None
