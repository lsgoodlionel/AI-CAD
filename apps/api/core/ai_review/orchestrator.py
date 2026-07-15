"""
四引擎协调器：顺序运行视觉引擎（填充 OCR 文本），
再并行运行规则/知识图谱/RAG/会审审查引擎，汇总结果写入数据库。

注意：会审审查引擎（review）的扩展列由 migrations/007、008、009 创建，
写库前请确保已执行该迁移（见下方 INSERT）。

D-18 GraphRAG 融合灰度接入（见 docs/PHASE_D_GRAPHRAG.md）：
    env CAD_GRAPHRAG_FUSION_ENABLED 控制是否用 GraphRAG 融合层
    （core/ai_review/graphrag/fusion.py::run_graphrag_fusion）替换 KG+RAG 并行拼接。
    缺省关闭 —— 此时并行引擎列表 / engine_results 字段 / 写库逻辑与灰度接入前
    逐字节一致（[Rules, KG, RAG, ReviewAudit]，engine_results 含 kg/rag 两个独立
    计数键），可安全合入不改变任何现有行为。开启后见 `_build_parallel_engines`。
"""
import asyncio
import json
import logging
import os
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
from .graphrag.fusion import run_graphrag_fusion
from .graphrag.types import FusionConfig, GraphRAGFusionResult

logger = logging.getLogger(__name__)

VISION_TIMEOUT_SEC = 60
ENGINE_TIMEOUT_SEC = 30

# 灰度开关环境变量名（缺省/未设置/非真值 = 关闭，恒等回退到现状 KG+RAG 并行拼接）
_GRAPHRAG_ENV_VAR = "CAD_GRAPHRAG_FUSION_ENABLED"
_TRUE_VALUES = {"1", "true", "yes", "on"}

_SEVERITY_ORDER = {
    IssueSeverity.CRITICAL: 0,
    IssueSeverity.MAJOR:    1,
    IssueSeverity.MINOR:    2,
    IssueSeverity.INFO:     3,
}


def _graphrag_fusion_enabled() -> bool:
    """D-18 灰度开关：env 未设置或非真值 → False（默认关闭，恒等回退）。"""
    return os.environ.get(_GRAPHRAG_ENV_VAR, "").strip().lower() in _TRUE_VALUES


class _GraphRAGStage:
    """GraphRAG 融合的编排适配层：把 `run_graphrag_fusion` 包装成与其它引擎相同的
    `analyze(ctx, db) -> list[AIIssue]` 接口，供现有并行调度（`_safe_run`）无缝复用，
    不改动 `_safe_run` 本体。仅在灰度开启时被构造（见 `_build_parallel_engines`）。

    `last_result` 在 `analyze` 后保存完整 `GraphRAGFusionResult`（mode/kg_count/
    rag_count 等诊断信息），供写库时组装 `engine_results`（`_build_engine_results`）。
    这是本适配层实例的私有状态（每次审图新建一个 Orchestrator/Stage 实例，不跨请求
    共享），不是对外部共享对象的隐藏变更。
    """

    engine_name = "graphrag"

    def __init__(self, db, redis, config: FusionConfig):
        self._db = db
        self._redis = redis
        self._config = config
        self.last_result: GraphRAGFusionResult | None = None

    async def analyze(self, ctx: DrawingContext, db) -> list[AIIssue]:
        result = await run_graphrag_fusion(ctx, self._db, self._redis, config=self._config)
        self.last_result = result
        return list(result.issues)


def _build_parallel_engines(
    db, redis, graphrag_enabled: bool,
) -> tuple[list, "_GraphRAGStage | None"]:
    """构造第二阶段并行引擎列表。

    灰度关闭（默认）：``[RulesEngine, KGEngine, RAGEngine, ReviewAuditEngine]``，
    与灰度接入前逐字节一致。返回 ``(engines, None)``。

    灰度开启：KG+RAG 替换为单个 `_GraphRAGStage`（内部走 GraphRAG 融合），
    返回 ``(engines, stage)``，`stage.last_result` 供后续写 `engine_results`。
    """
    if not graphrag_enabled:
        return (
            [RulesEngine(), KGEngine(), RAGEngine(db, redis), ReviewAuditEngine(redis)],
            None,
        )
    stage = _GraphRAGStage(db, redis, FusionConfig(enabled=True))
    return [RulesEngine(), stage, ReviewAuditEngine(redis)], stage


def _build_engine_results(
    vision_issues: list[AIIssue],
    parallel_results: list[list[AIIssue]],
    graphrag_stage: "_GraphRAGStage | None",
    ctx: DrawingContext,
) -> dict:
    """组装写入 `ai_review_reports.engine_results` 的汇总字典。

    灰度关闭：字段集合与取值同灰度接入前（vision/rules/kg/rag/review/ocr_metadata）。
    灰度开启：kg/rag 两个独立键替换为 graphrag 计数 + 融合诊断信息
    （graphrag_mode/kg_count/rag_count，源自 `GraphRAGFusionResult`）。
    """
    if graphrag_stage is None:
        return {
            "vision": len(vision_issues),
            "rules":  len(parallel_results[0]),
            "kg":     len(parallel_results[1]),
            "rag":    len(parallel_results[2]),
            "review": len(parallel_results[3]),
            "ocr_metadata": ctx.ocr_metadata,
        }
    stage_result = graphrag_stage.last_result
    return {
        "vision":   len(vision_issues),
        "rules":    len(parallel_results[0]),
        "graphrag": len(parallel_results[1]),
        "graphrag_mode": stage_result.mode if stage_result else None,
        "kg_count":  stage_result.kg_count if stage_result else 0,
        "rag_count": stage_result.rag_count if stage_result else 0,
        "review":   len(parallel_results[2]),
        "ocr_metadata": ctx.ocr_metadata,
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

        # ── 第二步：规则/KG/RAG（或 D-18 灰度开启时的 GraphRAG 融合）/会审引擎并行运行 ──
        graphrag_enabled = _graphrag_fusion_enabled()
        parallel_engines, graphrag_stage = _build_parallel_engines(
            self._db, self._redis, graphrag_enabled,
        )
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
        # V3 扩展列（review_sop(jsonb)，SOP 逐项清单核查）由 migrations/009 创建。
        # V4 扩展列（review_method(jsonb)，方法论控制链/五维审查/处理建议）由 migrations/011 创建。
        # 部署时必须先执行 migrations/007、008、009、011，否则下方 INSERT 因缺列报错。
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
                     question_pack, doc_minutes, doc_reply, review_sop, review_method)
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
                        CAST(:doc_reply AS jsonb), CAST(:review_sop AS jsonb),
                        CAST(:review_method AS jsonb))
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
                    # ── V3 扩展列（需先执行 migrations/009）──
                    "review_sop": json.dumps(issue.review_sop, ensure_ascii=False) if issue.review_sop else None,
                    # ── V4 扩展列（需先执行 migrations/011）──
                    "review_method": json.dumps(issue.review_method, ensure_ascii=False) if issue.review_method else None,
                },
            )

        # ── 更新 ai_review_reports ─────────────────────────────
        engine_results = _build_engine_results(
            vision_issues, parallel_results, graphrag_stage, ctx,
        )
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
