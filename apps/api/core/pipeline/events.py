"""Pipeline 事件记录与发射（D-08）。

事件驱动编排层的写入侧：调用方（如 tasks/ai_review.py 审图完成落库处）在关键
节点调用 ``emit_event()``：
  1. 落一条 ``pipeline_events`` 记录（可审计、可重放，即便 Celery 派发失败
     事件本身也不丢）
  2. 默认异步派发 Celery 任务 ``tasks.pipeline.process_pipeline_event``

设计取舍：``emit_event`` 本身只做「记录 + 尽力派发」，从不抛出会打断调用方
主流程的异常（Celery broker 抖动不应影响审图/建模主链路）；调用方仍应在
外层用 try/except 包裹整个 emit 调用，做双重保险（见 tasks/ai_review.py）。
"""
from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# ── 事件类型 ─────────────────────────────────────────────────────
# drawing.uploaded 的自动审图触发已存在于 routers/drawings.py（本工作块不改），
# 此处仅登记类型名供未来该点位补记事件审计时复用。
EVENT_DRAWING_UPLOADED = "drawing.uploaded"
EVENT_AI_REVIEW_COMPLETED = "ai_review.completed"
EVENT_MODEL_BUILT = "model.built"

_INSERT_EVENT_SQL = """
INSERT INTO pipeline_events (event_type, project_id, source_id, payload, status)
VALUES (:event_type, :project_id, :source_id, CAST(:payload AS jsonb), 'pending')
RETURNING id
"""

_MARK_STATUS_SQL = """
UPDATE pipeline_events
SET status=:status, error=:error, processed_at=now()
WHERE id=:event_id
"""


async def emit_event(
    db,
    *,
    event_type: str,
    project_id: str,
    source_id: str | None = None,
    payload: dict[str, Any] | None = None,
    dispatch: bool = True,
) -> str | None:
    """记录一条事件并（默认）派发 Celery 异步消费，返回事件 id。

    落库失败或派发失败均不向上抛出——事件编排层是「锦上添花」的自动化，
    绝不能因为它反过来拖垮审图/建模等核心业务链路。失败时返回 None 并记日志。
    """
    try:
        row = await db.fetch_one(
            _INSERT_EVENT_SQL,
            {
                "event_type": event_type,
                "project_id": project_id,
                "source_id": source_id,
                "payload": json.dumps(payload or {}, ensure_ascii=False, default=str),
            },
        )
    except Exception:
        logger.exception(
            "pipeline 事件落库失败 type=%s project_id=%s（不影响主流程）",
            event_type, project_id,
        )
        return None

    event_id = str(row["id"])

    if dispatch:
        try:
            from tasks.pipeline import process_pipeline_event
            process_pipeline_event.delay(event_id)
        except Exception:
            logger.exception(
                "pipeline 事件派发失败 event_id=%s type=%s（事件已落库，可人工重放）",
                event_id, event_type,
            )

    return event_id


async def mark_event_status(db, event_id: str, status: str, error: str | None = None) -> None:
    await db.execute(
        _MARK_STATUS_SQL,
        {"event_id": event_id, "status": status, "error": error[:500] if error else None},
    )
