"""
公示期自动推进任务 — Celery beat 定时任务

每小时执行一次，扫描所有 public_notice 状态且 notice_ends_at < now() 的提案，
自动推进至 distributing 状态，并写入审计日志。
"""
import asyncio
import logging

import databases

from core.celery_app import celery_app
from core.config import settings
from services.audit import write_audit

logger = logging.getLogger(__name__)


@celery_app.task(name="tasks.proposal_notice.advance_expired_notices")
def advance_expired_notices() -> dict:
    """推进公示期已到期的提案"""
    return asyncio.run(_do_advance())


async def _do_advance() -> dict:
    db = databases.Database(settings.database_url)
    await db.connect()
    advanced = 0
    try:
        rows = await db.fetch_all(
            """
            SELECT id, title
            FROM incentive_proposals
            WHERE status = 'public_notice'
              AND notice_ends_at IS NOT NULL
              AND notice_ends_at <= now()
            """
        )
        for row in rows:
            pid = str(row["id"])
            await db.execute(
                "UPDATE incentive_proposals SET status='distributing', updated_at=now() WHERE id=$1",
                pid,
            )
            await write_audit(
                db,
                user_id=None,
                action="advance_to_distributing",
                resource="incentive_proposal",
                resource_id=pid,
                old_state={"status": "public_notice"},
                new_state={"status": "distributing"},
                ip_address="celery-beat",
            )
            logger.info("提案 %s「%s」公示期已到，自动推进至 distributing", pid, row["title"])
            advanced += 1
    finally:
        await db.disconnect()
    return {"advanced": advanced}
