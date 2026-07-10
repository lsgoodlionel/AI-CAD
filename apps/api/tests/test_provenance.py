"""z 恢复置信度与降级框架测试（B-11）。

统一来源优先级、estimated 标记、降级 note 文案与择优决策，
供 B-02~B-10 各提取器/配准器一致标注「实测/估算」。
"""
import pytest

from core.model3d.provenance import (
    MEASURED_SOURCES,
    SOURCE_PRIORITY,
    Provenance,
    build_provenance,
    choose_by_priority,
    is_measured,
)


# ── 来源优先级 ─────────────────────────────────────────────────

@pytest.mark.unit
def test_source_priority_ordering():
    assert SOURCE_PRIORITY["section"] > SOURCE_PRIORITY["registered"] - 2
    assert SOURCE_PRIORITY["registered"] > SOURCE_PRIORITY["elevation"]
    assert SOURCE_PRIORITY["elevation"] > SOURCE_PRIORITY["estimated"]
    assert SOURCE_PRIORITY["estimated"] > SOURCE_PRIORITY["default"]


@pytest.mark.unit
def test_measured_sources_exclude_estimated_and_default():
    assert "section" in MEASURED_SOURCES
    assert "registered" in MEASURED_SOURCES
    assert "estimated" not in MEASURED_SOURCES
    assert "default" not in MEASURED_SOURCES


# ── build_provenance ───────────────────────────────────────────

@pytest.mark.unit
def test_build_measured_has_no_note_not_estimated():
    prov = build_provenance("section", confidence=0.9)
    assert prov.source == "section"
    assert prov.estimated is False
    assert prov.note == ""
    assert prov.confidence == pytest.approx(0.9)


@pytest.mark.unit
def test_build_default_is_estimated_with_note():
    prov = build_provenance(
        "default", quantity_label="层高", default_value=4.5, unit="m"
    )
    assert prov.estimated is True
    assert "层高" in prov.note
    assert "4.5" in prov.note
    assert prov.confidence <= 0.3


@pytest.mark.unit
def test_unknown_source_coerced_to_default():
    prov = build_provenance("garbage")
    assert prov.source == "default"
    assert prov.estimated is True


@pytest.mark.unit
def test_evidence_ref_preserved():
    prov = build_provenance("section", evidence_ref={"drawing_id": "d1", "residual": 0.02})
    assert prov.evidence_ref["drawing_id"] == "d1"


# ── choose_by_priority ─────────────────────────────────────────

@pytest.mark.unit
def test_choose_prefers_higher_source_priority():
    section = build_provenance("section", confidence=0.7)
    default = build_provenance("default", confidence=0.99)
    chosen = choose_by_priority([default, section])
    assert chosen.source == "section"  # 优先级压过高置信度的默认


@pytest.mark.unit
def test_choose_uses_confidence_as_tiebreak():
    low = build_provenance("elevation", confidence=0.6)
    high = build_provenance("elevation", confidence=0.8)
    assert choose_by_priority([low, high]).confidence == pytest.approx(0.8)


@pytest.mark.unit
def test_choose_empty_returns_none():
    assert choose_by_priority([]) is None


@pytest.mark.unit
def test_is_measured_helper():
    assert is_measured("section") is True
    assert is_measured("default") is False


@pytest.mark.unit
def test_provenance_as_dict_roundtrip():
    prov = build_provenance("section", confidence=0.9, evidence_ref={"k": "v"})
    data = prov.as_dict()
    assert data["source"] == "section"
    assert data["estimated"] is False
    assert data["evidence_ref"] == {"k": "v"}
