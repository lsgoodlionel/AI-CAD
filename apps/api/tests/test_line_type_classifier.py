"""线型识别单测(划-空节奏 → CAD 线型 → 制图语义)。"""
from core.model3d.line_type_classifier import (
    DASH_DOT, DASH_DOT_DOT, DASHED, MIN_LINE_COVERAGE, SOLID, UNKNOWN, classify,
    classify_spans, dash_rhythm, purpose_of,
)


def _spans(pattern: list[float], start: float = 0.0) -> list[tuple[float, float]]:
    """按 [划,空,划,空,...] 生成沿线区间。"""
    out, pos = [], start
    for i, length in enumerate(pattern):
        if i % 2 == 0:
            out.append((round(pos, 2), round(pos + length, 2)))
        pos += length
    return out


#: 实测 A-01-02A 轴线的真实节奏:长划 10.5 / 短划 2.1 / 空 2.1
AXIS_PATTERN = [10.5, 2.1, 2.1, 2.1] * 8


# ── 节奏还原 ────────────────────────────────────────────────────

def test_dash_rhythm_recovers_lengths_and_gaps():
    r = dash_rhythm(_spans([10.5, 2.1, 2.1, 2.1, 10.5, 2.1]))
    assert r["dashes"][0] == 10.5
    assert r["gap_mean"] == 2.1


def test_dash_rhythm_clusters_long_and_short_dashes():
    r = dash_rhythm(_spans(AXIS_PATTERN))
    means = [c["mean"] for c in r["dash_classes"]]
    assert len(means) == 2                     # 恰好两类:长划与短划
    assert means[0] > means[1] * 2             # 长短比显著


def test_dash_rhythm_handles_unsorted_input():
    """碎段来自 PDF 时顺序不定,必须先排序。"""
    a = dash_rhythm([(0.0, 10.5), (14.7, 16.8), (12.6, 14.7)])
    b = dash_rhythm([(12.6, 14.7), (0.0, 10.5), (14.7, 16.8)])
    assert a["dashes"] == b["dashes"]


def test_dash_rhythm_on_empty_input():
    r = dash_rhythm([])
    assert r["dashes"] == [] and r["coverage"] == 0.0


# ── 线型判定 ────────────────────────────────────────────────────

def test_axis_dash_dot_recognized_from_real_rhythm():
    """实测轴线节奏必须被判为点划线——这是「按制图标准读」的核心。"""
    assert classify(dash_rhythm(_spans(AXIS_PATTERN))) == DASH_DOT


def test_outline_dash_dot_dot_recognized():
    """一长两短 = 双点划线 = 外轮廓;短划数约为长划的两倍。"""
    pattern = [12.0, 2.0, 2.0, 2.0, 2.0, 2.0] * 6      # 长,空,短,空,短,空
    assert classify(dash_rhythm(_spans(pattern))) == DASH_DOT_DOT


def test_solid_line_recognized_by_full_coverage():
    assert classify(dash_rhythm([(0.0, 2300.0)])) == SOLID


def test_solid_line_with_few_segments_still_solid():
    """图框可能被切成两三段,覆盖仍接近满。"""
    spans = [(0.0, 1150.0), (1150.0, 2300.0)]
    assert classify(dash_rhythm(spans)) == SOLID


def test_dashed_line_recognized_by_uniform_dashes():
    pattern = [6.0, 3.0] * 8                            # 等长划
    assert classify(dash_rhythm(_spans(pattern))) == DASHED


def test_near_equal_dashes_are_dashed_not_dash_dot():
    """长短差别不大就不算点划线,免得把虚线误判成轴线。"""
    pattern = [6.0, 3.0, 4.0, 3.0] * 6                  # 6 与 4 之比 <2
    assert classify(dash_rhythm(_spans(pattern))) == DASHED


def test_too_few_segments_without_coverage_is_unknown():
    """两三段又不满覆盖,不足以判节奏——宁可 unknown 也不猜。"""
    assert classify(dash_rhythm([(0.0, 5.0), (50.0, 55.0)])) == UNKNOWN


def test_classify_on_empty():
    assert classify({"dashes": []}) == UNKNOWN


# ── 制图语义 ────────────────────────────────────────────────────

def test_purpose_maps_line_type_to_drafting_meaning():
    assert purpose_of(DASH_DOT) == "轴线"
    assert purpose_of(DASH_DOT_DOT) == "外轮廓/用地界线"
    assert purpose_of(SOLID) == "构件轮廓/图框"
    assert purpose_of(DASHED) == "不可见轮廓"
    assert purpose_of("nonsense") == "未知"


def test_classify_spans_end_to_end():
    got = classify_spans(_spans(AXIS_PATTERN))
    assert got["line_type"] == DASH_DOT
    assert got["purpose"] == "轴线"
    assert got["rhythm"]["gap_mean"] == 2.1


# ── 覆盖率下限:挡掉描边文字 ──────────────────────────────────────

def test_stroke_drawn_text_is_not_mistaken_for_a_line():
    """图框会签栏的描边文字曾被判成双点长画线(外轮廓)。

    文字笔画恰好符合「两类长度 + 短划多于长划」,但沿包络的墨迹占比极低。
    实测:真双点长画线覆盖 0.20~0.43,文字只有 0.00~0.02。
    """
    # 用**实测的真实笔画长度**(1.14 / 0.65pt,来自圈内字形探针),
    # 在 2090pt 包络内散布——与图框会签栏实测的 35~112 段同量级
    spans = []
    pos = 0.0
    for i in range(40):
        length = 1.14 if i % 3 == 0 else 0.65
        spans.append((pos, pos + length))
        pos += 53.0
    got = classify_spans(spans)
    assert got["rhythm"]["coverage"] < MIN_LINE_COVERAGE
    assert got["line_type"] == UNKNOWN


def test_real_dash_dot_dot_coverage_still_passes():
    """实测真双点长画线覆盖 0.33~0.43,必须仍被认出来。"""
    pattern = [12.0, 2.0, 2.0, 2.0, 2.0, 2.0] * 6
    got = classify_spans(_spans(pattern))
    assert got["rhythm"]["coverage"] >= MIN_LINE_COVERAGE
    assert got["line_type"] == DASH_DOT_DOT


def test_axis_dash_dot_coverage_still_passes():
    got = classify_spans(_spans(AXIS_PATTERN))
    assert got["line_type"] == DASH_DOT
