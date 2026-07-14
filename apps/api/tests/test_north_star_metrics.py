"""
Phase D · D-24 度量埋点 —— 三北极星指标测试

覆盖：
- ①关键路径完成时长（首图上传 → 首个创效提案草稿，跨项目中位数，小时）；
- ②建模自动触发采纳率（pipeline_suggestions rebuild_model：accepted/(accepted+dismissed)）；
- ③审校单条耗时（model_review_actions 按审校员分组后相邻动作时间差中位数，秒）；
- 边界：空数据返回 None 不硬造，负值/脏数据剔除，单条动作不产生样本；
- 取数：project_id 走参数化占位符（防注入）；
- 端点：统一信封 {success,data,error,meta}。
"""
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from main import app
from services.north_star_metrics import (
    compute_adoption_metric,
    compute_critical_path_metric,
    compute_review_duration_metric,
    fetch_critical_path_rows,
    fetch_pipeline_suggestion_rows,
    fetch_review_action_timing_rows,
)


# ── ①关键路径完成时长 ────────────────────────────────────────────

def test_critical_path_median_across_projects():
    """两个项目：24h 和 48h，中位数取平均（偶数个样本）。"""
    rows = [
        {
            "project_id": "p1",
            "first_drawing_at": datetime(2026, 7, 1, 0, 0, tzinfo=timezone.utc),
            "first_proposal_at": datetime(2026, 7, 2, 0, 0, tzinfo=timezone.utc),  # 24h
        },
        {
            "project_id": "p2",
            "first_drawing_at": datetime(2026, 7, 1, 0, 0, tzinfo=timezone.utc),
            "first_proposal_at": datetime(2026, 7, 3, 0, 0, tzinfo=timezone.utc),  # 48h
        },
    ]

    metric = compute_critical_path_metric(rows)

    assert metric["medianHours"] == 36.0
    assert metric["sampleSize"] == 2
    assert metric["unit"] == "hours"


def test_critical_path_discards_negative_diff_as_dirty_data():
    """提案早于首图（脏数据/时钟异常）的项目剔除，不参与中位数也不当 0。"""
    rows = [
        {
            "project_id": "p-bad",
            "first_drawing_at": datetime(2026, 7, 2, tzinfo=timezone.utc),
            "first_proposal_at": datetime(2026, 7, 1, tzinfo=timezone.utc),
        },
    ]

    metric = compute_critical_path_metric(rows)

    assert metric["medianHours"] is None
    assert metric["sampleSize"] == 0


def test_critical_path_empty_returns_none_not_zero():
    """无可比对项目：显式返回 None，不用 0 制造假象。"""
    metric = compute_critical_path_metric([])

    assert metric["medianHours"] is None
    assert metric["sampleSize"] == 0


def test_critical_path_accepts_iso_string_timestamps():
    """兼容测试/序列化场景下传入的 ISO 字符串时间戳。"""
    rows = [
        {
            "project_id": "p1",
            "first_drawing_at": "2026-07-01T00:00:00+00:00",
            "first_proposal_at": "2026-07-01T12:00:00+00:00",  # 12h
        },
    ]

    metric = compute_critical_path_metric(rows)

    assert metric["medianHours"] == 12.0


# ── ②建模自动触发采纳率 ──────────────────────────────────────────

def test_adoption_rate_rebuild_model_only():
    """3 accepted / 1 dismissed（rebuild_model）→ 0.75；create_proposal 不计入头图指标。"""
    rows = (
        [{"suggestion_type": "rebuild_model", "status": "accepted"} for _ in range(3)]
        + [{"suggestion_type": "rebuild_model", "status": "dismissed"}]
        + [{"suggestion_type": "create_proposal", "status": "accepted"}]
    )

    metric = compute_adoption_metric(rows)

    assert metric["rate"] == 0.75
    assert metric["accepted"] == 3
    assert metric["dismissed"] == 1
    assert metric["sampleSize"] == 4
    assert metric["bySuggestionType"]["create_proposal"]["rate"] == 1.0


def test_adoption_rate_no_rebuild_model_suggestions_returns_none():
    """rebuild_model 无样本（只有 create_proposal）：头图指标 None，不硬造。"""
    rows = [{"suggestion_type": "create_proposal", "status": "accepted"}]

    metric = compute_adoption_metric(rows)

    assert metric["rate"] is None
    assert metric["sampleSize"] == 0


def test_adoption_rate_empty_returns_none():
    metric = compute_adoption_metric([])

    assert metric["rate"] is None
    assert metric["accepted"] == 0
    assert metric["dismissed"] == 0
    assert metric["bySuggestionType"] == {}


# ── ③审校单条耗时 ────────────────────────────────────────────────

def test_review_duration_median_of_adjacent_diffs_per_reviewer():
    """单一审校员 3 条动作，间隔 10s / 30s → 相邻差值 [10, 30]，中位数 20。"""
    rows = [
        {"reviewer_id": "u1", "created_at": datetime(2026, 7, 1, 9, 0, 0, tzinfo=timezone.utc)},
        {"reviewer_id": "u1", "created_at": datetime(2026, 7, 1, 9, 0, 10, tzinfo=timezone.utc)},
        {"reviewer_id": "u1", "created_at": datetime(2026, 7, 1, 9, 0, 40, tzinfo=timezone.utc)},
    ]

    metric = compute_review_duration_metric(rows)

    assert metric["medianSeconds"] == 20.0
    assert metric["sampleSize"] == 2


def test_review_duration_groups_by_reviewer_to_avoid_interleaving_bias():
    """两个审校员交替动作：按 reviewer 分组后各自只有 1 条，不产生虚假的短间隔样本。"""
    rows = [
        {"reviewer_id": "u1", "created_at": datetime(2026, 7, 1, 9, 0, 0, tzinfo=timezone.utc)},
        {"reviewer_id": "u2", "created_at": datetime(2026, 7, 1, 9, 0, 1, tzinfo=timezone.utc)},
    ]

    metric = compute_review_duration_metric(rows)

    # 若未按 reviewer 分组，会错误地把两人交替动作算成 1 秒的"超快审校"；
    # 正确口径下各自组内只有 1 条动作，无相邻差值，样本数为 0。
    assert metric["sampleSize"] == 0
    assert metric["medianSeconds"] is None


def test_review_duration_missing_reviewer_id_falls_into_unknown_bucket():
    """reviewer_id 缺失归入统一分组，仍参与统计不丢样本。"""
    rows = [
        {"reviewer_id": None, "created_at": datetime(2026, 7, 1, 9, 0, 0, tzinfo=timezone.utc)},
        {"reviewer_id": None, "created_at": datetime(2026, 7, 1, 9, 0, 5, tzinfo=timezone.utc)},
    ]

    metric = compute_review_duration_metric(rows)

    assert metric["medianSeconds"] == 5.0
    assert metric["sampleSize"] == 1


def test_review_duration_empty_returns_none():
    metric = compute_review_duration_metric([])

    assert metric["medianSeconds"] is None
    assert metric["sampleSize"] == 0


# ── 取数：参数化防注入 ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fetch_critical_path_rows_uses_parameterized_project_filter():
    fake_db = AsyncMock()
    fake_db.fetch_all = AsyncMock(return_value=[])

    await fetch_critical_path_rows(fake_db, "proj-1")

    sql, *params = fake_db.fetch_all.call_args.args
    assert "$1" in sql
    assert params == ["proj-1"]
    assert "proj-1" not in sql


@pytest.mark.asyncio
async def test_fetch_pipeline_suggestion_rows_filters_resolved_statuses():
    fake_db = AsyncMock()
    fake_db.fetch_all = AsyncMock(return_value=[])

    await fetch_pipeline_suggestion_rows(fake_db, None)

    sql, *params = fake_db.fetch_all.call_args.args
    assert "accepted" in sql and "dismissed" in sql
    assert params == []


@pytest.mark.asyncio
async def test_fetch_review_action_timing_rows_orders_by_reviewer_then_time():
    fake_db = AsyncMock()
    fake_db.fetch_all = AsyncMock(return_value=[])

    await fetch_review_action_timing_rows(fake_db, "proj-1")

    sql, *params = fake_db.fetch_all.call_args.args
    assert "ORDER BY reviewer_id, created_at" in sql
    assert params == ["proj-1"]


# ── 端点：统一信封 ─────────────────────────────────────────────────

@pytest_asyncio.fixture
async def north_star_client():
    from dependencies import get_db, get_current_user

    class FakeDB:
        fetch_all = AsyncMock(return_value=[])
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
async def test_endpoint_envelope_and_structure(north_star_client):
    resp = await north_star_client.get(
        "/api/v1/dashboard/north-star-metrics", params={"project_id": "proj-1"}
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["error"] is None
    assert body["meta"]["project_id"] == "proj-1"

    data = body["data"]
    for key in ("criticalPathDuration", "modelAutoTriggerAdoption", "reviewActionDuration"):
        assert key in data, f"缺少字段: {key}"
    # 无数据时三指标均诚实返回 None，不硬造
    assert data["criticalPathDuration"]["medianHours"] is None
    assert data["modelAutoTriggerAdoption"]["rate"] is None
    assert data["reviewActionDuration"]["medianSeconds"] is None


@pytest.mark.asyncio
async def test_endpoint_error_returns_failure_envelope(north_star_client):
    from dependencies import get_db

    class BoomDB:
        fetch_all = AsyncMock(side_effect=RuntimeError("db down"))
        execute = AsyncMock()

    app.dependency_overrides[get_db] = lambda: BoomDB()

    resp = await north_star_client.get("/api/v1/dashboard/north-star-metrics")

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert body["data"] is None
    assert "db down" in body["error"]
