"""识别超时要**计数并报出来** —— 否则池被侵蚀无人知晓。

`asyncio.wait_for` 取消不了 executor 里正在跑的同步函数：超时后线程仍占着
`max_workers=2` 的池。本轮实测过它的极端后果 —— 两个僵尸线程把池占死，
recognize 阶段 **13 分钟零进展**，而 CPU 满载看起来像在干活。

治本要换 `ProcessPoolExecutor`（超时可 kill 进程），但成本实在：
`_recognize_sync` 要把几十 MB 的 PDF bytes 跨进程传，序列化开销可能超过收益，
且 16 处 `run_in_executor` 都要验证可 pickle。而当前症状已被「控制单图耗时」
缓解（圆检测 5.7 秒、超时 60 秒、实测最慢 40.6 秒）。

**所以：暂不改造，但必须可观测。** 超时数是池健康度的直接指标 ——
它由 0 变成常态，就是该动手改造的信号。
"""
from __future__ import annotations

import pytest

from services.model_elements import summarize_timeouts


@pytest.mark.unit
def test_timeouts_are_counted():
    got = summarize_timeouts([{"timeouts": 2}, {"timeouts": 1}, {"timeouts": 0}])
    assert got["count"] == 3


@pytest.mark.unit
def test_zero_timeouts_reports_healthy():
    """没有超时就明确说健康 —— 缺字段与「0 次」是两回事。"""
    got = summarize_timeouts([{"timeouts": 0}])
    assert got["count"] == 0
    assert got["healthy"] is True


@pytest.mark.unit
def test_many_timeouts_flag_the_pool():
    """超时多到接近池容量，就是**池正在被侵蚀**的信号。"""
    from services.model_elements import TIMEOUT_ALERT_THRESHOLD

    got = summarize_timeouts([{"timeouts": TIMEOUT_ALERT_THRESHOLD}])
    assert got["healthy"] is False
    assert got["note"], "要说清这意味着什么、下一步该做什么"


@pytest.mark.unit
def test_missing_field_is_not_counted_as_zero():
    """**判不出就说判不出**：没有该字段的层不参与统计，不当作 0。"""
    got = summarize_timeouts([{}, {"timeouts": 1}])
    assert got["count"] == 1
    assert got["floors_measured"] == 1


@pytest.mark.unit
def test_empty_input_is_safe():
    got = summarize_timeouts([])
    assert got["count"] == 0
    assert got["healthy"] is True
