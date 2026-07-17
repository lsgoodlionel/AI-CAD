"""
services/drawing_trace.py 单测 — 图纸正向追溯(Phase G1)

model_usage_from_scene:从 scene 统计某图生成的构件(按楼层/类别)。纯函数离线可测。
"""
from services.drawing_trace import model_usage_from_scene


def _scene():
    return {
        "generated_at": "2026-07-16",
        "floors": [
            {"key": "F1", "label": "一层", "elements": {
                "columns": [{"src": "dA"}, {"src": "dA"}, {"src": "dB"}],
                "walls": [{"src": "dA"}],
                "slabs": [{"src": "dB"}],
                "pipes": [], "beams": [], "equipment": [],
            }},
            {"key": "F2", "label": "二层", "elements": {
                "columns": [{"src": "dA"}],
                "walls": [], "slabs": [], "pipes": [], "beams": [], "equipment": [],
            }},
        ],
    }


def test_usage_counts_by_floor_and_kind_for_drawing():
    usage = model_usage_from_scene(_scene(), "dA")
    assert usage["used"] is True
    assert usage["total_elements"] == 4  # F1: 2柱+1墙, F2: 1柱
    floors = {f["key"]: f for f in usage["floors"]}
    assert floors["F1"]["by_kind"]["columns"] == 2
    assert floors["F1"]["by_kind"]["walls"] == 1
    assert floors["F2"]["by_kind"]["columns"] == 1
    # 该图没进 slabs
    assert "slabs" not in floors["F1"]["by_kind"]


def test_usage_for_unused_drawing():
    usage = model_usage_from_scene(_scene(), "dZ")
    assert usage["used"] is False
    assert usage["total_elements"] == 0
    assert usage["floors"] == []


def test_usage_empty_scene():
    usage = model_usage_from_scene({}, "dA")
    assert usage["used"] is False
    assert usage["total_elements"] == 0
