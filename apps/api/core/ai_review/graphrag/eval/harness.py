"""D-18 GraphRAG 合规审查评测 harness：kg_only / rag_only / graphrag 三方法对比
（对齐 docs/PHASE_D_GRAPHRAG.md §3.5/§4，复用 `core/model3d/eval/harness.py` 的
三方法对比编排模式，把 `rule/model/fusion` 换成 `kg_only/rag_only/graphrag`）。

诚实边界（同 Phase C M1 终评式表述）：`graphrag` 一栏的结论数字待真实评测集
（`bootstrap_gold.py` 自举或专家标注）与生产可用的 `graphrag_verifier` LLM 权重
同时就绪后才有意义；harness 本身离线可跑通——运行器均可 DI 注入 mock，绕开真实
db/redis/Chroma/LLM（同 `fusion.py` 的可测试性设计）。
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from core.ai_review.base import AIIssue, DrawingContext
from core.ai_review.graphrag.fusion import run_graphrag_fusion
from core.ai_review.graphrag.types import FusionConfig, GraphRAGFusionResult
from core.ai_review.kg_engine import KGEngine
from core.ai_review.rag_engine import RAGEngine

from .metrics import (
    ComplianceGt,
    ComplianceMetrics,
    DEFAULT_SIMILARITY_THRESHOLD,
    aggregate_compliance_metrics,
    evaluate_compliance,
)

METHODS = ("kg_only", "rag_only", "graphrag")

KgRunner = Callable[[DrawingContext], Awaitable[list[AIIssue]]]
RagRunner = Callable[[DrawingContext], Awaitable[list[AIIssue]]]
GraphRagRunner = Callable[[DrawingContext], Awaitable[GraphRAGFusionResult]]


@dataclass(frozen=True)
class ComplianceEvalSample:
    """单张图纸的评测样本：审图上下文 + 该图纸的金标准合规问题。"""

    ctx: DrawingContext
    gt: tuple[ComplianceGt, ...] = ()
    sample_id: str = ""


@dataclass(frozen=True)
class ComplianceComparisonReport:
    """kg_only / rag_only / graphrag 三方法对比报告（跨样本聚合后的整体指标）。"""

    methods: dict[str, ComplianceMetrics] = field(default_factory=dict)
    sample_count: int = 0
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "sample_count": self.sample_count,
            "methods": {k: v.to_dict() for k, v in self.methods.items()},
            "notes": list(self.notes),
        }


# ──────────────────────── 默认运行器（懒构造，需要真实 db/redis） ────────────────────────

def _default_kg_runner(db) -> KgRunner:
    engine = KGEngine()

    async def _run(ctx: DrawingContext) -> list[AIIssue]:
        return await engine.analyze(ctx, db)

    return _run


def _default_rag_runner(db, redis) -> RagRunner:
    engine = RAGEngine(db, redis)

    async def _run(ctx: DrawingContext) -> list[AIIssue]:
        return await engine.analyze(ctx, db)

    return _run


def _default_graphrag_runner(db, redis, config: FusionConfig) -> GraphRagRunner:
    async def _run(ctx: DrawingContext) -> GraphRAGFusionResult:
        return await run_graphrag_fusion(ctx, db, redis, config=config)

    return _run


def build_default_runners(
    db, redis, *, graphrag_config: FusionConfig | None = None,
) -> tuple[KgRunner, RagRunner, GraphRagRunner]:
    """构造真实 KG/RAG/GraphRAG 运行器（需要真实 db/redis/Chroma/LLM 路由）。

    单测应绕开本函数，直接手写 mock 运行器传给 `run_compliance_comparison`
    （见 `tests/test_graphrag_eval.py`），保持评测 harness 本身离线可跑通。
    """
    cfg = graphrag_config or FusionConfig(enabled=True)
    return (
        _default_kg_runner(db),
        _default_rag_runner(db, redis),
        _default_graphrag_runner(db, redis, cfg),
    )


# ──────────────────────── 三方法对比主入口 ────────────────────────

async def run_compliance_comparison(
    samples: list[ComplianceEvalSample],
    *,
    kg_runner: KgRunner,
    rag_runner: RagRunner,
    graphrag_runner: GraphRagRunner,
    similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
) -> ComplianceComparisonReport:
    """在样本集上跑 kg_only / rag_only / graphrag 三方法对比（doc §3.5）。

    按样本分别调用 `evaluate_compliance` 再用 `aggregate_compliance_metrics`
    聚合（而非展平后统一匹配）——原因见 `metrics.py` 模块 docstring 顶部
    「为何不展平」的说明（`AIIssue` 无 `drawing_id` 字段）。
    """
    per_sample: dict[str, list[ComplianceMetrics]] = {m: [] for m in METHODS}

    for sample in samples:
        gt = list(sample.gt)
        kg_issues = await kg_runner(sample.ctx)
        rag_issues = await rag_runner(sample.ctx)
        graphrag_result = await graphrag_runner(sample.ctx)

        per_sample["kg_only"].append(
            evaluate_compliance(gt, kg_issues, similarity_threshold=similarity_threshold)
        )
        per_sample["rag_only"].append(
            evaluate_compliance(gt, rag_issues, similarity_threshold=similarity_threshold)
        )
        per_sample["graphrag"].append(
            evaluate_compliance(
                gt, list(graphrag_result.issues), similarity_threshold=similarity_threshold,
            )
        )

    methods = {m: aggregate_compliance_metrics(per_sample[m]) for m in METHODS}
    notes = (
        "graphrag 端结论待真实评测集（bootstrap_gold.py 自举或专家标注）与真实 "
        "graphrag_verifier LLM 权重同时就绪后复评；冷启动阶段的数字仅供 harness "
        "链路验证，不代表生产精度（同 Phase C M1 终评「基座就绪、数字待权重」的"
        "诚实边界表述）。",
        "结构性预期（doc §3.5，需评测验证而非假设）：graphrag.recall >= "
        "max(kg_only.recall, rag_only.recall)——双路召回合并去重不丢候选，LLM "
        "核查只做筛选不做新增召回；若未达成，是 LLM 核查步骤过度剔除的明确信号，"
        "应对照三方法 fn_breakdown.retrieved_but_llm_dropped 排查（本 harness 未"
        "接入 retrieval_universe，该字段当前恒为 0，见 metrics.py 对该限制的说明）。",
    )
    return ComplianceComparisonReport(
        methods=methods, sample_count=len(samples), notes=notes,
    )
