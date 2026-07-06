"""套图汇总任务 finalize_batch_review 测试（终态判定 / 重试 / 摘要聚合）"""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tasks.batch_review import (
    BatchNotReady,
    _batch_status,
    _finalize_batch,
    finalize_batch_review,
)

PROJECT_ID = "22222222-2222-2222-2222-222222222222"
BATCH_ID = "99999999-9999-9999-9999-999999999999"
DRAWING_1 = "77777777-7777-7777-7777-777777777771"
DRAWING_2 = "77777777-7777-7777-7777-777777777772"


class _FakeDB:
    """fetch_all 依序返回：报告行 → 跨图分析图纸行 → 跨图分析问题行"""

    def __init__(self, batch: dict | None, fetch_all_results: list):
        self.fetch_one = AsyncMock(return_value=batch)
        self.fetch_all = AsyncMock(side_effect=fetch_all_results)
        self.execute = AsyncMock(return_value=None)


def _batch_row() -> dict:
    return {
        "id": BATCH_ID,
        "project_id": PROJECT_ID,
        "drawing_ids": json.dumps([DRAWING_1, DRAWING_2]),
    }


def _report(drawing_id: str, status: str) -> dict:
    return {"drawing_id": drawing_id, "status": status}


def _cross_rows() -> list:
    """analyze_batch 需要的两次 fetch_all：图纸行 + 问题行"""
    drawings = [
        {"id": DRAWING_1, "drawing_no": "JG-01", "version": "A", "discipline": "structure"},
        {"id": DRAWING_2, "drawing_no": "JZ-01", "version": "A", "discipline": "architecture"},
    ]
    issues = [
        {
            "drawing_id": DRAWING_1, "drawing_no": "JG-01", "discipline": "structure",
            "severity": "critical", "location_json": None,
            "interface_related": None, "review_method": None,
        }
    ]
    return [drawings, issues]


# ── 终态判定 ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_finalize_all_done_marks_batch_done():
    # Arrange
    db = _FakeDB(
        _batch_row(),
        [[_report(DRAWING_1, "done"), _report(DRAWING_2, "done")], *_cross_rows()],
    )

    # Act
    result = await _finalize_batch(db, BATCH_ID)

    # Assert
    assert result["status"] == "done"
    assert result["summary"]["total"] == 2
    assert result["summary"]["done"] == 2
    assert result["summary"]["failed"] == 0
    assert result["summary"]["issues_total"] == 1
    assert result["summary"]["critical_total"] == 1
    update_params = db.execute.await_args.args[1]
    assert update_params["status"] == "done"
    assert json.loads(update_params["cross"])["严重度分布"]["critical"] == 1


@pytest.mark.asyncio
async def test_finalize_with_one_failed_marks_partial_failed():
    db = _FakeDB(
        _batch_row(),
        [[_report(DRAWING_1, "done"), _report(DRAWING_2, "failed")], *_cross_rows()],
    )

    result = await _finalize_batch(db, BATCH_ID)

    assert result["status"] == "partial_failed"
    assert result["summary"]["done"] == 1
    assert result["summary"]["failed"] == 1


@pytest.mark.asyncio
async def test_finalize_all_failed_marks_batch_failed():
    db = _FakeDB(
        _batch_row(),
        [[_report(DRAWING_1, "failed"), _report(DRAWING_2, "failed")], *_cross_rows()],
    )

    result = await _finalize_batch(db, BATCH_ID)

    assert result["status"] == "failed"


# ── 未就绪 → 重试 ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_finalize_raises_not_ready_when_report_still_processing():
    db = _FakeDB(
        _batch_row(),
        [[_report(DRAWING_1, "done"), _report(DRAWING_2, "processing")]],
    )

    with pytest.raises(BatchNotReady):
        await _finalize_batch(db, BATCH_ID)

    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_finalize_raises_not_ready_when_report_missing():
    # 图纸尚无任何报告行（排队中）→ 视为未就绪
    db = _FakeDB(_batch_row(), [[_report(DRAWING_1, "done")]])

    with pytest.raises(BatchNotReady):
        await _finalize_batch(db, BATCH_ID)


@pytest.mark.asyncio
async def test_finalize_raises_for_unknown_batch():
    db = _FakeDB(None, [])

    with pytest.raises(ValueError):
        await _finalize_batch(db, BATCH_ID)


# ── Celery 包装层 ─────────────────────────────────────────────

def test_task_retries_when_batch_not_ready():
    def _raise(_batch_id: str):
        raise BatchNotReady("未完成图纸 1/2")

    retry_mock = MagicMock(side_effect=RuntimeError("RETRY_CALLED"))
    with (
        patch("tasks.batch_review._do_finalize", side_effect=_raise),
        patch.object(finalize_batch_review, "retry", retry_mock),
        pytest.raises(RuntimeError, match="RETRY_CALLED"),
    ):
        finalize_batch_review(BATCH_ID)

    retry_mock.assert_called_once()


def test_task_returns_result_when_ready():
    async def _fake(_batch_id: str) -> dict:
        return {"batch_id": BATCH_ID, "status": "done", "summary": {}}

    with patch("tasks.batch_review._do_finalize", _fake):
        result = finalize_batch_review(BATCH_ID)

    assert result["status"] == "done"


# ── 状态函数边界 ──────────────────────────────────────────────

def test_batch_status_boundaries():
    assert _batch_status(done=3, failed=0, total=3) == "done"
    assert _batch_status(done=2, failed=1, total=3) == "partial_failed"
    assert _batch_status(done=0, failed=3, total=3) == "failed"
