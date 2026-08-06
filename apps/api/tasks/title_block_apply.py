"""图框记忆批量套用(Celery 后台任务)。

**为什么必须异步**:每张图读不到档案文本时要做区域重识别,实测**每次数秒**;
138 张候选 × 若干模板 = 几分钟到几十分钟。放在 HTTP 请求里必被前端超时掐断——
用户看到的「批量刷新失败」就是这么来的,后端其实还在跑。

任务写进度到 `drawing_archive_status` 之外的独立轻量表不值当,直接用 Celery
的 result backend:前端拿 task_id 轮询即可。
"""
from __future__ import annotations

import asyncio
import logging

import databases

from core.celery_app import celery_app
from core.config import settings

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, max_retries=0)
def apply_title_block_templates_task(
    self, project_id: str, field: str = "discipline",
    limit: int = 500, ocr_budget: int = 400,
) -> dict:
    """把图框区域记忆套到尚未读到该字段的图纸(后台跑,前端轮询)。"""
    return asyncio.run(_run(self, project_id, field, limit, ocr_budget))


async def _run(task, project_id: str, field: str, limit: int, ocr_budget: int) -> dict:
    from services.title_block_apply import apply_templates

    db = databases.Database(settings.database_url)
    await db.connect()
    try:
        task.update_state(state="PROGRESS",
                          meta={"stage": "scanning", "project_id": project_id})
        result = await apply_templates(
            db, project_id, field, limit=limit, ocr_budget=ocr_budget)
        logger.info("[title_block] 批量套用完成 %s: %s", project_id, result)
        return result
    finally:
        await db.disconnect()
