"""图纸说明重建 Celery 任务。

**为什么单独一步**：档案里存的是 OCR/矢量的**行级碎片**（实测 249303 条
`note` 平均 14 字符），而需求要的是「完整把文字全部识别出来」，
且这份说明是「后期建模和审图中所有内容的总要求和验证起点」。

**不重跑 OCR**：碎片带位置、置信 0.92~0.99，说明直接从档案重建
（Phase E「抽取一次·单一真相源」）。所以这个任务**只读档案、只写档案**，
不碰 MinIO、不跑识别，整项目跑一遍很快。
"""
import asyncio
import logging

import databases

from core.celery_app import celery_app
from core.config import settings
from services.drawing_spec_text import (assemble_spec_blocks, persist_spec_text,
                                        tokens_from_archive)

logger = logging.getLogger(__name__)

_SELECT_PROJECT_DRAWINGS = """
SELECT DISTINCT drawing_id, project_id
FROM drawing_extracted_info
WHERE project_id = :project_id AND is_active
"""

_SELECT_DRAWING_ROWS = """
SELECT content, category, location_json
FROM drawing_extracted_info
WHERE drawing_id = CAST(:drawing_id AS uuid) AND is_active
"""


def _task_db() -> databases.Database:
    """小池连接——与 `drawing_info_extract` 同一纪律：
    asyncpg 池默认 min_size=10，扇出时会打爆 PG max_connections。"""
    return databases.Database(settings.database_url, min_size=1, max_size=2)


async def rebuild_one(db, project_id: str, drawing_id: str) -> int:
    rows = await db.fetch_all(_SELECT_DRAWING_ROWS, {"drawing_id": drawing_id})
    blocks = assemble_spec_blocks(tokens_from_archive(rows))
    return await persist_spec_text(db, project_id=project_id,
                                   drawing_id=drawing_id, blocks=blocks)


async def _rebuild_project(project_id: str) -> dict:
    db = _task_db()
    await db.connect()
    drawings = blocks = failed = 0
    try:
        rows = await db.fetch_all(_SELECT_PROJECT_DRAWINGS,
                                  {"project_id": project_id})
        for row in rows:
            drawings += 1
            try:
                blocks += await rebuild_one(db, str(row["project_id"]),
                                            str(row["drawing_id"]))
            except Exception as exc:      # 单图失败不拖垮整批
                failed += 1
                logger.warning("说明重建失败 drawing=%s: %s",
                               row["drawing_id"], exc)
    finally:
        await db.disconnect()
    logger.info("说明重建完成 project=%s 图 %d 块 %d 失败 %d",
                project_id, drawings, blocks, failed)
    return {"drawings": drawings, "blocks": blocks, "failed": failed}


@celery_app.task(name="tasks.drawing_spec_text.rebuild_project_spec_text")
def rebuild_project_spec_text(project_id: str) -> dict:
    """整项目重建说明。"""
    return asyncio.run(_rebuild_project(project_id))


@celery_app.task(name="tasks.drawing_spec_text.rebuild_drawing_spec_text")
def rebuild_drawing_spec_text(project_id: str, drawing_id: str) -> dict:
    """单图重建（上传/换版/人审修正后增量触发）。"""
    async def run() -> dict:
        db = _task_db()
        await db.connect()
        try:
            return {"blocks": await rebuild_one(db, project_id, drawing_id)}
        finally:
            await db.disconnect()

    return asyncio.run(run())
