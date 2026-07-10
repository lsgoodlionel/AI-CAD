"""C-14 评测基座测试：度量正确性 + 三方法编排 + 报告渲染。"""
from __future__ import annotations

from core.model3d.eval.harness import (
    EvalSample,
    discipline_of,
    run_comparison,
)
from core.model3d.eval.metrics import GtBox, evaluate, iou
from core.model3d.eval.report import render_markdown
from core.model3d.preprocess.schema import Primitive, PrimitiveDoc
from core.model3d.spotting.types import SymbolCandidate


def _cand(cat, bbox, source="model"):
    return SymbolCandidate(category=cat, confidence=0.9, bbox=bbox, source=source)


# ── IoU ─────────────────────────────────────────────────────────────

def test_iou_identical_is_one():
    assert iou((0, 0, 10, 10), (0, 0, 10, 10)) == 1.0


def test_iou_disjoint_is_zero():
    assert iou((0, 0, 10, 10), (20, 20, 30, 30)) == 0.0


def test_iou_half_overlap():
    # 两个 10x10，x 方向重叠 5 → 交 50 / 并 150 = 1/3
    assert abs(iou((0, 0, 10, 10), (5, 0, 15, 10)) - (50 / 150)) < 1e-9


def test_iou_degenerate_zero_area():
    assert iou((0, 0, 0, 0), (0, 0, 0, 0)) == 0.0


# ── evaluate：TP/FP/FN + PQ ─────────────────────────────────────────

def test_perfect_match_gives_pq_one():
    gt = [GtBox("column", (0, 0, 10, 10)), GtBox("beam", (20, 20, 30, 30))]
    pred = [_cand("column", (0, 0, 10, 10)), _cand("beam", (20, 20, 30, 30))]
    m = evaluate(gt, pred)
    assert (m.tp, m.fp, m.fn) == (2, 0, 0)
    assert m.precision == 1.0 and m.recall == 1.0 and m.f1 == 1.0
    assert m.pq == 1.0  # SQ=1 × RQ=1


def test_category_mismatch_is_not_tp():
    gt = [GtBox("column", (0, 0, 10, 10))]
    pred = [_cand("beam", (0, 0, 10, 10))]  # 同位置异类别
    m = evaluate(gt, pred)
    assert (m.tp, m.fp, m.fn) == (0, 1, 1)
    assert m.pq == 0.0


def test_below_iou_threshold_is_miss():
    gt = [GtBox("column", (0, 0, 10, 10))]
    pred = [_cand("column", (8, 8, 18, 18))]  # IoU 很低
    m = evaluate(gt, pred)
    assert m.tp == 0 and m.fp == 1 and m.fn == 1


def test_partial_precision_recall():
    gt = [GtBox("column", (0, 0, 10, 10)), GtBox("beam", (20, 20, 30, 30))]
    pred = [_cand("column", (0, 0, 10, 10)), _cand("wall", (100, 100, 110, 110))]
    m = evaluate(gt, pred)
    assert m.tp == 1 and m.fp == 1 and m.fn == 1
    assert m.precision == 0.5 and m.recall == 0.5


def test_empty_inputs_safe():
    assert evaluate([], []).pq == 0.0
    assert evaluate([GtBox("column", (0, 0, 1, 1))], []).recall == 0.0


def test_per_category_and_discipline_breakdown():
    gt = [GtBox("column", (0, 0, 10, 10)), GtBox("pipe", (20, 20, 30, 30))]
    pred = [_cand("column", (0, 0, 10, 10)), _cand("pipe", (20, 20, 30, 30))]
    m = evaluate(gt, pred, discipline_of=discipline_of)
    assert m.per_category["column"]["pq"] == 1.0
    assert "结构" in m.per_discipline and "机电" in m.per_discipline


def test_confusion_matrix_records_misclassification():
    gt = [GtBox("column", (0, 0, 10, 10))]
    pred = [_cand("beam", (0, 0, 10, 10))]  # 位置匹配但类别错
    m = evaluate(gt, pred)
    assert m.confusion["column"]["beam"] == 1


# ── discipline 映射 ─────────────────────────────────────────────────

def test_discipline_mapping():
    assert discipline_of("column") == "结构"
    assert discipline_of("door") == "建筑"
    assert discipline_of("pipe") == "机电"
    assert discipline_of("axis") == "通用"


# ── 三方法编排 ──────────────────────────────────────────────────────

def _demo_sample():
    prims = (
        Primitive(id=0, type="polyline",
                  points=((0, 0), (400, 0), (400, 400), (0, 400), (0, 0)),
                  layer="S-COLU", block="KZ1", closed=True),
        Primitive(id=1, type="line", points=((0, 0), (2000, 0)), layer="S-BEAM"),
    )
    gt = (
        GtBox("column", (0, 0, 400, 400)),
        GtBox("beam", (0, 0, 2000, 0)),
    )
    return EvalSample(doc=PrimitiveDoc(primitives=prims), gt=gt, sample_id="s1")


def test_run_comparison_has_three_methods():
    report = run_comparison([_demo_sample()])
    assert set(report.methods) == {"rule", "model", "fusion"}
    assert report.sample_count == 1


def test_run_comparison_is_deterministic():
    r1 = run_comparison([_demo_sample()]).to_dict()
    r2 = run_comparison([_demo_sample()]).to_dict()
    assert r1 == r2


def test_fusion_recall_not_below_rule():
    """结构性保证：融合召回 ≥ 纯规则（规则候选全保留）。"""
    report = run_comparison([_demo_sample()])
    assert report.methods["fusion"].recall >= report.methods["rule"].recall


def test_rule_detects_column_from_layer():
    """S-COLU 图层的柱应被规则识别到（PQ>0）。"""
    report = run_comparison([_demo_sample()])
    assert report.methods["rule"].per_category.get("column", {}).get("pq", 0) > 0


# ── 报告渲染 ────────────────────────────────────────────────────────

def test_render_markdown_has_sections():
    report = run_comparison([_demo_sample()], ceiling={"SymPoint_PQ": 0.83})
    md = render_markdown(report)
    assert "# Phase C 评测对比报告" in md
    assert "总体指标" in md and "分类别" in md and "结论摘要" in md
    assert "天花板参照" in md  # ceiling 提供时出现
    assert "纯规则" in md and "融合" in md
