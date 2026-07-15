"""
分区维护 + LLM 调用日志写入健壮性测试。

覆盖两个本地实测运维 bug 的修复：
1. llm_call_logs 缺未来月份分区导致写入崩溃 → 滚动建分区 + DEFAULT 兜底（migration 028）。
2. 日志写入失败会打断 LLM 调用 → router._log 包 try/except，只告警不阻断。
"""
from datetime import date
from unittest.mock import AsyncMock, Mock

import pytest

from tasks import partition_maintenance as pm


# ── 纯函数：分区 DDL 计算 ─────────────────────────────────────────

def test_month_partition_ddl_bounds_and_name():
    name, ddl = pm.month_partition_ddl(2026, 7)
    assert name == "llm_call_logs_2026_07"
    assert "CREATE TABLE IF NOT EXISTS llm_call_logs_2026_07" in ddl
    assert "FROM ('2026-07-01') TO ('2026-08-01')" in ddl


def test_month_partition_ddl_december_rolls_to_next_year():
    name, ddl = pm.month_partition_ddl(2026, 12)
    assert name == "llm_call_logs_2026_12"
    assert "FROM ('2026-12-01') TO ('2027-01-01')" in ddl


def test_upcoming_partition_ddls_covers_current_and_ahead_months():
    ddls = pm.upcoming_partition_ddls(date(2026, 11, 20), months_ahead=2)
    names = [name for name, _ in ddls]
    # 当月 + 未来两月，且跨年正确
    assert names == [
        "llm_call_logs_2026_11",
        "llm_call_logs_2026_12",
        "llm_call_logs_2027_01",
    ]


# ── 任务：滚动建分区，单月失败不阻断其余月份 ───────────────────────

@pytest.mark.asyncio
async def test_do_ensure_creates_partitions(monkeypatch):
    fake_db = Mock()
    fake_db.execute = AsyncMock()
    fake_db.connect = AsyncMock()
    fake_db.disconnect = AsyncMock()
    monkeypatch.setattr(pm.databases, "Database", lambda *_a, **_k: fake_db)

    result = await pm._do_ensure(today=date(2026, 7, 10))

    assert result["failed"] == []
    assert result["ensured"] == [
        "llm_call_logs_2026_07",
        "llm_call_logs_2026_08",
        "llm_call_logs_2026_09",
    ]
    assert fake_db.execute.await_count == 3
    fake_db.disconnect.assert_awaited_once()


@pytest.mark.asyncio
async def test_do_ensure_tolerates_single_month_failure(monkeypatch):
    fake_db = Mock()
    # 第二个月 CREATE 抛错（模拟 default 分区已有冲突行），其余仍应成功
    fake_db.execute = AsyncMock(side_effect=[None, Exception("conflict"), None])
    fake_db.connect = AsyncMock()
    fake_db.disconnect = AsyncMock()
    monkeypatch.setattr(pm.databases, "Database", lambda *_a, **_k: fake_db)

    result = await pm._do_ensure(today=date(2026, 7, 1))

    assert result["ensured"] == ["llm_call_logs_2026_07", "llm_call_logs_2026_09"]
    assert result["failed"] == ["llm_call_logs_2026_08"]
    fake_db.disconnect.assert_awaited_once()  # 失败也正常释放连接


# ── router._log：写入失败只告警，不上抛 ───────────────────────────

def _make_config():
    from core.llm.router import EngineConfig

    return EngineConfig({
        "engine_name": "rag_qa",
        "task_type": "primary",
        "model_db_id": "00000000-0000-0000-0000-000000000001",
        "model_id": "claude-sonnet-5",
        "provider_type": "anthropic",
        "input_price_per_1m": 3.0,
        "output_price_per_1m": 15.0,
    })


@pytest.mark.asyncio
async def test_log_swallows_write_failure():
    from core.llm.router import ModelRouter
    from core.llm.providers import LLMResponse

    fake_db = Mock()
    fake_db.execute = AsyncMock(side_effect=Exception(
        'no partition of relation "llm_call_logs" found for row'
    ))
    router = ModelRouter(db=fake_db, redis=Mock())

    resp = LLMResponse(
        content="ok", prompt_tokens=10, completion_tokens=5,
        model_id="claude-sonnet-5", latency_ms=42,
    )

    # 缺分区导致 INSERT 抛错，但 _log 必须静默吞掉、不上抛
    await router._log("rag_qa", _make_config(), resp, success=True)
    fake_db.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_route_returns_response_even_if_logging_fails():
    """端到端：日志写入失败时，route() 仍正常返回 LLM 响应。"""
    import asyncio
    from core.llm.router import ModelRouter
    from core.llm.providers import LLMResponse

    fake_db = Mock()
    fake_db.execute = AsyncMock(side_effect=Exception("log db down"))
    router = ModelRouter(db=fake_db, redis=Mock())

    config = _make_config()
    resp = LLMResponse(
        content="hello", prompt_tokens=1, completion_tokens=1,
        model_id="claude-sonnet-5", latency_ms=1,
    )

    fake_provider = Mock()
    fake_provider.complete = AsyncMock(return_value=resp)
    fake_cb = Mock()
    fake_cb.is_open = AsyncMock(return_value=False)
    fake_cb.record_success = AsyncMock()

    router._get_config = AsyncMock(return_value=config)
    router._get_cb = Mock(return_value=fake_cb)
    router._get_provider = Mock(return_value=fake_provider)

    result = await router.route("rag_qa", [{"role": "user", "content": "hi"}])
    assert result is resp

    # 让 fire-and-forget 的 _log 任务跑完，确认其内部异常已被吞、不外泄
    await asyncio.sleep(0)
    await asyncio.sleep(0)
