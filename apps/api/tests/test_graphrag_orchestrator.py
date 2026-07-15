"""D-18 GraphRAG 融合灰度接入编排层单测（`core/ai_review/orchestrator.py`）。

覆盖：
    ① 灰度开关 env 解析（`_graphrag_fusion_enabled`）——未设置/空/非真值 = 关闭，
       大小写/常见真值写法（1/true/yes/on）均视为开启。
    ② `_GraphRAGStage`：把 `run_graphrag_fusion` 包装成 `analyze(ctx, db)` 接口，
       正确透传 config、保存 `last_result`、返回值等于 `result.issues`。
    ③ `_build_parallel_engines`：灰度关闭 = `[Rules, KG, RAG, ReviewAudit]`（与
       接入前逐字节一致的引擎类型序列）；灰度开启 = `[Rules, _GraphRAGStage,
       ReviewAudit]`。
    ④ `_build_engine_results`：灰度关闭时字段集合（vision/rules/kg/rag/review/
       ocr_metadata）与取值同接入前；灰度开启时 kg/rag 替换为 graphrag 三个
       诊断键（graphrag/graphrag_mode/kg_count/rag_count）。

全部离线可跑：不连真实 DB/Redis/LLM，`run_graphrag_fusion` 通过 monkeypatch 替换。
"""
from __future__ import annotations

import pytest

import core.ai_review.orchestrator as orch
from core.ai_review.base import AIIssue, DrawingContext, IssueSeverity
from core.ai_review.graphrag.types import FusionConfig, GraphRAGFusionResult
from core.ai_review.kg_engine import KGEngine
from core.ai_review.rag_engine import RAGEngine
from core.ai_review.review_audit.engine import ReviewAuditEngine
from core.ai_review.rules_engine import RulesEngine


def _ctx(**overrides) -> DrawingContext:
    defaults = dict(
        drawing_id="d1", drawing_no="S-101", discipline="structure",
        title="结构平面图", version="A", file_key="k", file_ext="pdf",
        project_id="p1",
    )
    defaults.update(overrides)
    return DrawingContext(**defaults)


# ──────────────────────── ① env 灰度开关解析 ────────────────────────

@pytest.mark.parametrize("value", [None, "", "0", "false", "no", "off", "garbage"])
def test_graphrag_fusion_disabled_by_default_and_on_falsy_values(monkeypatch, value):
    if value is None:
        monkeypatch.delenv(orch._GRAPHRAG_ENV_VAR, raising=False)
    else:
        monkeypatch.setenv(orch._GRAPHRAG_ENV_VAR, value)
    assert orch._graphrag_fusion_enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "True", "TRUE", "yes", "on"])
def test_graphrag_fusion_enabled_on_truthy_values(monkeypatch, value):
    monkeypatch.setenv(orch._GRAPHRAG_ENV_VAR, value)
    assert orch._graphrag_fusion_enabled() is True


# ──────────────────────── ② _GraphRAGStage 适配层 ────────────────────────

@pytest.mark.asyncio
async def test_graphrag_stage_delegates_and_stores_last_result(monkeypatch):
    captured: dict = {}

    async def fake_run_graphrag_fusion(ctx, db, redis, *, config):
        captured["ctx"] = ctx
        captured["db"] = db
        captured["redis"] = redis
        captured["config"] = config
        return GraphRAGFusionResult(
            issues=(AIIssue(engine="graphrag", severity=IssueSeverity.MAJOR, description="x"),),
            mode="fusion", kg_count=1, rag_count=1, merged_count=1, llm_verified_count=1,
        )

    monkeypatch.setattr(orch, "run_graphrag_fusion", fake_run_graphrag_fusion)

    db, redis = object(), object()
    cfg = FusionConfig(enabled=True)
    stage = orch._GraphRAGStage(db, redis, cfg)
    ctx = _ctx()

    issues = await stage.analyze(ctx, db)

    assert captured["ctx"] is ctx
    assert captured["db"] is db
    assert captured["redis"] is redis
    assert captured["config"] is cfg
    assert stage.last_result is not None
    assert stage.last_result.mode == "fusion"
    assert issues == list(stage.last_result.issues)
    assert stage.engine_name == "graphrag"


# ──────────────────────── ③ _build_parallel_engines ────────────────────────

def test_build_parallel_engines_disabled_matches_pre_d18_engine_sequence():
    engines, stage = orch._build_parallel_engines(db=object(), redis=object(), graphrag_enabled=False)

    assert stage is None
    assert [type(e) for e in engines] == [RulesEngine, KGEngine, RAGEngine, ReviewAuditEngine]
    assert [e.engine_name for e in engines] == ["rules", "kg", "rag", "review"]


def test_build_parallel_engines_enabled_replaces_kg_rag_with_graphrag_stage():
    engines, stage = orch._build_parallel_engines(db=object(), redis=object(), graphrag_enabled=True)

    assert isinstance(stage, orch._GraphRAGStage)
    assert [type(e) for e in engines] == [RulesEngine, orch._GraphRAGStage, ReviewAuditEngine]
    assert [e.engine_name for e in engines] == ["rules", "graphrag", "review"]
    assert stage._config.enabled is True


# ──────────────────────── ④ _build_engine_results ────────────────────────

def test_build_engine_results_disabled_matches_pre_d18_shape():
    vision_issues = [AIIssue(engine="ocr", severity=IssueSeverity.INFO, description="v")]
    parallel_results = [
        [AIIssue(engine="rules", severity=IssueSeverity.INFO, description="r")],
        [AIIssue(engine="kg", severity=IssueSeverity.INFO, description="k")] * 2,
        [AIIssue(engine="rag", severity=IssueSeverity.INFO, description="g")] * 3,
        [AIIssue(engine="review", severity=IssueSeverity.INFO, description="a")] * 4,
    ]
    ctx = _ctx()
    ctx.ocr_metadata = {"pages": 1}

    result = orch._build_engine_results(vision_issues, parallel_results, None, ctx)

    assert result == {
        "vision": 1, "rules": 1, "kg": 2, "rag": 3, "review": 4,
        "ocr_metadata": {"pages": 1},
    }


def test_build_engine_results_enabled_reports_graphrag_diagnostics():
    vision_issues: list[AIIssue] = []
    parallel_results = [
        [AIIssue(engine="rules", severity=IssueSeverity.INFO, description="r")],
        [AIIssue(engine="graphrag", severity=IssueSeverity.MAJOR, description="g")] * 2,
        [AIIssue(engine="review", severity=IssueSeverity.INFO, description="a")] * 5,
    ]
    ctx = _ctx()

    class _FakeStage:
        last_result = GraphRAGFusionResult(
            mode="fusion_degraded", kg_count=3, rag_count=4, merged_count=5,
            llm_verified_count=0, warnings=("llm_verify_unavailable_fallback_to_merged",),
        )

    result = orch._build_engine_results(vision_issues, parallel_results, _FakeStage(), ctx)

    assert result == {
        "vision": 0, "rules": 1, "graphrag": 2,
        "graphrag_mode": "fusion_degraded", "kg_count": 3, "rag_count": 4,
        "review": 5, "ocr_metadata": {},
    }


def test_build_engine_results_enabled_handles_missing_last_result():
    """理论上不应发生（_safe_run 总会调用 analyze），但函数须优雅兜底不抛异常。"""
    class _FakeStage:
        last_result = None

    result = orch._build_engine_results([], [[], [], []], _FakeStage(), _ctx())

    assert result["graphrag_mode"] is None
    assert result["kg_count"] == 0
    assert result["rag_count"] == 0
