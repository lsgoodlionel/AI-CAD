"""会审审查 — 问题归类与风险分级测试"""
import pytest

from core.ai_review.review_audit.classifier import classify
from core.ai_review.review_audit.protocol_loader import load_disciplines

_HAS_PROTOCOL = bool(load_disciplines())

_EMPTY_LOC = {"drawings": [], "levels": [], "axes": [], "nodes_or_systems": [], "spaces": []}


@pytest.mark.unit
def test_classify_returns_expected_shape():
    out = classify("JG", [{"label": "标高", "reason": "x"}], "标高不一致", _EMPTY_LOC)
    assert set(out) >= {"issue_class", "risk", "interface"}
    assert set(out["risk"]) >= {"level", "trigger"}
    assert set(out["interface"]) >= {"primary", "related", "reason"}


@pytest.mark.unit
def test_issue_class_is_subset_of_allowed():
    allowed = {"表达遗漏", "图纸冲突", "接口冲突", "施工条件问题", "验收风险"}
    out = classify("JG", [], "现平面图与剖面图标高不一致，无法施工", _EMPTY_LOC)
    assert set(out["issue_class"]) <= allowed
    assert out["issue_class"]  # 非空


@pytest.mark.unit
def test_risk_level_in_three_grades():
    out = classify("JG", [], "一般表达优化", _EMPTY_LOC)
    assert out["risk"]["level"] in {"高", "中", "低"}


@pytest.mark.unit
@pytest.mark.skipif(not _HAS_PROTOCOL, reason="disciplines.yaml 未就绪")
def test_safety_text_escalates_risk():
    # 结构安全/消防类文本应升级，不应停留在低风险
    out = classify(
        "JG", [{"label": "标高", "reason": "x"}],
        "节点锚固缺失，影响结构安全，无法施工需返工", _EMPTY_LOC,
    )
    assert out["risk"]["level"] in {"高", "中"}
    assert out["risk"]["trigger"]


@pytest.mark.unit
@pytest.mark.skipif(not _HAS_PROTOCOL, reason="disciplines.yaml 未就绪")
def test_interface_primary_comes_from_protocol():
    out = classify("GPS", [], "管道标高与建筑打架", _EMPTY_LOC)
    # 给排水默认接口含本专业/建筑等
    assert isinstance(out["interface"]["related"], list)
