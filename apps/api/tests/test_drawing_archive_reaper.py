"""档案僵死状态回收单测。

**为什么需要**:实测 10 张真实图纸卡在 `extracting` 达 **12~13 天**——抽取任务
置位 `extracting` 后进程被杀(镜像重建/OCR 回填时段),而状态机没有任何回收机制,
这批图**永远不会被重抽**,也不会出现在任何失败列表里(静默丢失)。
"""
from datetime import datetime, timedelta, timezone

import pytest

from services.drawing_archive_reaper import (
    ACTIVE_STATUS, MAX_REAP_ATTEMPTS, RESET_STATUS, STALE_EXTRACTING_HOURS,
    is_stale, plan_reap, reap_attempts_of,
)

NOW = datetime(2026, 7, 30, 12, 0, 0, tzinfo=timezone.utc)


def _row(drawing_id: str, status: str = ACTIVE_STATUS, hours_ago: float = 0.0,
         summary: dict | None = None) -> dict:
    return {"drawing_id": drawing_id, "project_id": "p1", "status": status,
            "started_at": NOW - timedelta(hours=hours_ago), "summary": summary}


# ── 僵死判定 ──────────────────────────────────────────────────

def test_fresh_extracting_is_not_stale():
    """正在跑的不能碰 —— 误杀会把好任务的结果丢掉。"""
    assert not is_stale(ACTIVE_STATUS, NOW - timedelta(minutes=5), now=NOW)


def test_extracting_beyond_threshold_is_stale():
    assert is_stale(ACTIVE_STATUS, NOW - timedelta(hours=13 * 24), now=NOW)


def test_threshold_is_generous_relative_to_real_runtime():
    """单图抽取实测秒级到分钟级;阈值要远大于它、又远小于 12 天。"""
    assert 1 <= STALE_EXTRACTING_HOURS <= 6


def test_exactly_at_threshold_is_not_yet_stale():
    """边界取「严格超过」,避免把刚好卡在阈值上的任务判死。"""
    at = NOW - timedelta(hours=STALE_EXTRACTING_HOURS)
    assert not is_stale(ACTIVE_STATUS, at, now=NOW)


def test_ready_status_is_never_stale():
    """只回收 extracting;ready/reviewed 是终态,碰它们等于破坏数据。"""
    assert not is_stale("ready", NOW - timedelta(days=99), now=NOW)
    assert not is_stale("reviewed", NOW - timedelta(days=99), now=NOW)
    assert not is_stale("pending", NOW - timedelta(days=99), now=NOW)


def test_missing_started_at_is_not_stale():
    """时间戳缺失时不敢判死 —— 宁可漏回收,不可误杀。"""
    assert not is_stale(ACTIVE_STATUS, None, now=NOW)


def test_naive_timestamp_is_treated_as_utc():
    """驱动可能返回不带时区的时间;不能因此抛异常。"""
    naive = (NOW - timedelta(days=13)).replace(tzinfo=None)
    assert is_stale(ACTIVE_STATUS, naive, now=NOW)


# ── 回收次数(防止永久坏图无限重派)──────────────────────────────

def test_reap_attempts_defaults_to_zero():
    assert reap_attempts_of(None) == 0
    assert reap_attempts_of({}) == 0


def test_reap_attempts_read_from_summary():
    assert reap_attempts_of({"reap_attempts": 2}) == 2


def test_reap_attempts_ignores_garbage():
    """summary 是自由 jsonb,可能被别的写入方塞进任意内容。"""
    assert reap_attempts_of({"reap_attempts": "many"}) == 0
    assert reap_attempts_of("not a dict") == 0


# ── 回收计划 ──────────────────────────────────────────────────

def test_plan_resets_stale_rows_to_pending():
    plan = plan_reap([_row("a", hours_ago=13 * 24)], now=NOW)
    assert len(plan) == 1
    assert plan[0]["drawing_id"] == "a"
    assert plan[0]["next_status"] == RESET_STATUS == "pending"


def test_plan_skips_fresh_rows():
    assert plan_reap([_row("a", hours_ago=0.1)], now=NOW) == []


def test_plan_requeues_on_first_attempts():
    plan = plan_reap([_row("a", hours_ago=99)], now=NOW)
    assert plan[0]["requeue"] is True
    assert plan[0]["reap_attempts"] == 1


def test_plan_stops_requeueing_after_cap():
    """永久坏的图必须停止重派,否则每轮 beat 都在刷错误日志。"""
    assert MAX_REAP_ATTEMPTS == 2
    row = _row("a", hours_ago=99, summary={"reap_attempts": MAX_REAP_ATTEMPTS})
    plan = plan_reap([row], now=NOW)
    assert plan[0]["requeue"] is False
    assert plan[0]["reap_attempts"] == MAX_REAP_ATTEMPTS + 1


def test_plan_still_resets_status_even_when_not_requeueing():
    """到达上限也要脱离 extracting —— 否则它继续在看板上假装「正在抽取」。"""
    row = _row("a", hours_ago=99, summary={"reap_attempts": 9})
    assert plan_reap([row], now=NOW)[0]["next_status"] == RESET_STATUS


def test_plan_preserves_project_id_for_requeue():
    plan = plan_reap([_row("a", hours_ago=99)], now=NOW)
    assert plan[0]["project_id"] == "p1"


def test_plan_on_empty_input():
    assert plan_reap([], now=NOW) == []


def test_plan_handles_mixed_batch():
    rows = [_row("stale", hours_ago=99), _row("fresh", hours_ago=0.2),
            _row("done", status="ready", hours_ago=99)]
    ids = [p["drawing_id"] for p in plan_reap(rows, now=NOW)]
    assert ids == ["stale"]


def test_plan_does_not_mutate_input_rows():
    """不可变:计划只描述要做什么,不改原始行。"""
    row = _row("a", hours_ago=99, summary={"reap_attempts": 1})
    before = dict(row["summary"])
    plan_reap([row], now=NOW)
    assert row["summary"] == before
    assert row["status"] == ACTIVE_STATUS


def test_now_defaults_to_current_time():
    """不传 now 时用当前时间,便于任务直接调用。"""
    long_ago = datetime(2020, 1, 1, tzinfo=timezone.utc)
    assert is_stale(ACTIVE_STATUS, long_ago)
    assert not is_stale(ACTIVE_STATUS, datetime.now(timezone.utc))
