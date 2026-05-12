"""
四引擎协调器：顺序运行视觉引擎（填充 OCR 文本），
再并行运行规则/知识图谱/RAG 引擎，汇总结果写入数据库。
"""
import asyncio
import logging
import time
from uuid import UUID

from redis.asyncio import Redis

from .base import DrawingContext, AIIssue, IssueSeverity
from .rules_engine import RulesEngine
from .kg_engine import KGEngine
from .rag_engine import RAGEngine
from .vision_engine import VisionEngine

logger = logging.getLogger(__name__)

VISION_TIMEOUT_SEC = 60
ENGINE_TIMEOUT_SEC = 30

_SEVERITY_ORDER = {
    IssueSeverity.CRITICAL: 0,
    IssueSeverity.MAJOR:    1,
    IssueSeverity.MINOR:    2,
    IssueSeverity.INFO:     3,
}


class Orchestrator:
    def __init__(self, db, redis: Redis | None = None):
        self._db = db
        self._redis = redis

    async def run(self, ctx: DrawingContext, report_id: str) -> dict:
        """
        执行四引擎审查，将结果写入 ai_review_issues 表，
        更新 ai_review_reports 汇总字段。
        返回摘要 dict。
        """
        t0 = time.monotonic()

        # ── 第一步：视觉引擎（填充 OCR 文本）─────────────────
        vision_engine = VisionEngine()
        try:
            vision_issues = await asyncio.wait_for(
                vision_engine.analyze(ctx, self._db),
                timeout=VISION_TIMEOUT_SEC,
            )
        except asyncio.TimeoutError:
            logger.warning("[Orchestrator] 视觉引擎超时（%ds）", VISION_TIMEOUT_SEC)
            vision_issues = [AIIssue(
                engine="ocr", severity=IssueSeverity.MAJOR,
                description=f"视觉引擎处理超时（>{VISION_TIMEOUT_SEC}s），文件可能过大或格式异常",
                category="引擎超时",
            )]
        except Exception as e:
            logger.error("[Orchestrator] 视觉引擎异常: %s", e)
            vision_issues = []

        # ── 第二步：规则/KG/RAG 引擎并行运行 ─────────────────
        parallel_engines = [
            RulesEngine(),
            KGEngine(),
            RAGEngine(self._db, self._redis),
        ]

        async def _safe_run(engine) -> list[AIIssue]:
            try:
                return await asyncio.wait_for(
                    engine.analyze(ctx, self._db),
                    timeout=ENGINE_TIMEOUT_SEC,
                )
            except asyncio.TimeoutError:
                logger.warning("[Orchestrator] %s 超时", engine.engine_name)
                return [AIIssue(
                    engine=engine.engine_name,
                    severity=IssueSeverity.INFO,
                    description=f"{engine.engine_name} 引擎超时（>{ENGINE_TIMEOUT_SEC}s），部分分析未完成",
                    category="引擎超时",
                )]
            except Exception as e:
                logger.error("[Orchestrator] %s 异常: %s", engine.engine_name, e)
                return []

        parallel_results = await asyncio.gather(*[_safe_run(e) for e in parallel_engines])

        # ── 汇总所有问题 ───────────────────────────────────────
        all_issues: list[AIIssue] = list(vision_issues)
        for issues in parallel_results:
            all_issues.extend(issues)

        all_issues.sort(key=lambda i: _SEVERITY_ORDER.get(i.severity, 9))

        total = len(all_issues)
        critical = sum(1 for i in all_issues if i.severity == IssueSeverity.CRITICAL)
        processing_ms = int((time.monotonic() - t0) * 1000)

        # ── 写入 ai_review_issues ──────────────────────────────
        for issue in all_issues:
            await self._db.execute(
                """
                INSERT INTO ai_review_issues
                    (report_id, engine, severity, category, description,
                     regulation_ref, suggestion, location_x, location_y, status)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,'open')
                """,
                report_id,
                issue.engine,
                issue.severity.value,
                issue.category or None,
                issue.description,
                issue.regulation_ref or None,
                issue.suggestion or None,
                issue.location_x,
                issue.location_y,
            )

        # ── 更新 ai_review_reports ─────────────────────────────
        engine_results = {
            "vision":  len(vision_issues),
            "rules":   len(list(parallel_results[0])),
            "kg":      len(list(parallel_results[1])),
            "rag":     len(list(parallel_results[2])),
            "ocr_metadata": ctx.ocr_metadata,
        }
        await self._db.execute(
            """
            UPDATE ai_review_reports
            SET status='done', total_issues=$2, critical_issues=$3,
                processing_ms=$4, engine_results=$5::jsonb, completed_at=now()
            WHERE id=$1
            """,
            report_id, total, critical, processing_ms,
            __import__("json").dumps(engine_results),
        )

        logger.info(
            "[Orchestrator] 图纸 %s 审查完成：total=%d critical=%d 耗时=%dms",
            ctx.drawing_no, total, critical, processing_ms,
        )
        return {
            "total_issues": total,
            "critical_issues": critical,
            "processing_ms": processing_ms,
        }
