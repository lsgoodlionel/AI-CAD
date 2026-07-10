"""C-13 融合策略引擎单测 —— 四类场景 + 确定性优先原则。

覆盖 Phase C 验收核心：
    ① 仅规则命中          —— 规则候选全保留，source/confidence 不变。
    ② 仅模型命中（补召回）—— 空白区模型候选按门槛纳入，标 source="model"。
    ③ 规则/模型一致        —— 同处同类共识增强置信，source="fused"。
    ④ 规则/模型冲突        —— 按置信+优先级仲裁；**规则强命中不被模型覆盖**。

并断言：每个输出均带 source + confidence；结构性保证「召回 ≥ 纯规则」。
"""
from __future__ import annotations

import pytest

from core.model3d.fusion.arbitration import (
    DEFAULT_POLICY,
    FusionPolicy,
    bbox_iou,
    is_rule_strong,
    pair_candidates,
)
from core.model3d.fusion.fusion_engine import (
    _coerce_float,
    _coerce_priority,
    fuse,
    load_fusion_policy,
)
from core.model3d.spotting.types import SymbolCandidate


# ── 测试辅助 ─────────────────────────────────────────────
def _cand(
    category: str,
    confidence: float,
    bbox: tuple[float, float, float, float],
    source: str = "rule",
    **kw,
) -> SymbolCandidate:
    return SymbolCandidate(
        category=category, confidence=confidence, bbox=bbox, source=source, **kw
    )


def _assert_all_have_source_and_confidence(result) -> None:
    for c in result.candidates:
        assert c.source in ("rule", "model", "fused")
        assert 0.0 <= c.confidence <= 1.0


# ── 几何 / 配对基础 ──────────────────────────────────────
def test_bbox_iou_identical_boxes_is_one():
    # Arrange / Act / Assert
    assert bbox_iou((0, 0, 10, 10), (0, 0, 10, 10)) == pytest.approx(1.0)


def test_bbox_iou_disjoint_boxes_is_zero():
    assert bbox_iou((0, 0, 10, 10), (20, 20, 30, 30)) == 0.0


def test_bbox_iou_tolerates_unordered_coords():
    # 坐标顺序颠倒仍应归一
    assert bbox_iou((10, 10, 0, 0), (0, 0, 10, 10)) == pytest.approx(1.0)


def test_pair_candidates_greedy_one_to_one():
    # Arrange: 两规则、两模型，各自空间对齐
    rules = (_cand("wall", 0.6, (0, 0, 10, 10)), _cand("beam", 0.6, (100, 100, 110, 110)))
    models = (
        _cand("wall", 0.7, (1, 1, 11, 11), source="model"),
        _cand("beam", 0.7, (100, 100, 110, 110), source="model"),
    )
    # Act
    pairs, unmatched_rules, unmatched_models = pair_candidates(rules, models, 0.3)
    # Assert
    assert len(pairs) == 2
    assert unmatched_rules == ()
    assert unmatched_models == ()


# ── 场景 ① 仅规则命中 ────────────────────────────────────
def test_scenario_rule_only_all_rules_retained():
    # Arrange: 只有规则候选，无模型
    rules = (
        _cand("column", 0.9, (0, 0, 5, 5)),
        _cand("wall", 0.6, (10, 10, 20, 20)),
    )
    # Act
    result = fuse(rules, ())
    # Assert: 规则全保留、来源不变、置信不变
    assert len(result.candidates) == 2
    assert {c.source for c in result.candidates} == {"rule"}
    assert {c.confidence for c in result.candidates} == {0.9, 0.6}
    assert {r.decision for r in result.records} == {"rule_only"}
    _assert_all_have_source_and_confidence(result)


# ── 场景 ② 仅模型命中（补召回）──────────────────────────
def test_scenario_model_only_recall_fills_gaps():
    # Arrange: 无规则，模型候选落在空白区
    models = (
        _cand("pipe", 0.8, (0, 0, 5, 5), source="model"),
        _cand("equipment", 0.9, (10, 10, 20, 20), source="model"),
    )
    # Act
    result = fuse((), models)
    # Assert: 补召回，均标 source="model"
    assert len(result.candidates) == 2
    assert {c.source for c in result.candidates} == {"model"}
    assert {r.decision for r in result.records} == {"model_recall"}
    _assert_all_have_source_and_confidence(result)


def test_scenario_model_low_confidence_rejected():
    # Arrange: 模型候选置信低于门槛（默认 0.45）
    models = (_cand("door", 0.2, (0, 0, 5, 5), source="model"),)
    # Act
    result = fuse((), models)
    # Assert: 噪声不补召回
    assert result.candidates == ()
    assert result.records[0].decision == "model_rejected"


# ── 场景 ③ 规则/模型一致（增强置信）────────────────────
def test_scenario_agreement_boosts_confidence():
    # Arrange: 同处同类，规则 0.6 / 模型 0.7
    rule = _cand("wall", 0.6, (0, 0, 10, 10))
    model = _cand("wall", 0.7, (0, 0, 10, 10), source="model")
    # Act
    result = fuse((rule,), (model,))
    # Assert: 单一融合候选，置信被增强且高于两者
    assert len(result.candidates) == 1
    out = result.candidates[0]
    assert out.source == "fused"
    assert out.category == "wall"
    assert out.confidence > 0.7
    assert result.records[0].decision == "consensus"
    _assert_all_have_source_and_confidence(result)


# ── 场景 ④ 规则/模型冲突（仲裁 + 强规则保护）────────────
def test_scenario_conflict_strong_rule_not_overridden():
    # Arrange: 同处异类，规则强命中（0.9 ≥ 0.85）
    rule = _cand("column", 0.9, (0, 0, 10, 10))
    model = _cand("wall", 0.95, (0, 0, 10, 10), source="model")
    # Act
    result = fuse((rule,), (model,))
    # Assert: 强规则不被翻案 —— 类别仍为 column，来源保持 rule
    assert len(result.candidates) == 1
    out = result.candidates[0]
    assert out.category == "column"
    assert out.source == "rule"
    assert result.records[0].decision == "rule_protected"
    assert out.evidence["rejected_model_category"] == "wall"
    _assert_all_have_source_and_confidence(result)


def test_scenario_conflict_weak_rule_overridden_by_strong_model():
    # Arrange: 同处异类，规则弱（0.5）、模型强（0.9）
    rule = _cand("door", 0.5, (0, 0, 10, 10))
    model = _cand("window", 0.9, (0, 0, 10, 10), source="model")
    # Act
    result = fuse((rule,), (model,))
    # Assert: 弱规则被模型翻案 → 融合取模型类别，source=fused
    assert len(result.candidates) == 1
    out = result.candidates[0]
    assert out.category == "window"
    assert out.source == "fused"
    assert result.records[0].decision == "model_override"
    _assert_all_have_source_and_confidence(result)


def test_scenario_conflict_weak_rule_kept_when_model_marginal():
    # Arrange: 规则弱但模型仅略高、且高优先级规则（column）抬高门槛
    rule = _cand("column", 0.6, (0, 0, 10, 10))
    model = _cand("beam", 0.62, (0, 0, 10, 10), source="model")
    # Act
    result = fuse((rule,), (model,))
    # Assert: 模型未清晰胜出 → 规则保留
    out = result.candidates[0]
    assert out.category == "column"
    assert out.source == "rule"
    assert result.records[0].decision == "rule_wins"


# ── 结构性验收：召回 ≥ 纯规则；强命中不被覆盖 ───────────
def test_recall_never_below_pure_rules():
    # Arrange: 3 规则 + 2 模型（1 冲突强规则、1 空白补召回）
    rules = (
        _cand("column", 0.9, (0, 0, 10, 10)),      # 强规则
        _cand("wall", 0.6, (50, 50, 60, 60)),      # 弱规则
        _cand("beam", 0.7, (100, 0, 110, 10)),
    )
    models = (
        _cand("slab", 0.95, (0, 0, 10, 10), source="model"),     # 与强规则冲突
        _cand("pipe", 0.8, (200, 200, 210, 210), source="model"),  # 空白补召回
    )
    # Act
    result = fuse(rules, models)
    # Assert: 融合候选数 ≥ 规则数（每规则位保留 + 模型补召回）
    assert len(result.candidates) >= len(rules)
    # 强规则位仍为 column（未被 slab 覆盖）
    strong = [c for c in result.candidates if c.bbox == (0, 0, 10, 10)]
    assert strong and strong[0].category == "column"
    # 补召回项存在
    assert any(c.source == "model" and c.category == "pipe" for c in result.candidates)
    _assert_all_have_source_and_confidence(result)


# ── 策略加载（降级安全）─────────────────────────────────
def test_load_fusion_policy_returns_policy():
    policy = load_fusion_policy()
    assert isinstance(policy, FusionPolicy)
    assert policy.rule_priority.get("column", 0) >= policy.rule_priority.get("axis", 0)


def test_load_policy_reads_yaml_priority_table():
    # 真实 YAML 应被读入（column 优先级 10 > pipe 5）
    policy = load_fusion_policy()
    assert policy.rule_priority["column"] == 10
    assert policy.rule_priority["pipe"] == 5


def test_coerce_priority_skips_invalid_and_falls_back():
    default = {"wall": 8}
    # 非 dict → 回退默认
    assert _coerce_priority(["bad"], default) == default
    # 无效项跳过（bool/非数值/非 str 键）；有效项保留
    parsed = _coerce_priority(
        {"column": 10, "beam": True, "slab": "x", 5: 3}, default
    )
    assert parsed == {"column": 10}
    # 全无效 → 回退默认
    assert _coerce_priority({"a": None}, default) == default


def test_coerce_float_rejects_bool_and_non_numeric():
    assert _coerce_float(0.5, 0.1) == 0.5
    assert _coerce_float(True, 0.1) == 0.1  # bool 不算数值
    assert _coerce_float("nope", 0.1) == 0.1


def test_is_rule_strong_threshold():
    assert is_rule_strong(_cand("column", 0.85, (0, 0, 1, 1)), DEFAULT_POLICY)
    assert not is_rule_strong(_cand("column", 0.84, (0, 0, 1, 1)), DEFAULT_POLICY)


def test_fuse_is_pure_does_not_mutate_inputs():
    # Arrange
    rules = (_cand("wall", 0.6, (0, 0, 10, 10)),)
    models = (_cand("wall", 0.7, (0, 0, 10, 10), source="model"),)
    # Act
    fuse(rules, models)
    # Assert: 入参未被改动（frozen + 纯操作）
    assert rules[0].confidence == 0.6
    assert models[0].source == "model"


def test_fusion_result_to_dict_shape():
    result = fuse((_cand("wall", 0.6, (0, 0, 10, 10)),), ())
    payload = result.to_dict()
    assert "candidates" in payload
    assert "records" in payload
    assert payload["counts_by_source"] == {"rule": 1}
