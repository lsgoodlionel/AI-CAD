"""候选轴线抽取单测(供人工「直接选图上那条线」)。"""
from core.model3d.axis_line_detector import (
    build_candidates, classify_line, detect_axis_line_candidates,
    filter_by_span, merge_collinear,
)


def test_classify_line_separates_vertical_horizontal_and_skew():
    assert classify_line(0.5, 0.1, 0.5, 0.9) == "x"      # 竖向轴线
    assert classify_line(0.1, 0.4, 0.9, 0.4) == "y"      # 横向轴线
    assert classify_line(0.1, 0.1, 0.9, 0.9) is None     # 斜线不作候选
    assert classify_line(0.5, 0.5, 0.5, 0.5) is None     # 退化点


def test_classify_line_tolerates_slight_skew_within_tolerance():
    assert classify_line(0.500, 0.1, 0.505, 0.9) == "x"


def test_merge_collinear_joins_dashed_segments_into_one_axis():
    # 点划线轴线被检成三段碎线,应并回一条,跨度取包络
    dashed = [
        {"direction": "x", "x1_norm": 0.4, "y1_norm": 0.10, "x2_norm": 0.4, "y2_norm": 0.30},
        {"direction": "x", "x1_norm": 0.401, "y1_norm": 0.35, "x2_norm": 0.401, "y2_norm": 0.60},
        {"direction": "x", "x1_norm": 0.399, "y1_norm": 0.65, "x2_norm": 0.399, "y2_norm": 0.90},
    ]
    merged = merge_collinear(dashed)
    assert len(merged) == 1
    assert merged[0]["y1_norm"] == 0.10
    assert merged[0]["y2_norm"] == 0.90
    assert abs(merged[0]["x1_norm"] - 0.4) < 0.002


def test_merge_collinear_keeps_distinct_axes_apart():
    lines = [
        {"direction": "x", "x1_norm": 0.2, "y1_norm": 0, "x2_norm": 0.2, "y2_norm": 1},
        {"direction": "x", "x1_norm": 0.6, "y1_norm": 0, "x2_norm": 0.6, "y2_norm": 1},
        {"direction": "y", "x1_norm": 0, "y1_norm": 0.3, "x2_norm": 1, "y2_norm": 0.3},
    ]
    merged = merge_collinear(lines)
    assert len(merged) == 3


def test_filter_by_span_drops_short_annotation_lines():
    lines = [
        {"direction": "x", "x1_norm": 0.5, "y1_norm": 0.0, "x2_norm": 0.5, "y2_norm": 0.9},
        {"direction": "x", "x1_norm": 0.7, "y1_norm": 0.5, "x2_norm": 0.7, "y2_norm": 0.55},
    ]
    kept = filter_by_span(lines, min_span=0.25)
    assert len(kept) == 1
    assert kept[0]["x1_norm"] == 0.5


def test_build_candidates_end_to_end_from_raw_segments():
    raw = [
        (0.3, 0.05, 0.3, 0.50),      # 竖向轴线上半段
        (0.3, 0.55, 0.3, 0.95),      # 竖向轴线下半段(点划线)
        (0.05, 0.4, 0.95, 0.4),      # 横向轴线
        (0.1, 0.1, 0.9, 0.85),       # 斜线 → 丢弃
        (0.8, 0.5, 0.8, 0.53),       # 短标注线 → 丢弃
    ]
    cands = build_candidates(raw)
    assert len(cands) == 2
    assert {c["direction"] for c in cands} == {"x", "y"}
    vertical = next(c for c in cands if c["direction"] == "x")
    assert vertical["y1_norm"] == 0.05 and vertical["y2_norm"] == 0.95


def test_build_candidates_respects_limit_longest_first():
    raw = [(i / 100, 0.0, i / 100, 0.3 + i / 200) for i in range(30)]
    cands = build_candidates(raw, limit=5)
    assert len(cands) == 5
    spans = [abs(c["y2_norm"] - c["y1_norm"]) for c in cands]
    assert spans == sorted(spans, reverse=True)   # 长者优先


def test_detect_axis_line_candidates_degrades_gracefully_on_bad_pdf():
    assert detect_axis_line_candidates(b"not a pdf") == []
