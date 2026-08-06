"""工程 3D 模型基座构建 Celery 任务。

build_project_model(project_id)：
- 置 project_models.status='building'
- services.model_builder.build_scene 组装 scene/assets
- 成功 → status='ready'（version+1、scene/assets/built_at 更新）
- 失败 → status='failed'（error 截断 500），最多重试 2 次

蓝图：docs/MODEL_BASE_BLUEPRINT.md 第 7 节。
"""
import asyncio

from celery.exceptions import SoftTimeLimitExceeded
import json
import logging

import databases

from core.celery_app import celery_app
from core.config import settings
from services.model_builder import build_scene

logger = logging.getLogger(__name__)

ERROR_MAX_LEN = 500


def _resolve_build_mode(scene: dict) -> str | None:
    """物化 project_models.build_mode 汇总列（迁移 017）。

    优先取程序化 IFC 的 ``scene.model_ifc.build_mode``（"ifc"）；缺失时回退
    ``scene.lod.default_mode``（texture/elements/mixed），使该列真实反映渲染模式，
    而非恒为 ifc/NULL。
    """
    model_ifc = scene.get("model_ifc") or {}
    mode = model_ifc.get("build_mode")
    if mode:
        return str(mode)
    lod = scene.get("lod") or {}
    default_mode = lod.get("default_mode")
    return str(default_mode) if default_mode else None


#: 建模的**独立**超时。全局 `task_soft_time_limit=1500`（25 分钟）
#: 是给秒级任务定的，而全项目建模（2309 张图 / 14 层）实测:
#: 楼层定位 1.5 分钟 + **构件识别 23.5 分钟**，刚好被 1500s 杀掉后重试，
#: 重试再超时——白烧 50 分钟仍然失败。
BUILD_SOFT_TIME_LIMIT_SEC = 5400      # 90 分钟
BUILD_HARD_TIME_LIMIT_SEC = 6000      # 留 10 分钟给软超时的清理逻辑


@celery_app.task(
    bind=True, max_retries=2, default_retry_delay=30,
    soft_time_limit=BUILD_SOFT_TIME_LIMIT_SEC,
    time_limit=BUILD_HARD_TIME_LIMIT_SEC,
)
def build_project_model(self, project_id: str) -> dict:
    """模型基座构建任务入口。"""
    logger.info("模型基座构建任务启动: project_id=%s", project_id)
    try:
        return asyncio.run(_do_build(project_id))
    except SoftTimeLimitExceeded as exc:
        # **超时不重试**:同样的输入、同样的耗时，重试必然再超时。
        # 如实失败，让人知道该加时还是该减量——而不是白烧两轮。
        logger.error("模型基座构建超时(%ds): project_id=%s —— 不重试",
                     BUILD_SOFT_TIME_LIMIT_SEC, project_id)
        asyncio.run(_mark_failed(
            project_id, f"构建超时（超过 {BUILD_SOFT_TIME_LIMIT_SEC // 60} 分钟）"))
        raise exc
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
            SET status='building', progress=NULL, updated_at=now()
            WHERE project_id=:project_id
            """,
            {"project_id": project_id},
        )

        async def update_progress(payload: dict) -> None:
            """实时进度落库（migration 014 progress 列）；失败不影响构建。"""
            try:
                await db.execute(
                    """
                    UPDATE project_models
                    SET progress=CAST(:progress AS jsonb), updated_at=now()
                    WHERE project_id=:project_id
                    """,
                    {
                        "project_id": project_id,
                        "progress": json.dumps(payload, ensure_ascii=False),
                    },
                )
            except Exception as exc:  # noqa: BLE001 — 进度写入失败仅告警
                logger.debug("模型进度写入失败: %s", exc)

        scene, assets = await build_scene(db, project_id, progress_cb=update_progress)
        row = await db.fetch_one(
            """
            UPDATE project_models
            SET status='ready', version=version+1,
                scene=CAST(:scene AS jsonb), assets=CAST(:assets AS jsonb),
                build_mode=:build_mode,
                error=NULL, built_at=now(), updated_at=now()
            WHERE project_id=:project_id
            RETURNING version
            """,
            {
                "project_id": project_id,
                "scene": json.dumps(scene, ensure_ascii=False, default=str),
                "assets": json.dumps(assets, ensure_ascii=False, default=str),
                "build_mode": _resolve_build_mode(scene),
            },
        )
        version = row["version"] if row is not None else None
        logger.info("模型基座构建完成: project_id=%s version=%s", project_id, version)

        # ── H4:实体中心装配 —— scene 构件 → ComponentInstance 入库(可追溯) ──
        # try/except 包裹:装配是增强能力,失败绝不能影响建模主流程。
        try:
            from services.component_pipeline import assemble_scene_instances
            from services.component_repository import replace_instances
            if version is not None:
                assembled = assemble_scene_instances(scene)
                written = await replace_instances(
                    db, project_id, version, assembled["instances"])
                logger.info(
                    "H4 装配实体入库: project_id=%s version=%s 实体=%s 跨层缺口=%s",
                    project_id, version, written, len(assembled["continuity_gaps"]))
        except Exception as exc:  # noqa: BLE001 — 装配失败仅告警,不影响建模
            logger.warning("H4 实体装配/入库失败: %s", exc)

        # ── 发射 model.built 管线事件（D-08） ──────────────────────
        # try/except 包裹：事件编排层是自动化增强，发射失败绝不能影响建模主流程。
        try:
            from core.pipeline.handlers import emit_model_built_event
            await emit_model_built_event(db, project_id=project_id, version=version)
        except Exception as exc:  # noqa: BLE001 — 事件发射失败仅告警
            logger.debug("model.built 事件发射失败: %s", exc)

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
