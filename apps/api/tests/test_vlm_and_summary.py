"""
Phase F1:VLM→档案条目转换 + 扫描摘要构建单测。
"""
from core.model3d.vlm_read.types import (
    ComponentCandidate,
    DisciplineCandidate,
    ElevationCandidate,
    VlmReadResult,
)
from services.drawing_info_extractor import build_scan_summary, items_from_vlm


def test_items_from_vlm_maps_all_kinds():
    vlm = VlmReadResult(
        backend="ollama",
        model="qwen3.5",
        discipline=DisciplineCandidate(value="structure", confidence=0.8, evidence="桩基"),
        elevations=(ElevationCandidate(value_m=-2.35, confidence=0.7, evidence="±0.00"),),
        components=(ComponentCandidate(label="钢立柱", confidence=0.6, evidence=""),),
    )
    items = items_from_vlm(vlm)
    by_cat = {it["category"] for it in items}
    assert "discipline" in by_cat
    assert "elevation" in by_cat
    assert any(it["extractor"] == "vlm" for it in items)
    elev = next(it for it in items if it["category"] == "elevation")
    assert elev["value_json"] == {"elevation_m": -2.35}
    assert elev["extractor"] == "vlm"


def test_items_from_vlm_none_backend_empty():
    assert items_from_vlm(VlmReadResult(backend="none")) == []


def test_build_scan_summary_counts_by_category_and_extractor():
    items = [
        {"category": "elevation", "extractor": "ocr", "content": "-2.350"},
        {"category": "elevation", "extractor": "vlm", "content": "-2.35"},
        {"category": "axis", "extractor": "vector_text", "content": "1"},
        {"category": "note", "extractor": "ocr", "content": "本图尺寸以毫米计说明文字很长"},
    ]
    summary = build_scan_summary(items, vlm_backend="ollama")
    assert summary["by_category"]["elevation"] == 2
    assert summary["by_category"]["axis"] == 1
    assert summary["by_extractor"]["ocr"] == 2
    assert summary["by_extractor"]["vlm"] == 1
    assert summary["by_extractor"]["vector_text"] == 1
    assert summary["total"] == 4
    assert summary["vlm_backend"] == "ollama"
    # 内容样例(短、去重、限量)
    assert isinstance(summary["samples"], list)
    assert len(summary["samples"]) <= 8


def test_build_scan_summary_empty():
    s = build_scan_summary([], vlm_backend="none")
    assert s["total"] == 0
    assert s["by_category"] == {}
