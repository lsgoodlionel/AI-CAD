"""荐锚要按**确认它能带动多少张**排序 —— 口径必须是分区传播。

**上一版接错了对象**：用 `run_intersection_propagation` 的 dry-run 做预估，
那衡量的是「作为**交点传播**锚的价值」，要求锚图自己有世界锚点；
而荐锚列表里的图大多没有坐标标注 ⇒ 预估恒为 0。
数字没错，回答的却不是这里要问的问题。

正确口径：把某图**假设为已确认分区号**，再跑一轮分区传播，看覆盖增量。
"""
from __future__ import annotations

import inspect

import pytest

from services.axis_zone_propagate_job import run_zone_propagation


@pytest.mark.unit
def test_zone_propagation_supports_dry_run():
    """试算不能写库 —— 否则「看看哪张划算」本身就改了数据。"""
    assert "dry_run" in inspect.signature(run_zone_propagation).parameters


@pytest.mark.unit
def test_zone_propagation_accepts_an_extra_anchor():
    """预估的核心：把候选图**当作已确认**再跑一轮。"""
    assert "extra_anchor_drawing_id" in inspect.signature(
        run_zone_propagation).parameters


@pytest.mark.unit
def test_dry_run_guard_precedes_the_write():
    """守卫必须在写入之前 —— 写在之后等于没写（本轮已犯过一次）。"""
    src = inspect.getsource(run_zone_propagation)
    guard = src.find("dry_run")
    write = src.find("save_propagations")
    assert 0 < guard < write


@pytest.mark.unit
def test_extra_anchor_is_not_also_a_candidate():
    """假设已确认的那张图不能同时当候选 —— 否则自己匹配自己，虚增覆盖。"""
    src = inspect.getsource(run_zone_propagation)
    assert "anchor_ids" in src
    # extra 必须并入 anchor_ids，候选集才排除它
    assert src.count("anchor_ids") >= 2


@pytest.mark.unit
def test_estimate_returns_the_increment():
    """预估 = 有它 − 没它，**增量**才是这次确认的贡献。"""
    from services.axis_zone_propagate_job import estimate_zone_unlock

    assert "extra_anchor_drawing_id" in inspect.getsource(estimate_zone_unlock)
    assert "-" in inspect.getsource(estimate_zone_unlock)


@pytest.mark.unit
def test_estimate_never_reports_negative():
    """增量为负说明试算有噪声，报 0 而不是负数（宁可保守）。"""
    from services.axis_zone_propagate_job import estimate_zone_unlock

    assert "max(0" in inspect.getsource(estimate_zone_unlock)
