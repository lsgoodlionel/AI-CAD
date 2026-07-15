"""GraphRAG 融合召回层（D-18）。

同一查询走「条文图谱结构召回(KG)」+「向量语义召回(RAG)」双路，合并去重后
交 LLM 多步核查，产出带来源(source)与义务等级(obligation_level)标注的问题列表。

**不改 KG/RAG 引擎本体** —— 只读其公开接口（``BaseEngine.analyze``）来组合，
新增一个可插拔的编排层，全部依赖通过参数注入（kg_analyze/rag_analyze/
rag_retrieve/llm_verify），默认实现懒构造，便于离线单测 mock。

灰度开关（``FusionConfig.enabled``，默认 False）：
    关闭 → ``run_graphrag_fusion`` 恒等回到「KGEngine.analyze + RAGEngine.analyze
    并行拼接」，与 orchestrator 当前对这两个引擎的处理行为字节级一致。
    开启 → 走双路召回（KG 结构召回 + RAG **向量召回**，不复用 RAG 引擎内部
    LangGraph 的 LLM 步骤，避免同一查询链两次 LLM）→ 合并去重 → GraphRAG
    自己的 LLM 多步核查 → 核查失败时优雅降级为「合并候选直出」（mode=
    fusion_degraded，issue 显式标注未经 LLM 核实，供下游/评测过滤）。

融合仲裁哲学沿用 ``core/model3d/fusion`` 同款「强信号不被弱信号覆盖 + 弱信号
补召回 + 冲突仲裁」：KG 结构召回视为强信号来源（不因未与 RAG 重合而被丢弃，
双路都保留在合并候选里参与 LLM 核查，永不静默丢弃 —— 召回保底）；双路命中同
一条文（source=kg+rag）在降级路径按经验规则升一级 severity（共识增强），
真正的合规判定仍交给 LLM 核查步骤定夺（融合层本身不臆断合规结论）。
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import replace
from difflib import SequenceMatcher

from core.ai_review.base import AIIssue, DrawingContext, IssueSeverity
from core.ai_review.kg_engine import KGEngine
from core.ai_review.rag_engine import RAGEngine
from core.llm.router import ModelRouter

from .types import FusionCandidate, FusionConfig, GraphRAGFusionResult, RetrievalCandidate

logger = logging.getLogger(__name__)

DEFAULT_CONFIG = FusionConfig()

_OBLIGATION_LEVELS = {"MUST", "SHOULD", "MAY", "MUST_NOT"}

_SEVERITY_MAP = {
    "critical": IssueSeverity.CRITICAL,
    "major":    IssueSeverity.MAJOR,
    "minor":    IssueSeverity.MINOR,
    "info":     IssueSeverity.INFO,
}

_ESCALATE = {
    IssueSeverity.INFO:     IssueSeverity.MINOR,
    IssueSeverity.MINOR:    IssueSeverity.MAJOR,
    IssueSeverity.MAJOR:    IssueSeverity.CRITICAL,
    IssueSeverity.CRITICAL: IssueSeverity.CRITICAL,
}

KgAnalyzeFn = Callable[[DrawingContext], Awaitable[list[AIIssue]]]
RagAnalyzeFn = Callable[[DrawingContext], Awaitable[list[AIIssue]]]
RagRetrieveFn = Callable[[DrawingContext], Awaitable[list[RetrievalCandidate]]]
LlmVerifyFn = Callable[[DrawingContext, list[FusionCandidate]], Awaitable[list[AIIssue]]]


# ──────────────────────── 主入口 ────────────────────────

async def run_graphrag_fusion(
    ctx: DrawingContext,
    db,
    redis,
    *,
    config: FusionConfig | None = None,
    kg_analyze: KgAnalyzeFn | None = None,
    rag_analyze: RagAnalyzeFn | None = None,
    rag_retrieve: RagRetrieveFn | None = None,
    llm_verify: LlmVerifyFn | None = None,
) -> GraphRAGFusionResult:
    """执行 GraphRAG 融合召回。出错时逐层优雅降级，绝不抛出异常。

    参数注入均可选，缺省时懒构造默认实现（KGEngine/RAGEngine/ModelRouter，
    需要真实 db/redis）；测试可传入 mock 函数，完全绕开真实依赖离线跑通。
    """
    cfg = config or DEFAULT_CONFIG
    _kg_analyze = kg_analyze or _default_kg_analyze(db)
    _rag_analyze = rag_analyze or _default_rag_analyze(db, redis)

    if not cfg.enabled:
        return await _run_identity(ctx, _kg_analyze, _rag_analyze)

    return await _run_fusion(
        ctx, db, redis, cfg,
        kg_analyze=_kg_analyze,
        rag_retrieve=rag_retrieve or _default_vector_retrieval,
        llm_verify=llm_verify,
    )


async def _run_identity(
    ctx: DrawingContext, kg_analyze: KgAnalyzeFn, rag_analyze: RagAnalyzeFn,
) -> GraphRAGFusionResult:
    """灰度关闭：恒等回到现并行行为（KG + RAG 各自 analyze，结果拼接）。"""
    kg_issues, rag_issues = await asyncio.gather(kg_analyze(ctx), rag_analyze(ctx))
    issues = list(kg_issues) + list(rag_issues)
    return GraphRAGFusionResult(
        issues=tuple(issues),
        mode="identity",
        kg_count=len(kg_issues),
        rag_count=len(rag_issues),
        merged_count=len(issues),
        llm_verified_count=0,
        warnings=(),
    )


async def _run_fusion(
    ctx: DrawingContext,
    db,
    redis,
    cfg: FusionConfig,
    *,
    kg_analyze: KgAnalyzeFn,
    rag_retrieve: RagRetrieveFn,
    llm_verify: LlmVerifyFn | None,
) -> GraphRAGFusionResult:
    """灰度开启：双路召回 → 合并去重 → LLM 多步核查（失败则优雅降级）。"""
    kg_issues, rag_candidates = await asyncio.gather(kg_analyze(ctx), rag_retrieve(ctx))
    kg_candidates = [_issue_to_candidate(issue, "kg") for issue in kg_issues]
    merged = _merge_and_dedup([*kg_candidates, *rag_candidates], cfg)

    verify = llm_verify or _default_llm_verify(db, redis, cfg.llm_engine_name)

    warnings: list[str] = []
    try:
        verified_issues = await verify(ctx, merged)
        mode: str = "fusion"
        llm_verified_count = len(verified_issues)
        final_issues = verified_issues
    except Exception as e:  # noqa: BLE001 - 任何 LLM 核查失败都优雅降级，绝不抛出
        logger.warning("[GraphRAG] LLM 多步核查不可用，降级为合并候选直出: %s", e)
        warnings.append("llm_verify_unavailable_fallback_to_merged")
        mode = "fusion_degraded"
        llm_verified_count = 0
        final_issues = [_candidate_to_degraded_issue(c) for c in merged]

    return GraphRAGFusionResult(
        issues=tuple(final_issues),
        mode=mode,  # type: ignore[arg-type]
        kg_count=len(kg_issues),
        rag_count=len(rag_candidates),
        merged_count=len(merged),
        llm_verified_count=llm_verified_count,
        warnings=tuple(warnings),
    )


# ──────────────────────── 默认召回实现（懒构造） ────────────────────────

def _default_kg_analyze(db) -> KgAnalyzeFn:
    engine = KGEngine()

    async def _run(ctx: DrawingContext) -> list[AIIssue]:
        try:
            return await engine.analyze(ctx, db)
        except Exception as e:  # noqa: BLE001 - 与 BaseEngine 契约一致：出错返回空列表
            logger.error("[GraphRAG] KG 引擎异常: %s", e)
            return []

    return _run


def _default_rag_analyze(db, redis) -> RagAnalyzeFn:
    engine = RAGEngine(db, redis)

    async def _run(ctx: DrawingContext) -> list[AIIssue]:
        try:
            return await engine.analyze(ctx, db)
        except Exception as e:  # noqa: BLE001
            logger.error("[GraphRAG] RAG 引擎异常: %s", e)
            return []

    return _run


async def _default_vector_retrieval(ctx: DrawingContext) -> list[RetrievalCandidate]:
    """RAG 路径的**纯向量召回**（不含 LangGraph LLM 步骤，避免与 GraphRAG
    自身的 LLM 核查重复调用）。与 rag_engine._query_chroma 查询同一 Chroma
    collection，但返回结构化候选（regulation_ref + snippet）而非拼接文本，
    以便与 KG 候选做去重合并——这是刻意不复用 _query_chroma 的原因：其返回值
    已拼接成一段文本，逆向切分成结构化候选反而更脆弱。
    """
    try:
        import chromadb
    except ImportError:
        logger.info("[GraphRAG] chromadb 未安装，向量召回跳过")
        return []

    try:
        from core.config import settings

        client = chromadb.HttpClient(host=settings.chroma_host, port=settings.chroma_port)
        collection = client.get_or_create_collection("regulation_articles")
        if collection.count() == 0:
            return []

        query_text = f"{ctx.discipline} {ctx.title} {ctx.drawing_no}"
        results = collection.query(
            query_texts=[query_text],
            n_results=5,
            where={"discipline": {"$in": [ctx.discipline, "common"]}},
        )
        docs = results.get("documents", [[]])[0]
        metas = results.get("metadatas", [[]])[0]

        out: list[RetrievalCandidate] = []
        for doc, meta in zip(docs, metas):
            ref = (meta or {}).get("article_no", "") if isinstance(meta, dict) else ""
            out.append(RetrievalCandidate(
                source="rag",
                regulation_ref=str(ref or ""),
                snippet=(doc or "")[:400],
                severity_hint=IssueSeverity.INFO,
                discipline=ctx.discipline,
            ))
        return out
    except Exception as e:  # noqa: BLE001
        logger.debug("[GraphRAG] 向量召回失败（降级为空）: %s", e)
        return []


# ──────────────────────── 合并去重 ────────────────────────

def _issue_to_candidate(issue: AIIssue, source: str) -> RetrievalCandidate:
    return RetrievalCandidate(
        source=source,  # type: ignore[arg-type]
        regulation_ref=(issue.regulation_ref or "").strip(),
        snippet=issue.description,
        severity_hint=issue.severity,
        discipline=issue.category or "",
    )


def _texts_similar(a: str, b: str, threshold: float) -> bool:
    if not a or not b:
        return False
    return SequenceMatcher(None, a, b).ratio() >= threshold


def _merge_and_dedup(
    candidates: list[RetrievalCandidate], cfg: FusionConfig,
) -> list[FusionCandidate]:
    """贪心合并：regulation_ref 精确相等优先命中，否则按文本相似度阈值合并。

    确定性、纯函数、不改输入。规则候选（kg）永不因合并而丢失信息 —— 合并只
    追加 source，不覆盖已有 snippet/severity_hint（先到先占，与其它融合模块
    "召回保底" 的哲学一致）。
    """
    merged: list[FusionCandidate] = []
    for cand in candidates:
        match_idx: int | None = None
        for i, existing in enumerate(merged):
            same_ref = bool(cand.regulation_ref) and cand.regulation_ref == existing.regulation_ref
            same_text = _texts_similar(cand.snippet, existing.snippet, cfg.dedup_similarity_threshold)
            if same_ref or same_text:
                match_idx = i
                break
        if match_idx is None:
            merged.append(FusionCandidate(
                regulation_ref=cand.regulation_ref,
                snippet=cand.snippet,
                sources=(cand.source,),
                severity_hint=cand.severity_hint,
                discipline=cand.discipline,
            ))
        else:
            existing = merged[match_idx]
            if cand.source not in existing.sources:
                merged[match_idx] = replace(existing, sources=(*existing.sources, cand.source))

    return merged[: cfg.max_merged_candidates]


def _candidate_to_degraded_issue(cand: FusionCandidate) -> AIIssue:
    """LLM 核查不可用时的降级直出：透明标注「未经核实」，供下游/评测过滤。

    双路共识（kg+rag）按共识增强哲学升一级 severity；单路候选保持原 hint。
    """
    severity = _ESCALATE[cand.severity_hint] if cand.is_consensus else cand.severity_hint
    sources_label = "+".join(dict.fromkeys(cand.sources))  # 去重保序
    return AIIssue(
        engine="graphrag",
        severity=severity,
        description=f"[GraphRAG 降级·LLM 未核实·来源:{sources_label}] {cand.snippet}",
        category="GraphRAG融合核查（降级）",
        regulation_ref=cand.regulation_ref,
        suggestion="",
    )


# ──────────────────────── LLM 多步核查（默认实现） ────────────────────────

_VERIFY_SYSTEM = """\
你是建筑图纸合规审查专家。以下候选问题来自「条文图谱结构召回(kg)」与「向量语义\
召回(rag)」双路，已按条文引用/文本相似度合并去重，source 标注召回来源\
（kg / rag / kg+rag，kg+rag 表示双路共识，可信度更高）。请逐条核查是否构成\
真实合规问题，剔除误报，并为每条成立的候选判定：
- severity：critical|major|minor|info
- obligation_level：MUST|SHOULD|MAY|MUST_NOT（依据条文义务强度判断，无法判断填 SHOULD）

仅输出 JSON，格式：
{"issues":[{"index":0,"severity":"major","obligation_level":"MUST","description":"...","suggestion":"..."}]}
未成立的候选请勿输出（从 issues 中剔除该 index）。不要输出 JSON 以外的任何内容。\
"""


def _build_verify_messages(ctx: DrawingContext, candidates: list[FusionCandidate]) -> list[dict]:
    lines = []
    for i, c in enumerate(candidates):
        sources_label = "+".join(dict.fromkeys(c.sources)) or "unknown"
        lines.append(
            f"[{i}] source={sources_label} 条文={c.regulation_ref or '（未知）'} "
            f"专业={c.discipline or ctx.discipline}\n    摘要：{c.snippet[:300]}"
        )
    user = (
        f"图纸：{ctx.drawing_no} {ctx.title}（专业：{ctx.discipline}）\n\n"
        f"候选问题（共 {len(candidates)} 条，来自双路合并去重）：\n" + "\n".join(lines)
    )
    return [
        {"role": "system", "content": _VERIFY_SYSTEM},
        {"role": "user", "content": user},
    ]


def _parse_verify_response(content: str, candidates: list[FusionCandidate]) -> list[AIIssue]:
    """解析 LLM 核查响应。解析失败抛出异常（由调用方统一捕获并降级），
    不在本函数内静默吞掉——区分"LLM 不可用/输出不可解析"与"合法产出空列表"
    没有必要：两者都应触发上层同一条优雅降级路径。
    """
    text = content.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
    data = json.loads(text.strip())
    raw_issues = data.get("issues", [])
    if not isinstance(raw_issues, list):
        raise ValueError("graphrag verify 响应 'issues' 字段非数组")

    out: list[AIIssue] = []
    for item in raw_issues:
        idx = item.get("index")
        cand = candidates[idx] if isinstance(idx, int) and 0 <= idx < len(candidates) else None
        obligation = str(item.get("obligation_level", "")).upper()
        prefix = f"[{obligation}] " if obligation in _OBLIGATION_LEVELS else ""
        description = item.get("description") or (cand.snippet if cand else "")
        out.append(AIIssue(
            engine="graphrag",
            severity=_SEVERITY_MAP.get(str(item.get("severity", "info")).lower(), IssueSeverity.INFO),
            description=f"{prefix}{description}",
            category="GraphRAG融合核查",
            regulation_ref=(cand.regulation_ref if cand else "") or "",
            suggestion=item.get("suggestion", "") or "",
        ))
    return out


def _default_llm_verify(db, redis, engine_name: str) -> LlmVerifyFn:
    async def _run(ctx: DrawingContext, candidates: list[FusionCandidate]) -> list[AIIssue]:
        if not candidates:
            return []
        router = ModelRouter(db, redis)
        messages = _build_verify_messages(ctx, candidates)
        response = await router.route(engine_name, messages)
        return _parse_verify_response(response.content, candidates)

    return _run
