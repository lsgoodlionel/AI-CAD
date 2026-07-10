"""
C-17 返工点埋点与审校收敛度量看板 —— 后端度量测试

数据源：model_review_actions（人审动作埋点，C-15/C-16 写、C-17 聚合读）。
本套测试向该表「插入」合成埋点行（经 FakeDB.fetch_all 注入，CI 无需真实 PG），
断言：
- 确认率/改类率/否定率/补框率计算正确；
- 按专业 / 按类别返工率（rework = reclass+reject+addbox）正确；
- 收敛趋势（按天）随时间下降序列正确、升序；
- 边界：空数据返回零值不报错；
- 端点：统一信封 {success,data,error,meta}，data 对齐前端 ReviewMetrics 结构。
"""
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from main import app
from routers.dashboard import (
    _compute_review_metrics,
    _compute_trend,
    _fetch_review_action_rows,
    _group_rework,
)


# ── 合成埋点行工厂 ────────────────────────────────────────────────

def _action(
    action_type: str,
    *,
    discipline: str | None = None,
    old_category: str | None = None,
    new_category: str | None = None,
    created_at: str = "2026-07-01",
) -> dict:
    """构造一条 model_review_actions 埋点行。"""
    return {
        "action_type": action_type,
        "discipline": discipline,
        "old_category": old_category,
        "new_category": new_category,
        "created_at": created_at,
    }


# ── 总体率 ────────────────────────────────────────────────────────

def test_overall_rates_from_synthetic_actions():
    """10 条埋点：4 confirm / 2 reclass / 2 reject / 1 addbox / 1 edit。"""
    rows = (
        [_action("confirm") for _ in range(4)]
        + [_action("reclass") for _ in range(2)]
        + [_action("reject") for _ in range(2)]
        + [_action("addbox")]
        + [_action("edit")]
    )

    metrics = _compute_review_metrics(rows)

    assert metrics["confirmRate"] == 0.4
    assert metrics["reclassRate"] == 0.2
    assert metrics["rejectRate"] == 0.2
    assert metrics["addboxRate"] == 0.1


def test_rework_is_reclass_reject_addbox_only():
    """rework 口径 = reclass+reject+addbox；confirm/edit 不计入返工。"""
    rows = [
        _action("confirm", discipline="结构"),
        _action("edit", discipline="结构"),
        _action("reclass", discipline="结构"),
        _action("reject", discipline="结构"),
        _action("addbox", discipline="结构"),
    ]

    by_discipline = _compute_review_metrics(rows)["byDiscipline"]

    assert by_discipline["结构"]["total"] == 5
    assert by_discipline["结构"]["rework"] == 3  # reclass+reject+addbox
    assert by_discipline["结构"]["reworkRate"] == 0.6


# ── 分专业 ────────────────────────────────────────────────────────

def test_by_discipline_grouping():
    """结构 50% 返工、机电 0% 返工，分组独立统计。"""
    rows = [
        _action("reclass", discipline="结构"),
        _action("confirm", discipline="结构"),
        _action("confirm", discipline="机电"),
        _action("confirm", discipline="机电"),
    ]

    by_discipline = _compute_review_metrics(rows)["byDiscipline"]

    assert by_discipline["结构"]["reworkRate"] == 0.5
    assert by_discipline["机电"]["reworkRate"] == 0.0


def test_null_discipline_falls_into_unlabeled_bucket():
    """discipline 为空归入「未标注」，不丢数据。"""
    rows = [_action("reject", discipline=None)]

    by_discipline = _compute_review_metrics(rows)["byDiscipline"]

    assert "未标注" in by_discipline
    assert by_discipline["未标注"]["rework"] == 1


# ── 分类别 ────────────────────────────────────────────────────────

def test_by_category_uses_old_category_then_new():
    """类别归因：优先 old_category（机器初模类别），补框回落 new_category。"""
    rows = [
        _action("reclass", old_category="beam", new_category="column"),
        _action("confirm", old_category="beam"),
        _action("addbox", old_category=None, new_category="pipe"),
    ]

    by_category = _compute_review_metrics(rows)["byCategory"]

    assert by_category["beam"]["total"] == 2
    assert by_category["beam"]["rework"] == 1  # 只有 reclass
    assert by_category["pipe"]["total"] == 1
    assert by_category["pipe"]["rework"] == 1  # addbox 计入返工


def test_group_rework_helper_direct():
    """_group_rework 直接单测：total/rework/reworkRate 结构正确。"""
    rows = [_action("reject", old_category="wall"), _action("confirm", old_category="wall")]

    grouped = _group_rework(rows, lambda r: r.get("old_category"))

    assert grouped["wall"] == {"total": 2, "rework": 1, "reworkRate": 0.5}


# ── 收敛趋势 ──────────────────────────────────────────────────────

def test_trend_is_descending_over_time():
    """三天返工率 100% → 50% → 0%，趋势升序且返工率下降（收敛）。"""
    rows = [
        # Day1：2 条全返工 → 1.0
        _action("reclass", created_at="2026-07-01"),
        _action("reject", created_at="2026-07-01"),
        # Day2：2 条 1 返工 → 0.5
        _action("reclass", created_at="2026-07-02"),
        _action("confirm", created_at="2026-07-02"),
        # Day3：2 条 0 返工 → 0.0
        _action("confirm", created_at="2026-07-03"),
        _action("confirm", created_at="2026-07-03"),
    ]

    trend = _compute_trend(rows)

    assert [t["period"] for t in trend] == ["2026-07-01", "2026-07-02", "2026-07-03"]
    assert [t["reworkRate"] for t in trend] == [1.0, 0.5, 0.0]
    assert [t["count"] for t in trend] == [2, 2, 2]
    # 收敛：后一天返工率不高于前一天
    rates = [t["reworkRate"] for t in trend]
    assert all(rates[i] >= rates[i + 1] for i in range(len(rates) - 1))


def test_trend_accepts_datetime_created_at():
    """created_at 为 datetime 时也能按天分桶（兼容 asyncpg 返回类型）。"""
    rows = [
        _action("confirm", created_at=datetime(2026, 7, 1, 9, 0, tzinfo=timezone.utc)),
        _action("reject", created_at=datetime(2026, 7, 1, 18, 0, tzinfo=timezone.utc)),
    ]

    trend = _compute_trend(rows)

    assert len(trend) == 1
    assert trend[0]["period"] == "2026-07-01"
    assert trend[0]["reworkRate"] == 0.5


# ── 边界：空数据 ──────────────────────────────────────────────────

def test_empty_data_returns_zero_values():
    """空埋点不报错，全部率为 0，分组/趋势为空容器。"""
    metrics = _compute_review_metrics([])

    assert metrics["confirmRate"] == 0.0
    assert metrics["reclassRate"] == 0.0
    assert metrics["rejectRate"] == 0.0
    assert metrics["addboxRate"] == 0.0
    assert metrics["byDiscipline"] == {}
    assert metrics["byCategory"] == {}
    assert metrics["trend"] == []


# ── 参数化取数：防注入 + 过滤 ─────────────────────────────────────

@pytest.mark.asyncio
async def test_fetch_uses_parameterized_filters():
    """project_id + discipline 走占位符参数，SQL 不拼接用户值（防注入）。"""
    fake_db = AsyncMock()
    fake_db.fetch_all = AsyncMock(return_value=[])

    await _fetch_review_action_rows(fake_db, "proj-1", "结构")

    sql, *params = fake_db.fetch_all.call_args.args
    assert "$1" in sql and "$2" in sql
    assert params == ["proj-1", "结构"]
    assert "proj-1" not in sql  # 值不出现在 SQL 文本中


@pytest.mark.asyncio
async def test_fetch_without_filters_has_no_where():
    """无过滤条件时不带 WHERE，取全量。"""
    fake_db = AsyncMock()
    fake_db.fetch_all = AsyncMock(return_value=[])

    await _fetch_review_action_rows(fake_db, None, None)

    sql, *params = fake_db.fetch_all.call_args.args
    assert "WHERE" not in sql
    assert params == []


# ── 端点：统一信封 + 结构对齐 ReviewMetrics ──────────────────────

@pytest_asyncio.fixture
async def metrics_client():
    from dependencies import get_db, get_current_user

    synthetic = [
        _action("confirm", discipline="结构", old_category="column", created_at="2026-07-01"),
        _action("reclass", discipline="结构", old_category="beam", created_at="2026-07-02"),
        _action("reject", discipline="机电", old_category="pipe", created_at="2026-07-02"),
    ]

    class FakeDB:
        fetch_all = AsyncMock(return_value=synthetic)
        fetch_one = AsyncMock(return_value=None)
        execute = AsyncMock()

    app.dependency_overrides[get_db] = lambda: FakeDB()
    app.dependency_overrides[get_current_user] = lambda: {
        "id": str(uuid.uuid4()), "username": "designer", "role": "designer"
    }
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            yield ac
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_endpoint_envelope_and_structure(metrics_client):
    """端点返回统一信封，data 含 ReviewMetrics 全部字段。"""
    resp = await metrics_client.get(
        "/api/v1/dashboard/model-review-metrics", params={"project_id": "proj-1"}
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["error"] is None
    assert body["meta"]["total"] == 3
    assert body["meta"]["rework_actions"] == ["addbox", "reclass", "reject"]

    data = body["data"]
    for key in ("confirmRate", "reclassRate", "rejectRate", "addboxRate",
                "byDiscipline", "byCategory", "trend"):
        assert key in data, f"缺少字段: {key}"
    assert data["confirmRate"] == round(1 / 3, 4)
    assert data["byDiscipline"]["机电"]["reworkRate"] == 1.0
    assert [t["period"] for t in data["trend"]] == ["2026-07-01", "2026-07-02"]


@pytest.mark.asyncio
async def test_endpoint_error_returns_failure_envelope(metrics_client):
    """取数异常时返回 success=False 信封而非 500（边界兜底）。"""
    from dependencies import get_db

    class BoomDB:
        fetch_all = AsyncMock(side_effect=RuntimeError("db down"))
        execute = AsyncMock()

    app.dependency_overrides[get_db] = lambda: BoomDB()

    resp = await metrics_client.get("/api/v1/dashboard/model-review-metrics")

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert body["data"] is None
    assert "db down" in body["error"]


@pytest.mark.asyncio
async def test_endpoint_empty_data_ok(metrics_client):
    """无埋点数据端点仍 200，率为 0（覆盖空边界端到端）。"""
    from dependencies import get_db

    class EmptyDB:
        fetch_all = AsyncMock(return_value=[])
        execute = AsyncMock()

    app.dependency_overrides[get_db] = lambda: EmptyDB()

    resp = await metrics_client.get("/api/v1/dashboard/model-review-metrics")

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["meta"]["total"] == 0
    assert body["data"]["confirmRate"] == 0.0
    assert body["data"]["trend"] == []
