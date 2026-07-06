"""套图审查 Router 测试（创建/列表/详情进度聚合）"""
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _row(**values):
    return values


PROJECT_ID = "22222222-2222-2222-2222-222222222222"
BATCH_ID = "99999999-9999-9999-9999-999999999999"
DRAWING_1 = "77777777-7777-7777-7777-777777777771"
DRAWING_2 = "77777777-7777-7777-7777-777777777772"


# ── 创建批次 ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_batch_with_explicit_drawing_ids(client, fake_db):
    # Arrange：项目存在 + 两张 draft 图纸 + 批次/报告占位插入
    fake_db.fetch_all.return_value = [
        _row(id=DRAWING_1, status="draft", file_size_kb=100),
        _row(id=DRAWING_2, status="draft", file_size_kb=200),
    ]
    fake_db.fetch_one.side_effect = [
        _row(id=PROJECT_ID),          # 项目校验
        _row(id=BATCH_ID),            # 批次插入
        _row(id="rep-1"),             # 报告占位 1
        _row(id="rep-2"),             # 报告占位 2
    ]
    run_delay = MagicMock()
    finalize_delay = MagicMock()

    with (
        patch("routers.review_batches.run_ai_review.delay", run_delay),
        patch("routers.review_batches.finalize_batch_review.delay", finalize_delay),
        patch("routers.review_batches.write_audit", new=AsyncMock()),
    ):
        resp = await client.post(
            "/api/v1/review-batches",
            json={"project_id": PROJECT_ID, "drawing_ids": [DRAWING_1, DRAWING_2]},
        )

    # Assert
    assert resp.status_code == 201
    data = resp.json()
    assert data["batch_id"] == BATCH_ID
    assert data["scope"] == "multi"
    assert data["total"] == 2
    assert data["triggered"] == 2
    assert run_delay.call_count == 2
    finalize_delay.assert_called_once_with(BATCH_ID)


@pytest.mark.asyncio
async def test_create_batch_single_scope_derived_from_one_drawing(client, fake_db):
    fake_db.fetch_all.return_value = [_row(id=DRAWING_1, status="draft", file_size_kb=50)]
    fake_db.fetch_one.side_effect = [_row(id=PROJECT_ID), _row(id=BATCH_ID), _row(id="rep-1")]

    with (
        patch("routers.review_batches.run_ai_review.delay"),
        patch("routers.review_batches.finalize_batch_review.delay"),
        patch("routers.review_batches.write_audit", new=AsyncMock()),
    ):
        resp = await client.post(
            "/api/v1/review-batches",
            json={"project_id": PROJECT_ID, "drawing_ids": [DRAWING_1]},
        )

    assert resp.status_code == 201
    assert resp.json()["scope"] == "single"


@pytest.mark.asyncio
async def test_create_batch_full_set_when_drawing_ids_omitted(client, fake_db):
    fake_db.fetch_all.return_value = [
        _row(id=DRAWING_1, status="draft", file_size_kb=100),
        _row(id=DRAWING_2, status="ai_done", file_size_kb=100),
    ]
    fake_db.fetch_one.side_effect = [
        _row(id=PROJECT_ID),
        _row(id=BATCH_ID),
        _row(id="rep-1"),
        _row(id="rep-2"),
    ]

    with (
        patch("routers.review_batches.run_ai_review.delay") as run_delay,
        patch("routers.review_batches.finalize_batch_review.delay"),
        patch("routers.review_batches.write_audit", new=AsyncMock()),
    ):
        resp = await client.post(
            "/api/v1/review-batches", json={"project_id": PROJECT_ID}
        )

    assert resp.status_code == 201
    data = resp.json()
    assert data["scope"] == "full_set"
    assert data["total"] == 2
    assert run_delay.call_count == 2


@pytest.mark.asyncio
async def test_create_batch_skips_triggering_ai_reviewing_drawing(client, fake_db):
    # 显式列表中含一张已在审的图纸：计入 total，但不重复触发
    fake_db.fetch_all.return_value = [
        _row(id=DRAWING_1, status="draft", file_size_kb=100),
        _row(id=DRAWING_2, status="ai_reviewing", file_size_kb=100),
    ]
    fake_db.fetch_one.side_effect = [_row(id=PROJECT_ID), _row(id=BATCH_ID), _row(id="rep-1")]

    with (
        patch("routers.review_batches.run_ai_review.delay") as run_delay,
        patch("routers.review_batches.finalize_batch_review.delay"),
        patch("routers.review_batches.write_audit", new=AsyncMock()),
    ):
        resp = await client.post(
            "/api/v1/review-batches",
            json={"project_id": PROJECT_ID, "drawing_ids": [DRAWING_1, DRAWING_2]},
        )

    assert resp.status_code == 201
    data = resp.json()
    assert data["total"] == 2
    assert data["triggered"] == 1
    assert run_delay.call_count == 1


@pytest.mark.asyncio
async def test_create_batch_rejects_empty_full_set(client, fake_db):
    fake_db.fetch_one.side_effect = [_row(id=PROJECT_ID)]
    fake_db.fetch_all.return_value = []

    resp = await client.post("/api/v1/review-batches", json={"project_id": PROJECT_ID})

    assert resp.status_code == 400
    assert resp.json()["detail"] == "NO_REVIEWABLE_DRAWINGS"


@pytest.mark.asyncio
async def test_create_batch_rejects_drawing_of_other_project(client, fake_db):
    fake_db.fetch_one.side_effect = [_row(id=PROJECT_ID)]
    fake_db.fetch_all.return_value = [_row(id=DRAWING_1, status="draft", file_size_kb=10)]

    resp = await client.post(
        "/api/v1/review-batches",
        json={"project_id": PROJECT_ID, "drawing_ids": [DRAWING_1, DRAWING_2]},
    )

    assert resp.status_code == 400
    assert resp.json()["detail"] == "DRAWING_NOT_IN_PROJECT"


@pytest.mark.asyncio
async def test_create_batch_rejects_unknown_project(client, fake_db):
    fake_db.fetch_one.side_effect = [None]

    resp = await client.post("/api/v1/review-batches", json={"project_id": PROJECT_ID})

    assert resp.status_code == 404
    assert resp.json()["detail"] == "PROJECT_NOT_FOUND"


@pytest.mark.asyncio
async def test_create_batch_rejects_invalid_scope(client, fake_db):
    resp = await client.post(
        "/api/v1/review-batches",
        json={"project_id": PROJECT_ID, "scope": "everything"},
    )

    assert resp.status_code == 400
    assert resp.json()["detail"] == "INVALID_SCOPE"


# ── 批次列表 ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_batches_parses_jsonb_columns(client, fake_db):
    now = datetime.now(timezone.utc)
    fake_db.fetch_all.return_value = [
        _row(
            id=BATCH_ID,
            project_id=PROJECT_ID,
            scope="multi",
            drawing_ids=json.dumps([DRAWING_1, DRAWING_2]),
            status="done",
            summary=json.dumps({"total": 2, "done": 2, "failed": 0}),
            cross_findings=None,
            created_by="u1",
            created_at=now,
            completed_at=now,
        )
    ]
    fake_db.fetch_val.return_value = 1

    resp = await client.get(f"/api/v1/review-batches?project_id={PROJECT_ID}")

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    item = data["items"][0]
    assert item["drawing_ids"] == [DRAWING_1, DRAWING_2]
    assert item["summary"]["done"] == 2


# ── 批次详情 ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_batch_detail_aggregates_progress(client, fake_db):
    now = datetime.now(timezone.utc)
    fake_db.fetch_one.return_value = _row(
        id=BATCH_ID,
        project_id=PROJECT_ID,
        scope="multi",
        drawing_ids=json.dumps([DRAWING_1, DRAWING_2, "d3"]),
        status="processing",
        summary=None,
        cross_findings=None,
        created_by="u1",
        created_at=now,
        completed_at=None,
    )
    fake_db.fetch_all.return_value = [
        _row(drawing_id=DRAWING_1, drawing_no="JG-01", title="结构图", discipline="structure",
             report_status="done", total_issues=3, critical_issues=1),
        _row(drawing_id=DRAWING_2, drawing_no="JZ-01", title="建筑图", discipline="architecture",
             report_status="failed", total_issues=0, critical_issues=0),
        _row(drawing_id="d3", drawing_no="SD-01", title="机电图", discipline="mep",
             report_status="processing", total_issues=0, critical_issues=0),
    ]

    resp = await client.get(f"/api/v1/review-batches/{BATCH_ID}")

    assert resp.status_code == 200
    data = resp.json()
    assert data["batch"]["status"] == "processing"
    assert len(data["items"]) == 3
    assert data["progress"] == {"total": 3, "done": 1, "failed": 1, "processing": 1}


@pytest.mark.asyncio
async def test_get_batch_detail_returns_404_when_missing(client, fake_db):
    fake_db.fetch_one.return_value = None

    resp = await client.get(f"/api/v1/review-batches/{BATCH_ID}")

    assert resp.status_code == 404
    assert resp.json()["detail"] == "BATCH_NOT_FOUND"
