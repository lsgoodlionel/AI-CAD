"""Pipeline 编排 Celery 任务（D-08）。

消费 ``pipeline_events`` 表中落库的事件，调用 ``core/pipeline/handlers`` 生成
建议待办。自动打底、人工确认：本任务只写 ``pipeline_suggestions`` /
``pipeline_events.status``，不触发任何三审/签字/重建等有副作用的硬动作。

坑（对齐 tasks/ai_review.py 的既有模式）：Celery worker 必须能 import 到本
模块才会注册任务，否则 ``.delay()`` 派发时报 NotRegistered——已在
core/celery_app.py 的 include 列表 + task_routes 中登记。
"""
import asyncio
import json
import logging
from typing import Any

import databases

from core.celery_app import celery_app
from core.config import settings
from core.pipeline import events, handlers

logger = logging.getLogger(__name__)


def _parse_jsonb(value: Any, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return default
    return value


@celery_app.task(bind=True, max_retries=3, default_retry_delay=30)
def process_pipeline_event(self, event_id: str) -> dict:
    logger.info("pipeline 事件处理启动: event_id=%s", event_id)
    try:
        return asyncio.run(_process(event_id))
    except Exception as exc:
        logger.error("pipeline 事件处理失败: event_id=%s err=%s", event_id, exc)
        try:
            asyncio.run(_mark_failed(event_id, str(exc)))
        except Exception:
            logger.exception("pipeline 事件失败状态回写失败: event_id=%s", event_id)
        raise self.retry(exc=exc)


async def _process(event_id: str) -> dict:
    db = databases.Database(settings.database_url)
    await db.connect()
    try:
        row = await db.fetch_one(
            "SELECT id, event_type, project_id, source_id, payload FROM pipeline_events WHERE id=:event_id",
            {"event_id": event_id},
        )
        if row is None:
            raise ValueError(f"pipeline_events 记录不存在: {event_id}")

        event = dict(row)
        event["payload"] = _parse_jsonb(event.get("payload"), {})

        await db.execute(
            "UPDATE pipeline_events SET status='processing' WHERE id=:event_id",
            {"event_id": event_id},
        )

        result = await handlers.dispatch(db, event)

        await events.mark_event_status(db, event_id, "done")

        logger.info(
            "pipeline 事件处理完成: event_id=%s type=%s result=%s",
            event_id, event["event_type"], result,
        )
        return {"event_id": event_id, "event_type": event["event_type"], **result}
    finally:
        await db.disconnect()


async def _mark_failed(event_id: str, error: str) -> None:
    db = databases.Database(settings.database_url)
    await db.connect()
    try:
        await db.execute(
            """
            UPDATE pipeline_events
            SET status='failed', error=:error, processed_at=now()
            WHERE id=:event_id
            """,
            {"event_id": event_id, "error": error[:500]},
        )
    finally:
        await db.disconnect()
