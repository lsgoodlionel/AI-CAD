"""
四引擎协调器：顺序运行视觉引擎（填充 OCR 文本），
再并行运行规则/知识图谱/RAG/会审审查引擎，汇总结果写入数据库。

注意：会审审查引擎（review）的扩展列由 migrations/007 与 008 创建，
写库前请确保已执行该迁移（见下方 INSERT）。
"""
import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from uuid import UUID

from redis.asyncio import Redis

from .base import DrawingContext, AIIssue, IssueSeverity
from .rules_engine import RulesEngine
from .kg_engine import KGEngine
from .rag_engine import RAGEngine
from .vision_engine import VisionEngine
from .review_audit.engine import ReviewAuditEngine

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
    def __init__(
        self,
        db,
        redis: Redis | None = None,
        progress_callback: Callable[[dict], Awaitable[None]] | None = None,
    ):
        self._db = db
        self._redis = redis
        self._progress_callback = progress_callback

    async def _emit_progress(self, payload: dict) -> None:
        if self._progress_callback is not None:
            await self._progress_callback(payload)

    async def run(self, ctx: DrawingContext, report_id: str) -> dict:
        """
        执行四引擎审查，将结果写入 ai_review_issues 表，
        更新 ai_review_reports 汇总字段。
        返回摘要 dict。
        """
        t0 = time.monotonic()
        completed_steps = ["queued", "prepare"]

        # ── 第一步：视觉引擎（填充 OCR 文本）─────────────────
        vision_engine = VisionEngine()
        await self._emit_progress({
            "stage_key": "vision",
            "completed_keys": completed_steps,
            "active_keys": ["vision"],
        })
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
        completed_steps.append("vision")
        await self._emit_progress({
            "stage_key": "vision",
            "completed_keys": completed_steps,
            "active_keys": [],
            "metrics": {"vision_issues": len(vision_issues)},
        })

        # ── 第二步：规则/KG/RAG 引擎并行运行 ─────────────────
        parallel_engines = [
            RulesEngine(),
            KGEngine(),
            RAGEngine(self._db, self._redis),
            ReviewAuditEngine(),
        ]
        active_parallel = [engine.engine_name for engine in parallel_engines]
        await self._emit_progress({
            "stage_key": "rules",
            "completed_keys": completed_steps,
            "active_keys": active_parallel,
        })

        async def _safe_run(engine) -> list[AIIssue]:
            try:
                issues = await asyncio.wait_for(
                    engine.analyze(ctx, self._db),
                    timeout=ENGINE_TIMEOUT_SEC,
                )
                return issues
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
            finally:
                if engine.engine_name in active_parallel:
                    active_parallel.remove(engine.engine_name)
                if engine.engine_name not in completed_steps:
                    completed_steps.append(engine.engine_name)
                await self._emit_progress({
                    "stage_key": engine.engine_name,
                    "completed_keys": list(completed_steps),
                    "active_keys": list(active_parallel),
                })

        parallel_results = await asyncio.gather(*[_safe_run(e) for e in parallel_engines])
        await self._emit_progress({
            "stage_key": "summary",
            "completed_keys": completed_steps,
            "active_keys": ["summary"],
        })

        # ── 汇总所有问题 ───────────────────────────────────────
        all_issues: list[AIIssue] = list(vision_issues)
        for issues in parallel_results:
            all_issues.extend(issues)

        all_issues.sort(key=lambda i: _SEVERITY_ORDER.get(i.severity, 9))

        total = len(all_issues)
        critical = sum(1 for i in all_issues if i.severity == IssueSeverity.CRITICAL)
        processing_ms = int((time.monotonic() - t0) * 1000)

        # ── 写入 ai_review_issues ──────────────────────────────
        # 会审审查引擎（review）扩展列由 migrations/007 创建：
        #   discipline_code, location_json(jsonb), concerns(jsonb), issue_class(jsonb),
        #   interface_primary, interface_related(jsonb), risk_level, object_level,
        #   standard_question, evidence_gap(jsonb)
        # V2 扩展列（object_name, object_basis, scenario, scenario_reason,
        #   question_pack(jsonb), doc_minutes(jsonb), doc_reply(jsonb)）由 migrations/008 创建。
        # 部署时必须先执行 migrations/007 与 migrations/008，否则下方 INSERT 因缺列报错。
        for issue in all_issues:
            await self._db.execute(
                """
                INSERT INTO ai_review_issues
                    (report_id, engine, severity, category, description,
                     regulation_ref, suggestion, location_x, location_y, status,
                     discipline_code, location_json, concerns, issue_class,
                     interface_primary, interface_related, risk_level, object_level,
                     standard_question, evidence_gap,
                     object_name, object_basis, scenario, scenario_reason,
                     question_pack, doc_minutes, doc_reply)
                VALUES (:report_id,:engine,:severity,:category,:description,
                        :regulation_ref,:suggestion,:location_x,:location_y,'open',
                        :discipline_code,
                        CAST(:location_json AS jsonb), CAST(:concerns AS jsonb),
                        CAST(:issue_class AS jsonb),
                        :interface_primary, CAST(:interface_related AS jsonb),
                        :risk_level, :object_level,
                        :standard_question, CAST(:evidence_gap AS jsonb),
                        :object_name, :object_basis, :scenario, :scenario_reason,
                        CAST(:question_pack AS jsonb), CAST(:doc_minutes AS jsonb),
                        CAST(:doc_reply AS jsonb))
                """,
                {
                    "report_id": report_id,
                    "engine": issue.engine,
                    "severity": issue.severity.value,
                    "category": issue.category or None,
                    "description": issue.description,
                    "regulation_ref": issue.regulation_ref or None,
                    "suggestion": issue.suggestion or None,
                    "location_x": issue.location_x,
                    "location_y": issue.location_y,
                    "discipline_code": issue.discipline_code or None,
                    "location_json": json.dumps(issue.location, ensure_ascii=False) if issue.location else None,
                    "concerns": json.dumps(issue.concerns, ensure_ascii=False) if issue.concerns else None,
                    "issue_class": json.dumps(issue.issue_class, ensure_ascii=False) if issue.issue_class else None,
                    "interface_primary": issue.interface_primary or None,
                    "interface_related": json.dumps(issue.interface_related, ensure_ascii=False) if issue.interface_related else None,
                    "risk_level": issue.risk_level or None,
                    "object_level": issue.object_level or None,
                    "standard_question": issue.standard_question or None,
                    "evidence_gap": json.dumps(issue.evidence_gap, ensure_ascii=False) if issue.evidence_gap else None,
                    # ── V2 扩展列（需先执行 migrations/008）──
                    "object_name": issue.object_name or None,
                    "object_basis": issue.object_basis or None,
                    "scenario": issue.scenario or None,
                    "scenario_reason": issue.scenario_reason or None,
                    "question_pack": json.dumps(issue.question_pack, ensure_ascii=False) if issue.question_pack else None,
                    "doc_minutes": json.dumps(issue.doc_minutes, ensure_ascii=False) if issue.doc_minutes else None,
                    "doc_reply": json.dumps(issue.doc_reply, ensure_ascii=False) if issue.doc_reply else None,
                },
            )

        # ── 更新 ai_review_reports ─────────────────────────────
        engine_results = {
            "vision":  len(vision_issues),
            "rules":   len(list(parallel_results[0])),
            "kg":      len(list(parallel_results[1])),
            "rag":     len(list(parallel_results[2])),
            "review":  len(list(parallel_results[3])),
            "ocr_metadata": ctx.ocr_metadata,
        }
        await self._db.execute(
            """
            UPDATE ai_review_reports
            SET status='done', total_issues=:total_issues, critical_issues=:critical_issues,
                processing_ms=:processing_ms, engine_results=CAST(:engine_results AS jsonb),
                completed_at=now()
            WHERE id=:report_id
            """,
            {
                "report_id": report_id,
                "total_issues": total,
                "critical_issues": critical,
                "processing_ms": processing_ms,
                "engine_results": json.dumps(engine_results),
            },
        )
        await self._emit_progress({
            "stage_key": "summary",
            "completed_keys": [stage for stage in completed_steps + ["summary"]],
            "active_keys": [],
            "status": "done",
            "metrics": {
                "total_issues": total,
                "critical_issues": critical,
                "processing_ms": processing_ms,
            },
        })

        logger.info(
            "[Orchestrator] 图纸 %s 审查完成：total=%d critical=%d 耗时=%dms",
            ctx.drawing_no, total, critical, processing_ms,
        )
        return {
            "total_issues": total,
            "critical_issues": critical,
            "processing_ms": processing_ms,
        }
