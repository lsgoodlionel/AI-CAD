"""会审审查 — 定位与 concern 抽取测试"""
import pytest

from core.ai_review.review_audit import location_extractor, concern_extractor
from core.ai_review.review_audit.protocol_loader import (
    load_location_patterns, load_concern_keywords,
)

_HAS_PATTERNS = bool(load_location_patterns())
_HAS_CONCERNS = bool(load_concern_keywords())


@pytest.mark.unit
def test_location_extract_returns_all_keys():
    result = location_extractor.extract("任意文本")
    assert set(result) >= {"drawings", "levels", "axes", "nodes_or_systems", "spaces"}
    assert all(isinstance(v, list) for v in result.values())


@pytest.mark.unit
@pytest.mark.skipif(not _HAS_PATTERNS, reason="location_patterns.yaml 未就绪")
def test_location_extract_picks_axes_and_levels():
    text = "地下二层③~⑤轴梁标高，现平面图与剖面图不一致"
    result = location_extractor.extract(text)
    flat = "".join(sum(result.values(), []))
    # 至少抽到轴线或层位信息
    assert result["axes"] or result["levels"]
    assert flat  # 非空


@pytest.mark.unit
def test_location_extract_empty_text_is_safe():
    result = location_extractor.extract("")
    assert all(v == [] for v in result.values())


@pytest.mark.unit
@pytest.mark.skipif(not _HAS_CONCERNS, reason="concern_keywords.yaml 未就绪")
def test_concern_extract_hits_elevation_for_structure():
    concerns = concern_extractor.extract("JG", "梁标高在平面与剖面表达不一致")
    labels = [c["label"] for c in concerns]
    assert "标高" in labels
    # 每个 concern 都带 reason
    assert all(c.get("reason") for c in concerns)


@pytest.mark.unit
@pytest.mark.skipif(not _HAS_CONCERNS, reason="concern_keywords.yaml 未就绪")
def test_concern_extract_caps_at_three():
    concerns = concern_extractor.extract(
        "JDQ", "系统标高预留预埋管道做法节点尺寸全部存在问题"
    )
    assert len(concerns) <= 3


@pytest.mark.unit
def test_concern_extract_returns_list_of_dicts():
    concerns = concern_extractor.extract("ZH", "做法不清")
    assert isinstance(concerns, list)
    assert all(isinstance(c, dict) for c in concerns)
