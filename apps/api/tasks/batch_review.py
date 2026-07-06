"""套图审查汇总 Celery 任务（轮询型）。

finalize_batch_review 周期性检查批次内所有图纸的最新审图报告：
- 存在非终态（pending/processing/无报告）→ self.retry（10s 间隔，上限 ~30min）
- 全部终态 → 聚合 summary + cross_drawing.analyze_batch → 更新 review_batches
  status：全部 done→'done'；部分 failed→'partial_failed'；全部 failed→'failed'

蓝图：docs/BATCH_REVIEW_BLUEPRINT.md 第 4.5 节。
"""
import asyncio
import json
import logging

import databases

from core.celery_app import celery_app
from core.config import settings
from core.ai_review.cross_drawing import analyze_batch

logger = logging.getLogger(__name__)

TERMINAL_STATUSES = ("done", "failed")

_LATEST_REPORTS_SQL = """
SELECT DISTINCT ON (drawing_id) drawing_id, status
FROM ai_review_reports
WHERE drawing_id::text = ANY(:drawing_ids)
ORDER BY drawing_id, created_at DESC
"""


class BatchNotReady(Exception):
    """批次内仍有未终态的审图报告，需要稍后重试。"""


@celery_app.task(bind=True, max_retries=180, default_retry_delay=10)
def finalize_batch_review(self, batch_id: str) -> dict:
    """轮询型汇总任务入口。"""
    logger.info("套图审查汇总任务启动: batch_id=%s", batch_id)
    try:
        return asyncio.run(_do_finalize(batch_id))
    except BatchNotReady as exc:
        logger.info("批次 %s 未就绪，稍后重试: %s", batch_id, exc)
        raise self.retry(exc=exc)


async def _do_finalize(batch_id: str) -> dict:
    """建立 DB 连接并执行汇总（连接模式与 tasks/ai_review._do_review 一致）。"""
    db = databases.Database(settings.database_url)
    await db.connect()
    try:
        result = await _finalize_batch(db, batch_id)
        await _maybe_trigger_model_build(db, batch_id)
        return result
    finally:
        await db.disconnect()


async def _maybe_trigger_model_build(db, batch_id: str) -> None:
    """汇总完成后：项目已生成过模型基座 → 触发自动重建（失败仅告警不影响批次）。"""
    try:
        row = await db.fetch_one(
            """
            SELECT pm.project_id FROM project_models pm
            JOIN review_batches b ON b.project_id = pm.project_id
            WHERE b.id=:batch_id
            """,
            {"batch_id": batch_id},
        )
        if row is None:
            return
        from tasks.model_build import build_project_model  # 局部导入防循环依赖
        build_project_model.delay(str(row["project_id"]))
    except Exception as exc:  # noqa: BLE001 — 自动触发失败不得影响批次状态
        logger.warning("模型基座自动重建触发失败（忽略）: batch_id=%s error=%s", batch_id, exc)


def _safe_ids(value) -> list[str]:
    """drawing_ids JSONB 经驱动可能返回 str，安全解析为字符串列表。"""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return []
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _batch_status(done: int, failed: int, total: int) -> str:
    """终态判定：全部 done→done；全部 failed→failed；否则 partial_failed。"""
    if failed == 0:
        return "done"
    if failed == total:
        return "failed"
    return "partial_failed"


def _build_summary(total: int, done: int, failed: int, cross: dict) -> dict:
    """聚合摘要：数量统计复用跨图分析的严重度/专业分布。"""
    severity = cross.get("严重度分布", {})
    return {
        "total": total,
        "done": done,
        "failed": failed,
        "issues_total": sum(severity.values()),
        "critical_total": severity.get("critical", 0),
        "by_severity": severity,
        "by_discipline": cross.get("专业分布", {}),
    }


async def _finalize_batch(db, batch_id: str) -> dict:
    """核心汇总：报告终态检查 → 跨图分析 → 更新 review_batches。"""
    batch = await db.fetch_one(
        "SELECT id, project_id, drawing_ids FROM review_batches WHERE id=:batch_id",
        {"batch_id": batch_id},
    )
    if batch is None:
        raise ValueError(f"批次不存在: {batch_id}")

    drawing_ids = _safe_ids(batch["drawing_ids"])
    reports = await db.fetch_all(_LATEST_REPORTS_SQL, {"drawing_ids": drawing_ids})
    statuses = {str(row["drawing_id"]): row["status"] for row in reports}

    unfinished = [
        did for did in drawing_ids
        if statuses.get(did) not in TERMINAL_STATUSES
    ]
    if unfinished:
        raise BatchNotReady(f"未完成图纸 {len(unfinished)}/{len(drawing_ids)}")

    done = sum(1 for did in drawing_ids if statuses.get(did) == "done")
    failed = len(drawing_ids) - done
    cross = await analyze_batch(db, str(batch["project_id"]), drawing_ids)
    summary = _build_summary(len(drawing_ids), done, failed, cross)
    status = _batch_status(done, failed, len(drawing_ids))

    await db.execute(
        """
        UPDATE review_batches
        SET status=:status, summary=CAST(:summary AS jsonb),
            cross_findings=CAST(:cross AS jsonb), completed_at=now()
        WHERE id=:batch_id
        """,
        {
            "batch_id": batch_id,
            "status": status,
            "summary": json.dumps(summary, ensure_ascii=False),
            "cross": json.dumps(cross, ensure_ascii=False),
        },
    )
    logger.info("批次 %s 汇总完成: status=%s", batch_id, status)
    return {"batch_id": batch_id, "status": status, "summary": summary}
