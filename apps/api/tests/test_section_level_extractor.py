"""剖面标高线抽取测试（B-02）。

合成 DrawingGeometry 喂给抽取器（绕开渲染，确定性可复现，对齐详设 §7.1）：
标高线 y(pt) 与其应绑定的标高值 (elevation_m) 成对给出。
"""
import pytest

from core.model3d.section_level_extractor import (
    LevelMark,
    SectionLevels,
    extract_section_levels,
    filter_main_sequence,
)
from core.model3d.types import DrawingGeometry


def _make_section_geom(
    tie_points: list[tuple[float, float]],
    *,
    with_lines: bool = True,
) -> DrawingGeometry:
    """构造带标高线 + 标高文本的剖面几何。

    tie_points: [(y_pt, elevation_m)] —— 标高线 y 坐标与应绑定的标高值。
    """
    lines: list[tuple[float, float, float, float]] = []
    texts: list[tuple[float, float, str]] = []
    for y_pt, elevation in tie_points:
        if with_lines:
            lines.append((50.0, y_pt, 400.0, y_pt))
        texts.append((410.0, y_pt, f"{elevation:+.3f}".replace("+0.000", "±0.000")))
    return DrawingGeometry(page_w=500, page_h=800, lines=lines, texts=texts)


# ── 基本抽取 ────────────────────────────────────────────────────

@pytest.mark.unit
def test_extracts_ordered_levels_from_section():
    geom = _make_section_geom([(700, 0.0), (500, 4.2), (300, 8.4), (100, 12.6)])
    result = extract_section_levels(geom)

    assert isinstance(result, SectionLevels)
    assert result.reason is None
    values = [round(mark.elevation_m, 3) for mark in result.marks]
    assert values == [0.0, 4.2, 8.4, 12.6]  # 升序
    assert all(isinstance(mark, LevelMark) for mark in result.marks)


@pytest.mark.unit
def test_level_mark_carries_label_and_source_ref():
    geom = _make_section_geom([(700, 0.0), (500, 4.2)])
    result = extract_section_levels(geom)

    datum = result.marks[0]
    assert datum.elevation_m == pytest.approx(0.0)
    assert datum.label == "±0.000"
    assert "y_pt" in datum.source_ref
    assert datum.source_ref["y_pt"] == pytest.approx(700.0)


@pytest.mark.unit
def test_negative_basement_elevation_parsed():
    geom = _make_section_geom([(760, -3.6), (600, 0.0), (400, 4.2)])
    result = extract_section_levels(geom)
    values = [round(m.elevation_m, 3) for m in result.marks]
    assert values == [-3.6, 0.0, 4.2]


@pytest.mark.unit
def test_duplicate_elevation_deduped_keeping_highest_confidence():
    geom = _make_section_geom([(700, 0.0), (701, 0.0), (500, 4.2)])
    result = extract_section_levels(geom)
    values = [round(m.elevation_m, 3) for m in result.marks]
    assert values == [0.0, 4.2]


# ── 置信度与拟合 ────────────────────────────────────────────────

@pytest.mark.unit
def test_bound_marks_have_higher_confidence_than_textonly():
    bound = extract_section_levels(_make_section_geom([(700, 0.0), (500, 4.2)]))
    textonly = extract_section_levels(
        _make_section_geom([(700, 0.0), (500, 4.2)], with_lines=False)
    )
    assert bound.marks[0].confidence > textonly.marks[0].confidence


@pytest.mark.unit
def test_fit_slope_is_negative_for_valid_section():
    """页面 y 向下、标高向上 → 拟合斜率 elevation/y_pt 必为负。"""
    geom = _make_section_geom([(700, 0.0), (500, 4.2), (300, 8.4)])
    result = extract_section_levels(geom)
    assert result.fit["slope_m_per_pt"] < 0
    assert result.fit["tie_point_count"] == 3
    assert result.fit["residual"] == pytest.approx(0.0, abs=1e-6)


@pytest.mark.unit
def test_noisy_mark_lowers_fit_quality_confidence():
    clean = extract_section_levels(_make_section_geom([(700, 0.0), (500, 4.2), (300, 8.4)]))
    # 打乱一个点的 y 使其偏离线性
    noisy = extract_section_levels(_make_section_geom([(700, 0.0), (560, 4.2), (300, 8.4)]))
    assert noisy.fit["residual"] > clean.fit["residual"]
    assert min(m.confidence for m in noisy.marks) < min(m.confidence for m in clean.marks)


# ── 降级 / 边界 ─────────────────────────────────────────────────

@pytest.mark.unit
def test_no_elevation_text_returns_empty_with_reason():
    geom = DrawingGeometry(page_w=500, page_h=800, lines=[(50, 300, 400, 300)])
    result = extract_section_levels(geom)
    assert result.marks == ()
    assert result.reason == "no_elevation_text"


@pytest.mark.unit
def test_out_of_range_elevation_ignored():
    """超合理范围（>300m / <-30m）的数字不计入标高。"""
    geom = DrawingGeometry(
        page_w=500,
        page_h=800,
        texts=[(410, 700, "999.000"), (410, 500, "4.200")],
        lines=[(50, 700, 400, 700), (50, 500, 400, 500)],
    )
    result = extract_section_levels(geom)
    values = [round(m.elevation_m, 3) for m in result.marks]
    assert values == [4.2]


@pytest.mark.unit
def test_single_mark_flagged_low_fit_quality():
    geom = _make_section_geom([(700, 0.0)])
    result = extract_section_levels(geom)
    assert len(result.marks) == 1
    assert result.fit["tie_point_count"] == 1
    # 单点无法拟合线性映射 → 置信度受限
    assert result.marks[0].confidence <= 0.6


@pytest.mark.unit
def test_empty_geometry_returns_reason():
    result = extract_section_levels(DrawingGeometry())
    assert result.marks == ()
    assert result.reason == "no_elevation_text"


# ── 阶段A 鲁棒筛标高：filter_main_sequence（女儿墙/设备夹层噪声）────────────


def _mark(elevation_m: float, confidence: float = 0.9) -> LevelMark:
    return LevelMark(elevation_m=elevation_m, label=f"{elevation_m:+.3f}", confidence=confidence, source_ref={})


@pytest.mark.unit
def test_filter_main_sequence_drops_close_gap_keeping_higher_confidence():
    """屋面 12.6 + 女儿墙 13.0（间距 0.4m < 2.8m 噪声阈）：置信度更高者留存。"""
    marks = [_mark(0.0), _mark(3.6), _mark(12.6, confidence=0.6), _mark(13.0, confidence=0.95)]
    result = filter_main_sequence(marks)
    values = [m.elevation_m for m in result]
    assert values == [0.0, 3.6, 13.0]


@pytest.mark.unit
def test_filter_main_sequence_ties_keep_lower_elevation():
    """同置信度：保留靠前（更低标高）者，丢弃紧随其后的噪声标高。"""
    marks = [_mark(0.0), _mark(3.6), _mark(7.2), _mark(7.6)]  # 7.2/7.6 间距 0.4
    result = filter_main_sequence(marks)
    values = [m.elevation_m for m in result]
    assert values == [0.0, 3.6, 7.2]


@pytest.mark.unit
def test_filter_main_sequence_preserves_uniform_real_gaps():
    marks = [_mark(0.0), _mark(3.6), _mark(7.2), _mark(10.8)]
    result = filter_main_sequence(marks)
    assert [m.elevation_m for m in result] == [0.0, 3.6, 7.2, 10.8]


@pytest.mark.unit
def test_filter_main_sequence_stops_at_two_marks_even_if_close():
    """只剩 2 个标高时不再判定"过近"（样本太少），交由下游门槛把关。"""
    marks = [_mark(0.0), _mark(0.5)]
    result = filter_main_sequence(marks)
    assert [m.elevation_m for m in result] == [0.0, 0.5]


@pytest.mark.unit
def test_filter_main_sequence_iterates_multiple_noise_clusters():
    """多处噪声簇（女儿墙 + 设备夹层）需迭代多轮才能全部剔除。"""
    marks = [
        _mark(0.0), _mark(3.6),
        _mark(6.9, confidence=0.5), _mark(7.2, confidence=0.9),  # 设备夹层噪声（0.3m）
        _mark(10.8),
        _mark(13.9, confidence=0.5), _mark(14.2, confidence=0.9),  # 女儿墙噪声（0.3m）
    ]
    result = filter_main_sequence(marks)
    values = [m.elevation_m for m in result]
    assert values == [0.0, 3.6, 7.2, 10.8, 14.2]


@pytest.mark.unit
def test_extract_section_levels_filters_noise_end_to_end():
    """端到端：抽取器输出已过滤女儿墙噪声（14.2 附近 0.4m 内的噪声点被剔除）。"""
    geom = _make_section_geom(
        [(900, 0.0), (700, 3.6), (500, 7.2), (300, 10.8), (100, 11.2)]
    )
    result = extract_section_levels(geom)
    values = [round(m.elevation_m, 3) for m in result.marks]
    assert values == [0.0, 3.6, 7.2, 10.8]
