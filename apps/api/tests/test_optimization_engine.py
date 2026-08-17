"""学习闭环编排单测(扫事件 → 建议 → 采纳 → 生效)。"""
import json

import pytest

from services.learned_rules import (
    apply_ocr_corrections, extra_vocabulary, invalidate_cache, threshold_of,
)
from services.optimization_engine import (
    CATEGORY_TO_RULE, build_export, review_suggestion, rule_from_suggestion,
    run_optimization,
)


class _FakeDb:
    def __init__(self, events=None, existing=None, suggestion=None):
        self.events = events or []
        self.existing = existing or []
        self.suggestion = suggestion
        self.inserted: list[dict] = []
        self.updates: list[dict] = []
        self.rules: list[dict] = []

    async def fetch_one(self, sql, params):
        if "improvement_suggestions WHERE id" in sql:
            return self.suggestion
        if "INSERT INTO learned_rules" in sql:
            self.rules.append(params)
            return {"id": "r1"}
        if "INSERT INTO improvement_suggestions" in sql:
            self.inserted.append(params)
            return {"id": "s1"}
        return {"id": "run1"}

    async def fetch_all(self, sql, params):
        if "annotation_events" in sql:
            return self.events
        if "improvement_suggestions" in sql:
            return self.existing
        return []

    async def execute(self, sql, params):
        if "INSERT INTO improvement_suggestions" in sql:
            self.inserted.append(params)
        else:
            self.updates.append(params)


def _row(kind, human=None, auto=None, ctx=None):
    return {"kind": kind, "field": None, "auto_value": auto, "human_value": human,
            "context_json": ctx or {}, "created_at": None}


# ── 一轮分析 ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_optimization_logs_every_step():
    """过程日志不是装饰:人要靠它判断「这条建议凭什么」。"""
    db = _FakeDb(events=[_row("discipline", human="舞台机械") for _ in range(5)])
    res = await run_optimization(db, "p1")
    names = [s["step"] for s in res["steps"]]
    assert names == ["start", "scan_events", "analyze", "persist", "done"]
    assert res["scanned"] == 5 and res["findings"] >= 1


@pytest.mark.asyncio
async def test_run_optimization_skips_already_proposed_titles():
    """跑一轮翻一倍的列表没人看得下去;已提过的(含被否决的)不重复插。"""
    db = _FakeDb(events=[_row("discipline", human="人防") for _ in range(4)],
                 existing=[{"title": "专业词表补录「人防」"}])
    res = await run_optimization(db, "p1")
    persist = next(s for s in res["steps"] if s["step"] == "persist")
    assert persist["new_suggestions"] == 0
    assert persist["skipped_duplicates"] >= 1


@pytest.mark.asyncio
async def test_run_optimization_parses_json_string_context():
    db = _FakeDb(events=[
        {"kind": "discipline", "field": None, "auto_value": None,
         "human_value": "建筑", "context_json": json.dumps({"raw_text": "建 个人"}),
         "created_at": None} for _ in range(4)])
    res = await run_optimization(db, "p1")
    assert res["findings"] >= 1


@pytest.mark.asyncio
async def test_run_optimization_records_error_without_raising():
    class _Boom(_FakeDb):
        async def fetch_all(self, sql, params):
            raise RuntimeError("db down")

    res = await run_optimization(_Boom(), "p1")
    assert res["error"] and any(s["step"] == "error" for s in res["steps"])


# ── 建议 → 规则 ─────────────────────────────────────────────────

def test_rule_from_suggestion_for_each_auto_category():
    assert rule_from_suggestion("vocabulary", {"word": "人防"}) == ("人防", "人防")
    assert rule_from_suggestion(
        "ocr_correction", {"raw": "建 个人", "corrected": "建筑"}) == ("建 个人", "建筑")
    assert rule_from_suggestion(
        "threshold", {"observed_spans": [0.16, 0.2]}) == ("axis_min_span", "0.144")


def test_rule_from_suggestion_rejects_incomplete_evidence():
    assert rule_from_suggestion("vocabulary", {}) is None
    assert rule_from_suggestion("ocr_correction", {"raw": "x"}) is None
    assert rule_from_suggestion("threshold", {}) is None
    assert rule_from_suggestion("algorithm", {"anything": 1}) is None


def test_algorithm_category_has_no_rule_mapping():
    """算法改造类不该有生效路径——否则会假装采纳就解决了。"""
    assert "algorithm" not in CATEGORY_TO_RULE


# ── 人审 ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_accept_auto_applicable_writes_rule_and_takes_effect():
    db = _FakeDb(suggestion={
        "id": "s1", "project_id": "p1", "category": "vocabulary",
        "title": "补录「人防」", "evidence_json": {"word": "人防"},
        "auto_applicable": True, "status": "pending"})
    res = await review_suggestion(db, "s1", accept=True, user_id="u1")
    assert res["applied"] is True and res["status"] == "accepted"
    assert db.rules[0]["rule_key"] == "人防"


@pytest.mark.asyncio
async def test_accept_algorithm_suggestion_does_not_pretend_to_fix():
    db = _FakeDb(suggestion={
        "id": "s2", "project_id": "p1", "category": "algorithm",
        "title": "版式指纹不足", "evidence_json": {},
        "auto_applicable": False, "status": "pending"})
    res = await review_suggestion(db, "s2", accept=True, user_id="u1")
    assert res["status"] == "exported" and res["applied"] is False
    assert db.rules == []


@pytest.mark.asyncio
async def test_reject_records_status_without_writing_rules():
    db = _FakeDb(suggestion={
        "id": "s3", "project_id": "p1", "category": "vocabulary",
        "title": "补录「X」", "evidence_json": {"word": "X"},
        "auto_applicable": True, "status": "pending"})
    res = await review_suggestion(db, "s3", accept=False, user_id="u1")
    assert res["status"] == "rejected" and db.rules == []


@pytest.mark.asyncio
async def test_review_missing_suggestion():
    res = await review_suggestion(_FakeDb(suggestion=None), "nope",
                                  accept=True, user_id=None)
    assert res["ok"] is False


# ── 生效层 ──────────────────────────────────────────────────────

def test_apply_ocr_corrections_replaces_longest_first():
    """短串可能是长串的子串,先替换短的会把长的拆坏。"""
    corrections = {"建 个": "建", "建 个人": "建筑"}
    assert apply_ocr_corrections("专业 建 个人", corrections) == "专业 建筑"


def test_apply_ocr_corrections_noops_without_rules():
    assert apply_ocr_corrections("原文", {}) == "原文"
    assert apply_ocr_corrections("", {"a": "b"}) == ""


def test_threshold_of_falls_back_on_missing_or_bad_value():
    assert threshold_of({}, "axis_min_span", 0.25) == 0.25
    assert threshold_of({"threshold": {"axis_min_span": "0.15"}},
                        "axis_min_span", 0.25) == 0.15
    # 坏规则不该毁掉识别
    assert threshold_of({"threshold": {"axis_min_span": "abc"}},
                        "axis_min_span", 0.25) == 0.25


def test_extra_vocabulary_reads_learned_words():
    assert extra_vocabulary({"vocabulary": {"人防": "人防"}}) == {"人防": "人防"}
    assert extra_vocabulary({}) == {}


def test_invalidate_cache_is_safe_to_call():
    invalidate_cache("p1")
    invalidate_cache()


# ── 导出 ────────────────────────────────────────────────────────

def test_build_export_contains_only_developer_items_with_evidence():
    run = {"run_id": "r1", "scanned": 120, "steps": [{"step": "done"}]}
    suggestions = [
        {"title": "版式指纹不足", "category": "algorithm", "detail": "...",
         "evidence": {"page_aspect": 1.41}, "impact": 9, "confidence": 0.7,
         "auto_applicable": False},
        {"title": "补录「人防」", "category": "vocabulary", "detail": "...",
         "evidence": {"word": "人防"}, "impact": 5, "confidence": 0.8,
         "auto_applicable": True},
    ]
    pack = build_export(run, suggestions)
    assert len(pack["needs_development"]) == 1
    assert pack["needs_development"][0]["evidence"] == {"page_aspect": 1.41}
    assert pack["auto_applied_count"] == 1
    assert pack["process_log"] == run["steps"]
