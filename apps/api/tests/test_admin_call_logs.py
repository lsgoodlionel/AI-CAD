"""
routers/admin/call_logs.py 回归测试 — 健康看板 500 修复(Phase E0-1)

两个已复现的线上 500:
1. GET /admin/llm/logs/daily  — SQL `($1 || ' days')::interval` 给 int 参数,
   asyncpg 报 TypeError: expected str, got int。修法:make_interval(days => $1)。
2. GET /admin/llm/logs/circuit-breakers — redis 客户端 decode_responses=True
   返回 str key,代码再调 key.decode() 报 AttributeError。修法:兼容 str/bytes。
"""
import json
from unittest.mock import AsyncMock, patch

import pytest


API = "/api/v1/admin/llm/logs"


# ── /daily:interval 参数类型回归 ─────────────────────────────────

@pytest.mark.asyncio
async def test_daily_cost_sql_uses_make_interval_with_int_days(client, fake_db):
    """days 以 int 绑定时,SQL 必须用 make_interval 而非字符串拼接 ::interval。

    回归背景:`($1 || ' days')::interval` 中 `||` 要求 text,int 参数在
    asyncpg 编码阶段抛 TypeError → 500。FakeDB 不执行 SQL,故直接钉 SQL 形态。
    """
    resp = await client.get(f"{API}/daily", params={"days": 30})

    assert resp.status_code == 200
    sql = fake_db.fetch_all.call_args.args[0]
    assert "make_interval" in sql, "daily SQL 应使用 make_interval(days => $1)"
    assert "|| ' days'" not in sql, "禁止 int 参数与 ' days' 字符串拼接"
    # days 保持 int 绑定(不靠转 str 绕过)
    bound_args = fake_db.fetch_all.call_args.args[1:]
    assert bound_args[0] == 30 and isinstance(bound_args[0], int)


@pytest.mark.asyncio
async def test_daily_cost_with_engine_name_filter(client, fake_db):
    """带 engine_name 过滤时占位符与参数一一对应,仍返回 200。"""
    resp = await client.get(
        f"{API}/daily", params={"days": 7, "engine_name": "rag_qa"}
    )

    assert resp.status_code == 200
    sql = fake_db.fetch_all.call_args.args[0]
    bound_args = fake_db.fetch_all.call_args.args[1:]
    assert "$2" in sql
    assert bound_args == (7, "rag_qa")


# ── /circuit-breakers:redis str/bytes key 兼容回归 ───────────────

def _fake_redis(keys: list, payload: dict) -> AsyncMock:
    r = AsyncMock()
    r.keys = AsyncMock(return_value=keys)
    r.get = AsyncMock(return_value=json.dumps(payload))
    return r


@pytest.mark.asyncio
async def test_cb_status_with_str_keys(client):
    """decode_responses=True 场景:keys() 返回 str,不得再 .decode()。

    回归背景:str.decode() 不存在 → AttributeError → 500。
    """
    redis = _fake_redis(["cb:rag_qa:primary"], {"state": "open", "failures": 5})

    with patch("dependencies.get_redis", AsyncMock(return_value=redis)):
        resp = await client.get(f"{API}/circuit-breakers")

    assert resp.status_code == 200
    body = resp.json()
    assert body == [{"key": "cb:rag_qa:primary", "state": "open", "failures": 5}]


@pytest.mark.asyncio
async def test_cb_status_with_bytes_keys(client):
    """decode_responses=False 场景:bytes key 仍正确解码(向后兼容)。"""
    redis = _fake_redis([b"cb:kg_compliance_reasoning:primary"], {"state": "closed"})

    with patch("dependencies.get_redis", AsyncMock(return_value=redis)):
        resp = await client.get(f"{API}/circuit-breakers")

    assert resp.status_code == 200
    body = resp.json()
    assert body[0]["key"] == "cb:kg_compliance_reasoning:primary"
    assert body[0]["state"] == "closed"


@pytest.mark.asyncio
async def test_cb_status_empty(client):
    """无断路器键时返回空列表而非报错。"""
    redis = _fake_redis([], {})

    with patch("dependencies.get_redis", AsyncMock(return_value=redis)):
        resp = await client.get(f"{API}/circuit-breakers")

    assert resp.status_code == 200
    assert resp.json() == []
