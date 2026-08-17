"""荐锚要说「确认它能解锁多少张」，而不只是「它覆盖力强」（J1-3 深化）。

实测卡点：856 张双向轴网图里 **521 张卡在多分区未确认**，
而分区传播的投入产出比极高（人工 3 条/1 张图 → 传播 217 条/187 张）。

所以「该确认哪几张」这个决定值钱 —— 但现在的荐锚只按**覆盖力代理指标**
（最长序列 × 方向数）排序，人看不到「确认了到底能多出多少张」。
预估把代理指标换成**实际模拟的可解锁量**。

**必须 dry-run**：预估要试算多个候选，不能真写库 ——
否则光是「看看哪个划算」就把 `axis_intersections` 改了。
"""
from __future__ import annotations

import inspect

import pytest

from services.axis_intersection_propagate import run_intersection_propagation


@pytest.mark.unit
def test_propagation_supports_dry_run():
    """预估复用真实匹配逻辑，只是不落库 —— 另写一套模拟必然与实现漂移。"""
    assert "dry_run" in inspect.signature(run_intersection_propagation).parameters


@pytest.mark.unit
def test_dry_run_skips_writes():
    """**dry-run 不得写库**：试算若有副作用，人就不敢用它做决策。"""
    src = inspect.getsource(run_intersection_propagation)
    guard = src.find("if dry_run:")
    clear = src.find("_CLEAR_SQL")
    assert guard > 0, "缺少 dry_run 守卫"
    assert guard < clear, "守卫必须在清理/写入之前，否则 dry-run 照样改库"


@pytest.mark.unit
def test_estimate_reports_unlockable_count():
    from services.axis_intersection_propagate import format_coverage_estimate

    got = format_coverage_estimate(drawings=42, points=1580)
    assert "42" in got


@pytest.mark.unit
def test_estimate_of_nothing_is_honest():
    """预估为 0 要直说 —— 让人别在这张图上花时间。"""
    from services.axis_intersection_propagate import format_coverage_estimate

    got = format_coverage_estimate(drawings=0, points=0)
    assert got
    assert "0" in got


@pytest.mark.unit
def test_estimate_outranks_the_proxy_metric():
    """**实测解锁量优先于覆盖力代理指标** —— 后者会被符号场误检刷榜。

    实测：荐锚前 4 名的理由全带「轴线数远超常见轴网」（最长序列
    79/77/53/117 段），而**预估解锁全是 0 张**。
    代理指标（最长序列 × 方向数）越长排越前，而误检产生的正是超长假序列 ——
    排序等于被误检牵着走。

    所以有预估时按预估排；预估缺失的排在有预估者之后（判不出不能冒充 0，
    但也不该挤掉已知有效的）。
    """
    from services.anchor_candidates import rank_by_estimate

    got = rank_by_estimate([
        {"drawing_id": "noise", "score": 9999, "estimated_drawings": 0},
        {"drawing_id": "real", "score": 10, "estimated_drawings": 42},
    ])
    assert [r["drawing_id"] for r in got] == ["real", "noise"]


@pytest.mark.unit
def test_items_without_estimate_keep_their_relative_order():
    """预估算不出的保持原有排序，不因缺字段被打乱。"""
    from services.anchor_candidates import rank_by_estimate

    got = rank_by_estimate([
        {"drawing_id": "a", "score": 100},
        {"drawing_id": "b", "score": 50},
    ])
    assert [r["drawing_id"] for r in got] == ["a", "b"]


@pytest.mark.unit
def test_estimated_items_come_before_unestimated():
    """有实测解锁量的优先于「不知道能解锁多少」的。"""
    from services.anchor_candidates import rank_by_estimate

    got = rank_by_estimate([
        {"drawing_id": "unknown", "score": 9999},
        {"drawing_id": "known", "score": 1, "estimated_drawings": 5},
    ])
    assert got[0]["drawing_id"] == "known"


@pytest.mark.unit
def test_zero_estimate_ranks_below_unknown():
    """**已知解锁 0** 比「不知道」更该排后面 —— 前者已被证伪。"""
    from services.anchor_candidates import rank_by_estimate

    got = rank_by_estimate([
        {"drawing_id": "proven-zero", "score": 9999, "estimated_drawings": 0},
        {"drawing_id": "unknown", "score": 1},
    ])
    assert got[0]["drawing_id"] == "unknown"
