"""工程信息抽取 Celery 任务(Phase E1-1)。

extract_project_drawing_info(project_id)：
- 遍历项目全部图纸,逐图:MinIO 拉字节 → drawing_info_extractor 抽取 → 覆盖式落库
- 单图失败记日志跳过,不拖垮批量(与建模管线纪律一致)
- 代次:全项目重抽时统一 extraction_version = 上一代 max + 1

extract_single_drawing_info(drawing_id)：单图重抽(上传/换版后增量触发)。
"""
import asyncio
import logging
import os

import databases

from core.celery_app import celery_app
from core.config import settings

logger = logging.getLogger(__name__)

_SELECT_PROJECT_DRAWINGS = """
SELECT id, project_id, file_key, title
FROM drawings
WHERE project_id = :project_id AND file_key IS NOT NULL
ORDER BY created_at
"""

_SELECT_ONE_DRAWING = """
SELECT id, project_id, file_key, title
FROM drawings
WHERE id = :drawing_id AND file_key IS NOT NULL
"""

_SELECT_NEXT_VERSION = """
SELECT COALESCE(MAX(extraction_version), 0) + 1 AS v
FROM drawing_extracted_info
WHERE project_id = :project_id
"""


def _file_ext(file_key: str, title: str | None) -> str:
    ext = os.path.splitext(file_key)[1].lstrip(".").lower()
    if ext:
        return ext
    return os.path.splitext(title or "")[1].lstrip(".").lower()


async def _extract_one(db, row: dict, version: int) -> int:
    """抽取并落库单图,返回写入条数;失败抛出由调用方决定吞或抛。"""
    from core.storage import get_file_bytes
    from services.drawing_info_extractor import (
        extract_drawing_info,
        persist_drawing_info,
    )

    file_bytes = get_file_bytes(row["file_key"])
    ext = _file_ext(row["file_key"], row.get("title"))
    items = extract_drawing_info(file_bytes, ext, filename=row.get("title"))
    return await persist_drawing_info(
        db,
        project_id=str(row["project_id"]),
        drawing_id=str(row["id"]),
        items=items,
        version=version,
    )


@celery_app.task(bind=True, max_retries=1, default_retry_delay=60)
def extract_project_drawing_info(self, project_id: str) -> dict:
    """全项目工程信息抽取入口(扇出模式)。

    大项目(数千图 × OCR 数十秒/图)串行必撞 celery 1800s 硬超时,
    故本任务只做「查清单 + 定版本 + 逐图派发单图任务」,秒级完成;
    真正抽取由 extract_single_drawing_info 逐图独立执行(可重试、互不拖累)。
    """
    logger.info("[drawing_info] 项目级抽取启动(扇出): project_id=%s", project_id)
    try:
        return asyncio.run(_fanout_project(project_id))
    except Exception as exc:
        logger.error("[drawing_info] 项目级扇出失败: %s", exc)
        raise self.retry(exc=exc)


async def _fanout_project(project_id: str) -> dict:
    db = databases.Database(settings.database_url)
    await db.connect()
    try:
        rows = [dict(r) for r in await db.fetch_all(
            _SELECT_PROJECT_DRAWINGS, {"project_id": project_id}
        )]
        version_row = await db.fetch_one(_SELECT_NEXT_VERSION, {"project_id": project_id})
        version = int(version_row["v"]) if version_row else 1
    finally:
        await db.disconnect()

    for row in rows:
        extract_single_drawing_info.delay(str(row["id"]), version)

    result = {"project_id": project_id, "enqueued": len(rows), "version": version}
    logger.info("[drawing_info] 项目级扇出完成: %s", result)
    return result


@celery_app.task(bind=True, max_retries=1, default_retry_delay=30)
def extract_single_drawing_info(self, drawing_id: str, version: int | None = None) -> dict:
    """单图工程信息抽取/重抽入口。

    version 由项目级扇出统一下发保证同代次;单图独立触发时为 None,
    按项目当前 max+1 自算。
    """
    try:
        return asyncio.run(_do_extract_single(drawing_id, version))
    except Exception as exc:
        logger.error("[drawing_info] 单图抽取失败: drawing_id=%s %s", drawing_id, exc)
        raise self.retry(exc=exc)


async def _do_extract_single(drawing_id: str, version: int | None = None) -> dict:
    db = databases.Database(settings.database_url)
    await db.connect()
    try:
        row = await db.fetch_one(_SELECT_ONE_DRAWING, {"drawing_id": drawing_id})
        if row is None:
            return {"drawing_id": drawing_id, "items": 0, "skipped": "not_found"}
        row = dict(row)
        if version is None:
            version_row = await db.fetch_one(
                _SELECT_NEXT_VERSION, {"project_id": str(row["project_id"])}
            )
            version = int(version_row["v"]) if version_row else 1
        items = await _extract_one(db, row, version)
        return {"drawing_id": drawing_id, "items": items, "version": version}
    finally:
        await db.disconnect()
