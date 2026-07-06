"""工程 3D 模型基座构建 Celery 任务。

build_project_model(project_id)：
- 置 project_models.status='building'
- services.model_builder.build_scene 组装 scene/assets
- 成功 → status='ready'（version+1、scene/assets/built_at 更新）
- 失败 → status='failed'（error 截断 500），最多重试 2 次

蓝图：docs/MODEL_BASE_BLUEPRINT.md 第 7 节。
"""
import asyncio
import json
import logging

import databases

from core.celery_app import celery_app
from core.config import settings
from services.model_builder import build_scene

logger = logging.getLogger(__name__)

ERROR_MAX_LEN = 500


@celery_app.task(bind=True, max_retries=2, default_retry_delay=30)
def build_project_model(self, project_id: str) -> dict:
    """模型基座构建任务入口。"""
    logger.info("模型基座构建任务启动: project_id=%s", project_id)
    try:
        return asyncio.run(_do_build(project_id))
    except Exception as exc:
        logger.error("模型基座构建失败: project_id=%s error=%s", project_id, exc)
        asyncio.run(_mark_failed(project_id, str(exc)))
        raise self.retry(exc=exc)


async def _do_build(project_id: str) -> dict:
    """建立 DB 连接并执行构建（连接模式与 tasks/ai_review._do_review 一致）。"""
    db = databases.Database(settings.database_url)
    await db.connect()
    try:
        await db.execute(
            """
            UPDATE project_models
            SET status='building', updated_at=now()
            WHERE project_id=:project_id
            """,
            {"project_id": project_id},
        )
        scene, assets = await build_scene(db, project_id)
        row = await db.fetch_one(
            """
            UPDATE project_models
            SET status='ready', version=version+1,
                scene=CAST(:scene AS jsonb), assets=CAST(:assets AS jsonb),
                error=NULL, built_at=now(), updated_at=now()
            WHERE project_id=:project_id
            RETURNING version
            """,
            {
                "project_id": project_id,
                "scene": json.dumps(scene, ensure_ascii=False, default=str),
                "assets": json.dumps(assets, ensure_ascii=False, default=str),
            },
        )
        version = row["version"] if row is not None else None
        logger.info("模型基座构建完成: project_id=%s version=%s", project_id, version)
        return {"project_id": project_id, "status": "ready", "version": version}
    finally:
        await db.disconnect()


async def _mark_failed(project_id: str, error: str) -> None:
    """失败落库：status='failed'，error 截断 500 字符。"""
    db = databases.Database(settings.database_url)
    await db.connect()
    try:
        await db.execute(
            """
            UPDATE project_models
            SET status='failed', error=:error, updated_at=now()
            WHERE project_id=:project_id
            """,
            {"project_id": project_id, "error": error[:ERROR_MAX_LEN]},
        )
    finally:
        await db.disconnect()
