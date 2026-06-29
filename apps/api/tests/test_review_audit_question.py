"""会审审查 — 闭环问题生成测试（question_generator）"""
import re

import pytest

from core.ai_review.review_audit.question_generator import generate
from core.ai_review.review_audit.protocol_loader import load_templates

_HAS_TEMPLATES = bool(load_templates())

_LOC = {"drawings": ["JS-101"], "levels": ["地下二层"], "axes": ["③~⑤轴"],
        "nodes_or_systems": [], "spaces": []}


@pytest.mark.unit
def test_generate_returns_list_of_strings():
    out = generate("JG", [{"label": "标高", "reason": "x"}], [], _LOC, "标高不一致")
    assert isinstance(out, list)
    assert all(isinstance(s, str) for s in out)


@pytest.mark.unit
@pytest.mark.skipif(not _HAS_TEMPLATES, reason="question_templates.yaml 未就绪")
def test_generated_question_has_no_unfilled_placeholders():
    out = generate("JG", [{"label": "标高", "reason": "x"}], [], _LOC, "标高不一致")
    assert out
    for q in out:
        # 占位符 {对象}/{待明确} 不得残留
        assert not re.search(r"\{对象\}|\{待明确\}", q)


@pytest.mark.unit
@pytest.mark.skipif(not _HAS_TEMPLATES, reason="question_templates.yaml 未就绪")
def test_object_hit_appears_in_question():
    out = generate("JG", [{"label": "标高", "reason": "x"}], ["梁、柱、板、墙、核心筒"], _LOC, "梁标高不一致")
    assert out
    joined = "".join(out)
    # 对象命中应体现在问题句中（对象名或定位）
    assert "梁" in joined or "③~⑤轴" in joined


@pytest.mark.unit
@pytest.mark.skipif(not _HAS_TEMPLATES, reason="question_templates.yaml 未就绪")
def test_question_carries_discipline_prefix():
    out = generate("ZH", [{"label": "做法", "reason": "x"}], [], _LOC, "做法不清")
    assert out
    assert any(q.startswith("[ZH") for q in out)


@pytest.mark.unit
def test_no_concern_template_fallback_is_safe():
    # 无 concern、无对象时不应抛异常（模板回退）
    out = generate("ZH", [], [], _LOC, "")
    assert isinstance(out, list)
