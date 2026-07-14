"""
AI 审图 Celery 异步任务 — Phase 2 四引擎实现。

流程：
  1. 从 DB 读取图纸元数据，构建 DrawingContext
  2. 启动 Orchestrator（Vision → Rules/KG/RAG 并行）
  3. 写入 ai_review_issues + 更新 ai_review_reports
  4. 将图纸状态更新为 ai_done（current_stage → technical_review）
  5. 发射 ai_review.completed 管线事件（D-08，失败不影响本任务主流程）
  6. 失败时回退至 draft，最多重试 3 次
"""
import asyncio
import json
import logging
from datetime import datetime, timezone

import databases
from redis.asyncio import Redis

from core.celery_app import celery_app
from core.config import settings
from core.ai_review import DrawingContext, Orchestrator
from core.pipeline import events as pipeline_events
from services.ai_review_progress import estimate_total_seconds, progress_payload

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, max_retries=3, default_retry_delay=60)
def run_ai_review(self, drawing_id: str) -> dict:
    logger.info("AI 审图任务启动: drawing_id=%s", drawing_id)
    try:
        result = asyncio.run(_do_review(drawing_id))
        return result
    except Exception as exc:
        logger.error("AI 审图任务失败: %s", exc)
        asyncio.run(_mark_failed(drawing_id, str(exc)))
        raise self.retry(exc=exc)


async def _do_review(drawing_id: str) -> dict:
    db = databases.Database(settings.database_url)
    redis = Redis.from_url(settings.redis_url, decode_responses=True)

    await db.connect()
    try:
        # ── 1. 读取图纸元数据 ─────────────────────────────────
        row = await db.fetch_one(
            """
            SELECT d.id, d.drawing_no, d.discipline, d.title, d.version,
                   d.file_key, d.file_size_kb, d.estimated_impact, d.project_id
            FROM drawings d WHERE d.id=:drawing_id
            """,
            {"drawing_id": drawing_id},
        )
        if not row:
            raise ValueError(f"图纸不存在: {drawing_id}")

        file_key = row["file_key"] or ""
        file_ext = file_key.rsplit(".", 1)[-1].lower() if "." in file_key else "pdf"

        ctx = DrawingContext(
            drawing_id=drawing_id,
            drawing_no=row["drawing_no"],
            discipline=row["discipline"],
            title=row["title"] or "",
            version=row["version"],
            file_key=file_key,
            file_ext=file_ext,
            project_id=str(row["project_id"]),
            estimated_impact=float(row["estimated_impact"]) if row["estimated_impact"] else None,
        )

        # ── 2. 获取或创建审查报告记录 ──────────────────────────
        report = await db.fetch_one(
            """
            SELECT id, created_at, engine_results FROM ai_review_reports
            WHERE drawing_id=:drawing_id AND status IN ('pending','processing')
            ORDER BY created_at DESC LIMIT 1
            """,
            {"drawing_id": drawing_id},
        )
        if report is None:
            report = await db.fetch_one(
                """
                INSERT INTO ai_review_reports (drawing_id, status)
                VALUES (:drawing_id,'processing')
                RETURNING id, created_at
                """,
                {"drawing_id": drawing_id},
            )
        else:
            await db.execute(
                "UPDATE ai_review_reports SET status='processing' WHERE id=:report_id",
                {"report_id": report["id"]},
            )
        report_id = str(report["id"])
        report_started_at = report["created_at"] if "created_at" in report else datetime.now(timezone.utc)
        try:
            engine_results = report["engine_results"] if "engine_results" in report else {}
            if isinstance(engine_results, str):
                engine_results = json.loads(engine_results)
            progress_started_at = (engine_results or {}).get("progress", {}).get("started_at")
            if progress_started_at:
                report_started_at = datetime.fromisoformat(progress_started_at.replace("Z", "+00:00"))
        except Exception:
            pass
        recent_avg_ms = await db.fetch_val(
            """
            SELECT AVG(processing_ms)::int
            FROM ai_review_reports
            WHERE status='done' AND processing_ms IS NOT NULL
              AND created_at > now() - interval '30 days'
            """
        )
        estimated_total_seconds = estimate_total_seconds(row["file_size_kb"], recent_avg_ms)

        async def update_progress(payload: dict) -> None:
            progress = progress_payload(
                status=payload.get("status", "processing"),
                stage_key=payload["stage_key"],
                started_at=report_started_at,
                updated_at=datetime.now(timezone.utc),
                completed_keys=payload.get("completed_keys", []),
                active_keys=payload.get("active_keys"),
                estimated_total_seconds=estimated_total_seconds,
                warnings=payload.get("warnings"),
                metrics=payload.get("metrics"),
            )
            await db.execute(
                """
                UPDATE ai_review_reports
                SET engine_results = COALESCE(engine_results, '{}'::jsonb)
                    || jsonb_build_object('progress', CAST(:progress AS jsonb))
                WHERE id=:report_id
                """,
                {
                    "report_id": report_id,
                    "progress": json.dumps(progress, ensure_ascii=False),
                },
            )

        await update_progress({
            "stage_key": "prepare",
            "completed_keys": ["queued"],
            "active_keys": ["prepare"],
        })

        # ── 3. 运行四引擎协调器 ────────────────────────────────
        orchestrator = Orchestrator(db, redis, progress_callback=update_progress)
        summary = await orchestrator.run(ctx, report_id)

        # ── 4. 更新图纸状态 ────────────────────────────────────
        await db.execute(
            """
            UPDATE drawings
            SET status='ai_done', current_stage='technical_review', updated_at=now()
            WHERE id=:drawing_id
            """,
            {"drawing_id": drawing_id},
        )

        logger.info(
            "AI 审图完成: drawing_id=%s total=%d critical=%d ms=%d",
            drawing_id,
            summary["total_issues"],
            summary["critical_issues"],
            summary["processing_ms"],
        )

        # ── 5. 发射 ai_review.completed 管线事件（D-08） ────────
        # try/except 包裹：事件编排层是自动化增强，发射失败绝不能影响审图主流程。
        try:
            await pipeline_events.emit_event(
                db,
                event_type=pipeline_events.EVENT_AI_REVIEW_COMPLETED,
                project_id=str(row["project_id"]),
                source_id=drawing_id,
                payload={
                    "report_id": report_id,
                    "total_issues": summary["total_issues"],
                    "critical_issues": summary["critical_issues"],
                },
            )
        except Exception:
            logger.exception(
                "ai_review.completed 事件发射失败: drawing_id=%s（不影响审图主流程）",
                drawing_id,
            )

        return {"drawing_id": drawing_id, **summary}

    finally:
        await db.disconnect()
        await redis.aclose()


async def _mark_failed(drawing_id: str, error: str) -> None:
    db = databases.Database(settings.database_url)
    await db.connect()
    try:
        await db.execute(
            """
            UPDATE drawings
            SET status='draft', updated_at=now()
            WHERE id=:drawing_id AND status='ai_reviewing'
            """,
            {"drawing_id": drawing_id},
        )
        await db.execute(
            """
            UPDATE ai_review_reports
            SET status='failed',
                engine_results = COALESCE(engine_results, '{}'::jsonb)
                    || jsonb_build_object('error', CAST(:error AS text))
            WHERE drawing_id=:drawing_id AND status='processing'
            """,
            {"drawing_id": drawing_id, "error": error[:500]},
        )
    finally:
        await db.disconnect()
