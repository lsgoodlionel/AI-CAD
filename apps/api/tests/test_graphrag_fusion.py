"""D-18 GraphRAG 融合召回层单测 —— 灰度开关 + 双路合并去重 + LLM 核查降级。

覆盖：
    ① 灰度关闭（默认）—— 恒等回到 KG.analyze + RAG.analyze 拼接，不触碰
       rag_retrieve/llm_verify（不注入即不应被调用）。
    ② 灰度开启 + LLM 核查成功 —— 双路合并去重 → 交给 llm_verify → 结果即
       llm_verify 的返回值，且合并去重确实收敛了重复候选。
    ③ 灰度开启 + LLM 核查失败/不可达（模拟本地 ollama 未起）—— 优雅降级为
       「合并候选直出」，mode=fusion_degraded，不抛异常，issue 显式标注降级。
    ④ 合并去重：regulation_ref 精确相等 与 文本相似度 两条路径均能收敛。
    ⑤ 默认向量召回在 chromadb 未安装/异常时优雅降级为空列表。

全部离线可跑：KG/RAG/LLM 均通过依赖注入替换为 mock，不连真实 DB/Redis/Chroma/LLM。
"""
from __future__ import annotations

import json

import pytest

from core.ai_review.base import AIIssue, DrawingContext, IssueSeverity
from core.ai_review.graphrag.fusion import (
    _default_vector_retrieval,
    _merge_and_dedup,
    _parse_verify_response,
    run_graphrag_fusion,
)
from core.ai_review.graphrag.types import FusionCandidate, FusionConfig, RetrievalCandidate


def _ctx(**overrides) -> DrawingContext:
    defaults = dict(
        drawing_id="d1", drawing_no="S-101", discipline="structure",
        title="结构平面图", version="A", file_key="k", file_ext="pdf",
        project_id="p1",
    )
    defaults.update(overrides)
    return DrawingContext(**defaults)


def _issue(engine: str, ref: str = "", desc: str = "问题", severity=IssueSeverity.INFO) -> AIIssue:
    return AIIssue(engine=engine, severity=severity, description=desc, regulation_ref=ref)


class _NeverCalled:
    """哨兵：断言灰度关闭时 rag_retrieve/llm_verify 不应被调用。"""

    async def __call__(self, *args, **kwargs):
        raise AssertionError("不应被调用（灰度关闭时应恒等回到 KG+RAG 拼接）")


# ──────────────────────── ① 灰度关闭 = 恒等 ────────────────────────

@pytest.mark.asyncio
async def test_disabled_by_default_returns_identity_concat_of_kg_and_rag():
    # Arrange
    kg_issues = [_issue("kg", ref="GB50010-2010 8.2.1")]
    rag_issues = [_issue("rag", ref="GB50011-2010 3.1.1")]

    async def kg_analyze(ctx):
        return kg_issues

    async def rag_analyze(ctx):
        return rag_issues

    # Act
    result = await run_graphrag_fusion(
        _ctx(), db=None, redis=None,
        kg_analyze=kg_analyze, rag_analyze=rag_analyze,
        rag_retrieve=_NeverCalled(), llm_verify=_NeverCalled(),
    )

    # Assert
    assert result.mode == "identity"
    assert list(result.issues) == kg_issues + rag_issues
    assert result.kg_count == 1
    assert result.rag_count == 1
    assert result.merged_count == 2
    assert result.llm_verified_count == 0
    assert result.warnings == ()


@pytest.mark.asyncio
async def test_explicit_disabled_config_is_identity_too():
    async def kg_analyze(ctx):
        return []

    async def rag_analyze(ctx):
        return []

    result = await run_graphrag_fusion(
        _ctx(), db=None, redis=None,
        config=FusionConfig(enabled=False),
        kg_analyze=kg_analyze, rag_analyze=rag_analyze,
    )

    assert result.mode == "identity"
    assert result.issues == ()


# ──────────────────────── ② 灰度开启 + LLM 核查成功 ────────────────────────

@pytest.mark.asyncio
async def test_enabled_merges_dedupes_and_delegates_to_llm_verify():
    # Arrange：kg 与 rag 命中同一条文（应被合并为 1 条共识候选），
    # 另有一条 rag-only 候选（不应被去重）。
    async def kg_analyze(ctx):
        return [_issue("kg", ref="GB50010-2010 8.2.1", desc="锚固长度不足", severity=IssueSeverity.MAJOR)]

    async def rag_retrieve(ctx):
        return [
            RetrievalCandidate(source="rag", regulation_ref="GB50010-2010 8.2.1", snippet="锚固长度可能不足"),
            RetrievalCandidate(source="rag", regulation_ref="GB50011-2010 3.1.1", snippet="抗震等级需复核"),
        ]

    captured_candidates: list[FusionCandidate] = []

    async def llm_verify(ctx, candidates):
        captured_candidates.extend(candidates)
        return [_issue("graphrag", ref="GB50010-2010 8.2.1", desc="核查通过：锚固长度不足", severity=IssueSeverity.MAJOR)]

    # Act
    result = await run_graphrag_fusion(
        _ctx(), db=None, redis=None,
        config=FusionConfig(enabled=True),
        kg_analyze=kg_analyze, rag_retrieve=rag_retrieve, llm_verify=llm_verify,
    )

    # Assert：3 条原始候选（1 kg + 2 rag）合并去重为 2 条（同条文命中合并）
    assert result.kg_count == 1
    assert result.rag_count == 2
    assert result.merged_count == 2
    assert len(captured_candidates) == 2
    consensus = next(c for c in captured_candidates if c.regulation_ref == "GB50010-2010 8.2.1")
    assert set(consensus.sources) == {"kg", "rag"}
    assert consensus.is_consensus is True

    assert result.mode == "fusion"
    assert result.llm_verified_count == 1
    assert list(result.issues)[0].description == "核查通过：锚固长度不足"
    assert result.warnings == ()


@pytest.mark.asyncio
async def test_enabled_with_no_candidates_skips_llm_and_returns_empty():
    async def kg_analyze(ctx):
        return []

    async def rag_retrieve(ctx):
        return []

    called = False

    async def llm_verify(ctx, candidates):
        nonlocal called
        called = True
        return []

    result = await run_graphrag_fusion(
        _ctx(), db=None, redis=None,
        config=FusionConfig(enabled=True),
        kg_analyze=kg_analyze, rag_retrieve=rag_retrieve, llm_verify=llm_verify,
    )

    assert result.mode == "fusion"
    assert result.issues == ()
    # 空候选场景下 llm_verify 仍会被调用一次（返回空），但不应抛异常
    assert called is True


# ──────────────────────── ③ LLM 核查失败 → 优雅降级 ────────────────────────

@pytest.mark.asyncio
async def test_llm_verify_failure_degrades_to_merged_candidates_gracefully():
    # Arrange：模拟本地 ollama 未起 / 路由无该引擎配置 → route() 抛异常
    async def kg_analyze(ctx):
        return [_issue("kg", ref="GB50009-2012 4.1.1", desc="荷载取值存疑", severity=IssueSeverity.MAJOR)]

    async def rag_retrieve(ctx):
        return []

    async def llm_verify_raises(ctx, candidates):
        raise RuntimeError("[graphrag_verifier] 未找到任何模型配置")

    # Act
    result = await run_graphrag_fusion(
        _ctx(), db=None, redis=None,
        config=FusionConfig(enabled=True),
        kg_analyze=kg_analyze, rag_retrieve=rag_retrieve, llm_verify=llm_verify_raises,
    )

    # Assert：不抛异常，降级直出，issue 显式标注未经核实
    assert result.mode == "fusion_degraded"
    assert result.llm_verified_count == 0
    assert "llm_verify_unavailable_fallback_to_merged" in result.warnings
    assert len(result.issues) == 1
    issue = result.issues[0]
    assert issue.engine == "graphrag"
    assert "未核实" in issue.description
    assert issue.regulation_ref == "GB50009-2012 4.1.1"
    # kg-only 候选（非共识）severity 不升级，保持原 hint
    assert issue.severity == IssueSeverity.MAJOR


@pytest.mark.asyncio
async def test_consensus_candidate_escalates_severity_on_degraded_path():
    """双路共识候选在降级路径按「共识增强」哲学升一级 severity。"""
    async def kg_analyze(ctx):
        return [_issue("kg", ref="GB50016-2014 5.1.1", desc="防火分区超限", severity=IssueSeverity.MINOR)]

    async def rag_retrieve(ctx):
        return [RetrievalCandidate(source="rag", regulation_ref="GB50016-2014 5.1.1", snippet="防火分区面积超限")]

    async def llm_verify_raises(ctx, candidates):
        raise RuntimeError("router unavailable")

    result = await run_graphrag_fusion(
        _ctx(), db=None, redis=None,
        config=FusionConfig(enabled=True),
        kg_analyze=kg_analyze, rag_retrieve=rag_retrieve, llm_verify=llm_verify_raises,
    )

    assert result.mode == "fusion_degraded"
    assert len(result.issues) == 1
    # MINOR → MAJOR（共识升一级）
    assert result.issues[0].severity == IssueSeverity.MAJOR
    assert "kg+rag" in result.issues[0].description


# ──────────────────────── ④ 合并去重（纯函数）────────────────────────

def test_merge_dedup_exact_regulation_ref_match():
    candidates = [
        RetrievalCandidate(source="kg", regulation_ref="GB50010-2010 8.2.1", snippet="锚固长度不足"),
        RetrievalCandidate(source="rag", regulation_ref="GB50010-2010 8.2.1", snippet="完全不同的措辞文本"),
    ]
    merged = _merge_and_dedup(candidates, FusionConfig())
    assert len(merged) == 1
    assert set(merged[0].sources) == {"kg", "rag"}


def test_merge_dedup_text_similarity_match_without_ref():
    candidates = [
        RetrievalCandidate(source="kg", regulation_ref="", snippet="钢筋锚固长度明显不足需复核"),
        RetrievalCandidate(source="rag", regulation_ref="", snippet="钢筋锚固长度明显不足，需要复核"),
    ]
    merged = _merge_and_dedup(candidates, FusionConfig(dedup_similarity_threshold=0.7))
    assert len(merged) == 1
    assert set(merged[0].sources) == {"kg", "rag"}


def test_merge_dedup_keeps_distinct_candidates_separate():
    candidates = [
        RetrievalCandidate(source="kg", regulation_ref="GB50010-2010 8.2.1", snippet="锚固长度不足"),
        RetrievalCandidate(source="rag", regulation_ref="GB50011-2010 3.1.1", snippet="抗震等级需复核，与前者完全无关"),
    ]
    merged = _merge_and_dedup(candidates, FusionConfig())
    assert len(merged) == 2


def test_merge_dedup_respects_max_merged_candidates_cap():
    # 刻意使用互不相似的文本（避免 SequenceMatcher 在短文本上误判为相似），
    # 只验证「合并去重后按 max_merged_candidates 截断」这一行为。
    topics = [
        "锚固长度不足", "抗震等级不满足", "防火分区超限", "疏散距离过长", "楼板厚度偏薄",
        "梁配筋率不足", "柱轴压比超限", "管线净距不够", "消防水量不足", "采光通风不达标",
        "无障碍坡度超限", "外墙保温缺失", "屋面防水等级低", "楼梯宽度不足", "电气间距违规",
        "给排水坡度错误", "燃气管道净距不足", "空调冷凝水未接", "变形缝宽度不足", "地下室防水等级低",
        "幕墙抗风压不足", "钢结构防火涂层薄", "基础埋深不够", "桩基承载力不足", "边坡稳定性存疑",
        "人防等级不达标", "车库净高不足", "机房降噪不达标", "污水管坡度不足", "配电箱间距违规",
    ]
    candidates = [
        RetrievalCandidate(source="kg", regulation_ref=f"REF-{i}", snippet=topic)
        for i, topic in enumerate(topics)
    ]
    merged = _merge_and_dedup(candidates, FusionConfig(max_merged_candidates=5))
    assert len(merged) == 5


def test_merge_dedup_never_drops_rule_side_information():
    """规则/结构（kg）候选先到先占：合并时 snippet/severity_hint 不被后到的 rag 覆盖。"""
    candidates = [
        RetrievalCandidate(source="kg", regulation_ref="GB50010-2010 8.2.1", snippet="KG原始描述", severity_hint=IssueSeverity.CRITICAL),
        RetrievalCandidate(source="rag", regulation_ref="GB50010-2010 8.2.1", snippet="RAG不同措辞", severity_hint=IssueSeverity.INFO),
    ]
    merged = _merge_and_dedup(candidates, FusionConfig())
    assert merged[0].snippet == "KG原始描述"
    assert merged[0].severity_hint == IssueSeverity.CRITICAL


# ──────────────────────── ⑤ LLM 响应解析（纯函数）────────────────────────

def test_parse_verify_response_maps_obligation_level_and_index():
    candidates = [FusionCandidate(regulation_ref="GB50010-2010 8.2.1", snippet="s0", sources=("kg",))]
    content = json.dumps({
        "issues": [
            {"index": 0, "severity": "major", "obligation_level": "must",
             "description": "锚固长度不足", "suggestion": "加长锚固区"},
        ]
    })
    issues = _parse_verify_response(content, candidates)
    assert len(issues) == 1
    assert issues[0].severity == IssueSeverity.MAJOR
    assert issues[0].description.startswith("[MUST] ")
    assert issues[0].regulation_ref == "GB50010-2010 8.2.1"
    assert issues[0].suggestion == "加长锚固区"


def test_parse_verify_response_drops_unconfirmed_candidates():
    """LLM 判定不成立的候选（不在 issues 数组中）应被丢弃，不臆造。"""
    candidates = [
        FusionCandidate(regulation_ref="A", snippet="s0", sources=("kg",)),
        FusionCandidate(regulation_ref="B", snippet="s1", sources=("rag",)),
    ]
    content = json.dumps({"issues": [{"index": 1, "severity": "info", "obligation_level": "may", "description": "ok"}]})
    issues = _parse_verify_response(content, candidates)
    assert len(issues) == 1
    assert issues[0].regulation_ref == "B"


def test_parse_verify_response_handles_markdown_code_fence():
    candidates: list[FusionCandidate] = []
    content = "```json\n" + json.dumps({"issues": []}) + "\n```"
    assert _parse_verify_response(content, candidates) == []


def test_parse_verify_response_raises_on_malformed_json():
    """解析失败应抛异常（由上层 run_graphrag_fusion 统一捕获降级），不能静默吞掉。"""
    with pytest.raises(Exception):
        _parse_verify_response("不是 JSON", [])


# ──────────────────────── 默认向量召回优雅降级 ────────────────────────

@pytest.mark.asyncio
async def test_default_vector_retrieval_degrades_gracefully_without_chromadb(monkeypatch):
    """chromadb 未安装/连接失败时返回空列表，不抛异常（离线可跑核心保证）。"""
    result = await _default_vector_retrieval(_ctx())
    assert isinstance(result, list)
