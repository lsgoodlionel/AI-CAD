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


# ── 按 GB/T 50001 §2 校订比例表（本轮）────────────────────

def test_seventy_five_is_not_a_standard_scale():
    """**修正记录**：旧表含 `75`，而 GB/T 50001 的常用表与可用表里都没有
    1:75 —— 那是凭印象加进去的。原文见
    `data/knowledge/drawing_standards/textbook-shitu-yusuan/book.md` 表 2-6。"""
    from services.scale_candidates import STANDARD_DENOMINATORS

    assert 75 not in STANDARD_DENOMINATORS


def test_common_scales_match_the_standard_table():
    """常用比例：1:1、1:2、1:5、1:10、1:20、1:50、1:100、1:150、
    1:200、1:500、1:1000、1:2000（两本教材的表逐项一致，互相印证）。"""
    from services.scale_candidates import COMMON_DENOMINATORS

    assert COMMON_DENOMINATORS == (1, 2, 5, 10, 20, 50, 100, 150,
                                   200, 500, 1000, 2000)


def test_detail_and_large_site_scales_are_now_recognised():
    """此前漏掉的两头：1:1~1:6 的详图档、1:60/80/600 与总图的大比例档。
    实测全库 2142 个变换里，这些「此前被判非标准」的有 105 个。"""
    for denominator in (1, 2, 3, 4, 6, 60, 80, 600, 5000, 25000):
        assert snap_to_standard(denominator) == (denominator, True)


def test_snapping_prefers_the_common_table():
    """GB/T 50001 §2 明文「应优先用表中常用比例」。"""
    assert snap_to_standard(98) == (100, True)      # 常用档
    assert snap_to_standard(78) == (80, True)       # 常用档不命中 → 可用档
    assert snap_to_standard(137)[1] is False        # 两档都不命中 → 不吸附
