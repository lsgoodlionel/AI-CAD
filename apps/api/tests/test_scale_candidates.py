"""比例尺候选提取单测(攻 drawing_transform 瓶颈,人审在环)。纯函数。"""
from services.scale_candidates import (
    assess_existing_scale,
    build_scale_candidates,
    extract_denominators,
    scale_of_denominator,
    snap_to_standard,
)


def test_extract_handles_both_colons_and_spacing():
    texts = ["比例 1:100", "详图1：50", "节点 1: 10", "无关文字"]
    assert sorted(extract_denominators(texts)) == [10, 50, 100]


def test_extract_filters_out_of_range():
    """越界值(如编号 1:3000)不作比例尺。"""
    assert extract_denominators(["1:3000", "1:2"]) == []


def test_scale_of_denominator_is_exact_physics():
    """1:100 → 0.03528 m/pt(1pt=25.4/72mm),与实测真值完全一致。"""
    assert abs(scale_of_denominator(100) - 0.03528) < 1e-5
    assert abs(scale_of_denominator(50) - 0.01764) < 1e-5


def test_snap_to_standard():
    assert snap_to_standard(98) == (100, True)      # OCR 抖动 → 吸附
    assert snap_to_standard(101) == (100, True)
    assert snap_to_standard(137)[1] is False        # 偏离过大 → 不吸附


def test_build_candidates_ranks_by_votes():
    """一图多比例尺(主图+详图)→ 全部列出交人裁决,不自动选。"""
    texts = ["1:100", "平面 1:100", "1:100", "节点 1:10", "节点 1:10"]
    cands = build_scale_candidates(texts)
    assert cands[0]["denominator"] == 100 and cands[0]["votes"] == 3
    assert cands[1]["denominator"] == 10 and cands[1]["votes"] == 2
    assert abs(cands[0]["share"] - 0.6) < 1e-6
    assert all(c["is_standard"] for c in cands)
    assert cands[0]["label"] == "1:100"


def test_build_candidates_empty():
    assert build_scale_candidates(["无比例尺"]) == []


def test_assess_existing_scale_detects_nonstandard():
    """现有变换质量评估:实测仅 46.1% 符合标准比例尺。"""
    good = assess_existing_scale(0.03528)          # 1:100
    assert good["is_standard"] is True
    assert good["nearest_standard"] == 100
    bad = assess_existing_scale(0.38889)           # 实测非标准值
    assert bad["is_standard"] is False


def test_assess_invalid_scale():
    assert assess_existing_scale(0)["denominator"] is None


def test_origin_from_axis_points():
    from services.scale_candidates import origin_from_axis_points
    ox, oy = origin_from_axis_points(
        [{"x": 300.0, "y": 800.0}, {"x": 120.0, "y": 500.0}], page_h=1000.0)
    assert ox == 120.0
    assert oy == 200.0        # page_h - max(y),与 pt_to_meter 同口径


def test_origin_without_axes_defaults_zero():
    from services.scale_candidates import origin_from_axis_points
    assert origin_from_axis_points([], page_h=1000.0) == (0.0, 0.0)


def test_build_confirmed_transform_is_exact_and_full_confidence():
    """人确认 → 精确换算 + 满置信(区别于算法估算的平均 0.007)。"""
    from services.scale_candidates import build_confirmed_transform
    t = build_confirmed_transform(100, [{"x": 120.0, "y": 800.0}], page_h=1000.0)
    assert abs(t["scale_m_pt"] - 0.03528) < 1e-5
    assert t["origin_x"] == 120.0
    assert t["confidence"] == 1.0
    assert t["source"] == "human_confirmed_scale"


def test_build_confirmed_transform_guards():
    from services.scale_candidates import build_confirmed_transform
    assert build_confirmed_transform(0, [], 1000.0) is None
    assert build_confirmed_transform(100, [], 0) is None
