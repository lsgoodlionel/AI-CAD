"""
规范文件导入 Celery 任务

- import_regulation_file_task: 异步处理单个规范文件（PDF/Word）
  触发时机：用户通过管理后台上传文件后
  完成后：regulation_books.status → 'active'，写入审计日志
"""
import asyncio
import logging

import databases

from core.celery_app import celery_app
from core.config import settings
from core.llm.router import ModelRouter
from dependencies import DatabaseAdapter
from services.audit import write_audit
from services.regulation_importer import import_regulation_file

logger = logging.getLogger(__name__)


@celery_app.task(name="tasks.regulation_import.import_regulation_file_task", bind=True, max_retries=2)
def import_regulation_file_task(self, book_id: str, file_key: str, filename: str) -> dict:
    """
    从 MinIO 取文件 → regulation_importer 流水线 → 更新状态。
    """
    return asyncio.run(_do_import(book_id, file_key, filename))


async def _do_import(book_id: str, file_key: str, filename: str) -> dict:
    raw_db = databases.Database(settings.database_url)
    db = DatabaseAdapter(raw_db)
    await raw_db.connect()

    result: dict = {}
    try:
        # 标记处理中
        await db.execute(
            "UPDATE regulation_books SET status='processing', updated_at=now() WHERE id=$1",
            book_id,
        )

        # 从 MinIO 读取文件
        from core.storage import get_file_bytes
        file_bytes = get_file_bytes(file_key)

        # 构建 ModelRouter（共享 DB 连接）
        from redis.asyncio import Redis
        redis = Redis.from_url(settings.redis_url)
        router = ModelRouter(db=db, redis=redis)

        result = await import_regulation_file(
            db=db,
            router=router,
            book_id=book_id,
            file_bytes=file_bytes,
            filename=filename,
        )

        await redis.aclose()

        # 写入完成状态
        await db.execute(
            "UPDATE regulation_books SET status='active', updated_at=now() WHERE id=$1",
            book_id,
        )
        await write_audit(
            db,
            user_id=None,
            action="regulation_import_complete",
            resource="regulation_book",
            resource_id=book_id,
            new_state=result,
        )
        logger.info("regulation_book %s import done: %s", book_id, result)

    except Exception as exc:
        logger.error("regulation_book %s import failed: %s", book_id, exc)
        await db.execute(
            "UPDATE regulation_books SET status='import_failed', updated_at=now() WHERE id=$1",
            book_id,
        )
        result = {"error": str(exc)}
    finally:
        await raw_db.disconnect()

    return result
