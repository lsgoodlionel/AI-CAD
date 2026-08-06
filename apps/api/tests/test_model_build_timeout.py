"""建模任务的超时与重试策略。

**实测问题**:全项目建模(2309 张图 / 14 层)在全局
`task_soft_time_limit=1500`(25 分钟)下被杀:

```
01:04:38  开始
01:06:06  楼层定位完成(1.5 分钟)
01:29:36  Soft time limit (1500s) exceeded  ← 构件识别吃掉 23.5 分钟
          Task retry: Retry in 30s          ← 重试还会超时,纯浪费
```

两个问题:

1. **全局超时不适合长任务** —— 建模天然是分钟到小时级,
   而同一个 worker 上跑的规范同步、公示推进都是秒级。
2. **超时不该重试** —— 同样的输入、同样的耗时,重试必然再超时。
   `max_retries=2` 意味着白白再烧 50 分钟,最后仍然失败。
   超时要**立刻如实失败**,让人知道该加时还是该减量。
"""
from __future__ import annotations

import pytest
from celery.exceptions import SoftTimeLimitExceeded

from tasks.model_build import (
    BUILD_HARD_TIME_LIMIT_SEC, BUILD_SOFT_TIME_LIMIT_SEC, build_project_model,
)


@pytest.mark.unit
def test_build_has_its_own_generous_soft_limit():
    """建模要有**独立于全局**的超时 —— 实测单次需 25 分钟以上。"""
    from core.celery_app import celery_app

    assert BUILD_SOFT_TIME_LIMIT_SEC > celery_app.conf.task_soft_time_limit
    assert BUILD_SOFT_TIME_LIMIT_SEC >= 3600, "至少 1 小时,实测 25 分钟不够"


@pytest.mark.unit
def test_hard_limit_leaves_room_above_the_soft_limit():
    """硬超时要留出余量,让软超时的清理逻辑跑得完。"""
    assert BUILD_HARD_TIME_LIMIT_SEC > BUILD_SOFT_TIME_LIMIT_SEC


@pytest.mark.unit
def test_task_registers_its_own_limits():
    assert build_project_model.soft_time_limit == BUILD_SOFT_TIME_LIMIT_SEC
    assert build_project_model.time_limit == BUILD_HARD_TIME_LIMIT_SEC


@pytest.mark.unit
def test_timeout_is_not_retried(monkeypatch):
    """**超时不重试** —— 同样的输入重试必然再超时,只是白烧时间。

    直接跑 task 的函数体(`.run`),并把 `_do_build` 与落库都打桩掉,
    只观察「有没有调用 retry」。
    """
    from tasks import model_build

    marked: list = []
    monkeypatch.setattr(model_build, "_mark_failed",
                        lambda pid, msg: marked.append(msg))
    monkeypatch.setattr(model_build, "asyncio",
                        _FakeAsyncio(raises=SoftTimeLimitExceeded()))
    retried: list = []
    monkeypatch.setattr(build_project_model, "retry",
                        lambda **kw: retried.append(kw) or RuntimeError("x"))

    with pytest.raises(SoftTimeLimitExceeded):
        build_project_model.run("p1")
    assert retried == [], "超时不该走重试"
    assert marked and "超时" in marked[0], "要如实落库为超时失败"


@pytest.mark.unit
def test_other_failures_are_still_retried(monkeypatch):
    """网络/DB 抖动这类**瞬时**失败仍然值得重试。"""
    from tasks import model_build

    monkeypatch.setattr(model_build, "_mark_failed", lambda pid, msg: None)
    monkeypatch.setattr(model_build, "asyncio",
                        _FakeAsyncio(raises=RuntimeError("db down")))
    retried: list = []

    def _retry(**kw):
        retried.append(kw)
        return RuntimeError("retry raised")

    monkeypatch.setattr(build_project_model, "retry", _retry)
    with pytest.raises(RuntimeError):
        build_project_model.run("p1")
    assert retried, "瞬时失败应当重试"


class _FakeAsyncio:
    """`asyncio.run` 的最小替身:第一次抛指定异常,之后静默(给落库用)。"""

    def __init__(self, raises: BaseException) -> None:
        self._raises: BaseException | None = raises

    def run(self, coro):
        # 关掉协程对象,避免 "never awaited" 警告
        close = getattr(coro, "close", None)
        if callable(close):
            close()
        if self._raises is not None:
            exc, self._raises = self._raises, None
            raise exc
        return None
