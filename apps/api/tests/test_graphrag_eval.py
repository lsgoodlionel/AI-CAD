"""D-18 GraphRAG 合规审查评测 harness 单测（`core/ai_review/graphrag/eval/`）。

覆盖：
    ① `normalize_regulation_ref` / `extract_obligation_level`：归一化 + 前缀提取。
    ② `evaluate_compliance`：TP/FP/FN 判定（含 severity 容差与 wrong_severity）、
       条文引用命中率、义务等级混淆矩阵、义务降级率、FP 细分（spurious/wrong_ref/
       wrong_severity）、FN 细分（有/无 retrieval_universe 两种情形）。
    ③ `aggregate_compliance_metrics`：跨样本求和聚合，比率从聚合后的原始计数
       重新推导（而非对样本比率取平均）。
    ④ `run_compliance_comparison`：kg_only/rag_only/graphrag 三方法对比编排，
       DI 注入 mock 运行器，离线可跑，验证聚合结果的 sample_count/notes。

全部离线：不连真实 DB/Redis/Chroma/LLM。
"""
from __future__ import annotations

import pytest

from core.ai_review.base import AIIssue, DrawingContext, IssueSeverity
from core.ai_review.graphrag.eval.harness import (
    ComplianceEvalSample,
    run_compliance_comparison,
)
from core.ai_review.graphrag.eval.metrics import (
    ComplianceGt,
    aggregate_compliance_metrics,
    evaluate_compliance,
    extract_obligation_level,
    normalize_regulation_ref,
)
from core.ai_review.graphrag.types import GraphRAGFusionResult


def _ctx(**overrides) -> DrawingContext:
    defaults = dict(
        drawing_id="d1", drawing_no="S-101", discipline="structure",
        title="结构平面图", version="A", file_key="k", file_ext="pdf",
        project_id="p1",
    )
    defaults.update(overrides)
    return DrawingContext(**defaults)


def _issue(ref: str = "", desc: str = "问题", severity=IssueSeverity.MAJOR) -> AIIssue:
    return AIIssue(engine="kg", severity=severity, description=desc, regulation_ref=ref)


# ──────────────────────── ① 归一化 / 提取 ────────────────────────

def test_normalize_regulation_ref_strips_whitespace_and_case():
    assert normalize_regulation_ref(" gb50010-2010 第8.2.1条 ") == "GB50010第8.2.1条"


def test_normalize_regulation_ref_strips_version_year_by_default():
    assert normalize_regulation_ref("GB50010-2010") == normalize_regulation_ref("GB50010-2015")


def test_normalize_regulation_ref_empty_is_empty():
    assert normalize_regulation_ref("") == ""
    assert normalize_regulation_ref(None) == ""


def test_extract_obligation_level_reads_prefix():
    assert extract_obligation_level("[MUST] 锚固长度不足") == "MUST"
    assert extract_obligation_level("[MUST_NOT] 禁止事项") == "MUST_NOT"
    assert extract_obligation_level("[SHOULD] 建议事项") == "SHOULD"


def test_extract_obligation_level_defaults_to_should_without_prefix():
    assert extract_obligation_level("没有前缀的描述") == "SHOULD"


# ──────────────────────── ② evaluate_compliance：TP/FP/FN ────────────────────────

def test_exact_ref_match_within_severity_tolerance_is_tp():
    gt = [ComplianceGt(drawing_id="d1", regulation_ref="GB50010-2010 8.2.1",
                        discipline="structure", obligation_level="MUST", severity="major")]
    pred = [_issue(ref="GB50010-2010 8.2.1", desc="[MUST] 锚固长度不足", severity=IssueSeverity.CRITICAL)]

    m = evaluate_compliance(gt, pred)

    assert (m.tp, m.fp, m.fn) == (1, 0, 0)
    assert m.regulation_hit_rate == 1.0
    assert m.obligation_confusion == {"MUST": {"MUST": 1}}
    assert m.obligation_downgrade_rate == 0.0


def test_severity_diff_exceeding_tolerance_is_fp_wrong_severity_not_fn():
    gt = [ComplianceGt(drawing_id="d1", regulation_ref="GB50010-2010 8.2.1", severity="critical")]
    pred = [_issue(ref="GB50010-2010 8.2.1", desc="锚固问题", severity=IssueSeverity.INFO)]

    m = evaluate_compliance(gt, pred)

    assert m.tp == 0
    assert m.fn == 0  # 内容找到了，不算漏报
    assert m.fp == 1
    assert m.fp_breakdown["wrong_severity"] == 1
    assert m.fp_breakdown["spurious"] == 0
    assert m.fp_breakdown["wrong_ref"] == 0


def test_unmatched_pred_with_no_similar_gt_is_spurious():
    gt = [ComplianceGt(drawing_id="d1", regulation_ref="GB50010-2010 8.2.1", snippet="锚固长度不足需复核")]
    pred = [_issue(ref="", desc="完全无关的疏散距离问题描述内容")]

    m = evaluate_compliance(gt, pred)

    assert m.tp == 0
    assert m.fn == 1  # gt 没被满足
    assert m.fp == 1
    assert m.fp_breakdown["spurious"] == 1


def test_unmatched_pred_similar_content_wrong_ref_is_wrong_ref():
    gt = [ComplianceGt(drawing_id="d1", regulation_ref="GB50010-2010 8.2.1",
                        snippet="钢筋锚固长度明显不足需要复核")]
    # 另一条 gt 已经用精确条文抢占了匹配名额，本 pred 与第一条 gt 内容高度相似但
    # 条文引用不同，应落回 FP·wrong_ref（而非漏判为 spurious）。
    gt2 = ComplianceGt(drawing_id="d1", regulation_ref="GB50011-2010 3.1.1", snippet="抗震等级需复核")
    pred = [
        _issue(ref="GB50099-1999 9.9.9", desc="钢筋锚固长度明显不足，需要复核一下"),
        _issue(ref="GB50011-2010 3.1.1", desc="抗震等级需复核"),
    ]

    m = evaluate_compliance([gt[0], gt2], pred)

    assert m.fp_breakdown["wrong_ref"] == 1
    assert m.tp == 1  # 第二条精确匹配


def test_fn_breakdown_without_retrieval_universe_defaults_to_kg_missed():
    gt = [ComplianceGt(drawing_id="d1", regulation_ref="GB50010-2010 8.2.1")]
    m = evaluate_compliance(gt, [])
    assert m.fn == 1
    assert m.fn_breakdown == {"kg_missed_rag_missed": 1, "retrieved_but_llm_dropped": 0}


def test_fn_breakdown_with_retrieval_universe_detects_llm_dropped():
    gt = [ComplianceGt(drawing_id="d1", regulation_ref="GB50010-2010 8.2.1")]
    universe = [_issue(ref="GB50010-2010 8.2.1", desc="曾被召回但 LLM 核查剔除")]

    m = evaluate_compliance(gt, [], retrieval_universe=universe)

    assert m.fn_breakdown == {"kg_missed_rag_missed": 0, "retrieved_but_llm_dropped": 1}


def test_obligation_downgrade_rate_flags_must_to_should_direction():
    gt = [
        ComplianceGt(drawing_id="d1", regulation_ref="A", obligation_level="MUST", severity="major"),
        ComplianceGt(drawing_id="d1", regulation_ref="B", obligation_level="MUST", severity="major"),
    ]
    pred = [
        _issue(ref="A", desc="[SHOULD] 降级判定", severity=IssueSeverity.MAJOR),
        _issue(ref="B", desc="[MUST] 判定正确", severity=IssueSeverity.MAJOR),
    ]

    m = evaluate_compliance(gt, pred)

    assert m.tp == 2
    # MUST 行：1 个降级到 SHOULD，1 个保持 MUST → downgrade_rate = 1/2
    assert m.obligation_downgrade_rate == 0.5


def test_empty_gt_and_pred_yields_zeroed_metrics_no_division_error():
    m = evaluate_compliance([], [])
    assert (m.tp, m.fp, m.fn) == (0, 0, 0)
    assert m.precision == 0.0 and m.recall == 0.0 and m.f1 == 0.0
    assert m.regulation_hit_rate == 0.0
    assert m.obligation_downgrade_rate == 0.0


# ──────────────────────── ③ aggregate_compliance_metrics ────────────────────────

def test_aggregate_sums_raw_counts_and_rederives_rates_not_averages():
    # 样本1：tp=1,fp=1（precision=0.5）；样本2：tp=3,fp=0（precision=1.0）。
    # 简单平均 (0.5+1.0)/2=0.75 是错误的；正确做法是从 tp=4,fp=1 重新算 4/5=0.8。
    m1 = evaluate_compliance(
        [ComplianceGt(drawing_id="d1", regulation_ref="A")],
        [_issue(ref="A", severity=IssueSeverity.MAJOR), _issue(ref="", desc="无关内容噪声")],
    )
    m2 = evaluate_compliance(
        [
            ComplianceGt(drawing_id="d2", regulation_ref="X"),
            ComplianceGt(drawing_id="d2", regulation_ref="Y"),
            ComplianceGt(drawing_id="d2", regulation_ref="Z"),
        ],
        [_issue(ref="X"), _issue(ref="Y"), _issue(ref="Z")],
    )

    merged = aggregate_compliance_metrics([m1, m2])

    assert merged.tp == 4
    assert merged.fp == 1
    assert merged.precision == pytest.approx(4 / 5)
    assert merged.sample_count == 2


def test_aggregate_empty_list_returns_zeroed_metrics():
    merged = aggregate_compliance_metrics([])
    assert merged.sample_count == 0
    assert merged.tp == 0 and merged.fp == 0 and merged.fn == 0


# ──────────────────────── ④ run_compliance_comparison（DI mock） ────────────────────────

@pytest.mark.asyncio
async def test_run_compliance_comparison_aggregates_three_methods_offline():
    sample = ComplianceEvalSample(
        ctx=_ctx(),
        gt=(ComplianceGt(drawing_id="d1", regulation_ref="GB50010-2010 8.2.1", severity="major"),),
    )

    async def kg_runner(ctx):
        return [_issue(ref="GB50010-2010 8.2.1", severity=IssueSeverity.MAJOR)]

    async def rag_runner(ctx):
        return []  # rag 漏报，验证 kg_only.recall 应高于 rag_only.recall

    async def graphrag_runner(ctx):
        return GraphRAGFusionResult(
            issues=(_issue(ref="GB50010-2010 8.2.1", desc="[MUST] 核查通过", severity=IssueSeverity.MAJOR),),
            mode="fusion", kg_count=1, rag_count=0, merged_count=1, llm_verified_count=1,
        )

    report = await run_compliance_comparison(
        [sample], kg_runner=kg_runner, rag_runner=rag_runner, graphrag_runner=graphrag_runner,
    )

    assert report.sample_count == 1
    assert set(report.methods.keys()) == {"kg_only", "rag_only", "graphrag"}
    assert report.methods["kg_only"].recall == 1.0
    assert report.methods["rag_only"].recall == 0.0
    assert report.methods["graphrag"].recall == 1.0
    assert len(report.notes) >= 1
    d = report.to_dict()
    assert d["sample_count"] == 1
    assert "kg_only" in d["methods"]
