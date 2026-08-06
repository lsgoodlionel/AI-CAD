"""轴号带识别单测。

**图面依据**(GB/T 50001 §8.0.2「编号宜注写在平面图**下方及左侧**」):
同一族轴线的轴号圈会排成一条**带**——横行标注竖向轴线,竖列标注横向轴线。
所以**带方向与它所标注的轴线方向垂直**。

**实测**(A-01-02A 108 个圈,容差 6pt):
    0° 带 24 个 → 分区 1 的 24 条数字轴线
    0° 带 12 个 → 分区 2 的 2-1~2-12
   90° 带 14 个 → 分区 2 的 14 条字母轴线
   42° 带 16 个 → 分区 3 的 16 条数字轴线
  132° 带 11 + 4 个 → 分区 3 的 15 条字母轴线(4 个是边界错台段)

带一出,轴线位置就**由圈直接给出**,不必再靠几何阈值猜哪条线是轴线。
"""
import math

import pytest

from core.model3d.axis_label_band import (
    BAND_TOLERANCE_RATIO, MIN_BAND_MEMBERS, axis_angle_of_band, bands_to_axes,
    detect_bands,
)


def _row(n: int, y: float, x0: float = 100.0, step: float = 50.0,
         d: float = 28.0) -> list[dict]:
    """横行:n 个圈等距排在 y 上 —— 标注竖向轴线。"""
    return [{"cx": x0 + i * step, "cy": y, "diameter_pt": d} for i in range(n)]


def _col(n: int, x: float, y0: float = 100.0, step: float = 50.0,
         d: float = 28.0) -> list[dict]:
    return [{"cx": x, "cy": y0 + i * step, "diameter_pt": d} for i in range(n)]


# ── 带方向 ↔ 轴线方向 ────────────────────────────────────────────

def test_band_direction_is_perpendicular_to_the_axes_it_labels():
    """横行(0°)标注的是竖向(90°)轴线 —— 搞反了整套配对全错。"""
    assert axis_angle_of_band(0.0) == 90.0
    assert axis_angle_of_band(90.0) == 0.0


def test_axis_angle_of_band_normalizes_to_0_180():
    assert axis_angle_of_band(42.0) == 132.0
    assert axis_angle_of_band(132.0) == 42.0


# ── 带检测 ────────────────────────────────────────────────────

def test_detects_a_horizontal_row_of_circles():
    bands = detect_bands(_row(24, 2178.0))
    assert len(bands) == 1
    assert bands[0]["member_count"] == 24
    assert bands[0]["band_angle_deg"] == 0.0
    assert bands[0]["axis_angle_deg"] == 90.0


def test_detects_row_and_column_separately():
    bands = detect_bands(_row(12, 1575.0) + _col(14, 500.0))
    assert sorted(b["member_count"] for b in bands) == [12, 14]


def test_band_split_by_offset_gap():
    """同方向但偏移相差很大 = 两条带(分区 1 与分区 2 的底部行)。"""
    bands = detect_bands(_row(24, 2178.0) + _row(12, 1575.0))
    assert sorted(b["member_count"] for b in bands) == [12, 24]


def test_tolerance_scales_with_circle_diameter():
    """容差按圈径比例定,才能同时适配 28pt 与 16pt 两种图。"""
    assert BAND_TOLERANCE_RATIO == pytest.approx(0.25)
    # 16pt 圈 → 容差 4pt:偏移差 3pt 仍算同带
    small = [{"cx": 0.0, "cy": 0.0, "diameter_pt": 16.0},
             {"cx": 50.0, "cy": 3.0, "diameter_pt": 16.0},
             {"cx": 100.0, "cy": 1.0, "diameter_pt": 16.0}]
    assert detect_bands(small)[0]["member_count"] == 3


def test_min_members_rejects_stray_pairs():
    """两个圈不构成带 —— 附加轴线常成对出现,不能当主带。"""
    assert MIN_BAND_MEMBERS == 3
    assert detect_bands(_row(2, 100.0)) == []


def test_each_circle_belongs_to_at_most_one_band():
    """同一批圈在不同方向上会偶然共线;必须按成员数贪心独占,否则重复计数。"""
    circles = _row(24, 2178.0) + _col(14, 500.0)
    bands = detect_bands(circles)
    claimed = [i for b in bands for i in b["members"]]
    assert len(claimed) == len(set(claimed))


def test_larger_band_wins_the_contested_circle():
    """行列交点处的圈归给成员更多的带(交点只有一个圈,不重复布点)。"""
    circles = _row(10, 300.0) + _col(4, 100.0, y0=300.0)[1:]   # (100,300) 已在行内
    bands = detect_bands(circles)
    biggest = max(bands, key=lambda b: b["member_count"])
    assert biggest["member_count"] == 10


def test_rotated_band_is_detected():
    """旋转分区(42°/132°)必须同样成带 —— 正交侥幸正确曾掩盖法向 bug。"""
    rad = math.radians(42.0)
    circles = [{"cx": math.cos(rad) * i * 60.0, "cy": math.sin(rad) * i * 60.0,
                "diameter_pt": 28.0} for i in range(16)]
    bands = detect_bands(circles, directions=(0.0, 42.0, 90.0, 132.0))
    assert bands[0]["member_count"] == 16
    assert bands[0]["axis_angle_deg"] == 132.0


def test_detect_bands_on_empty_input():
    assert detect_bands([]) == []


def test_band_records_span_for_diagnostics():
    bands = detect_bands(_row(5, 700.0, x0=100.0, step=100.0))
    b = bands[0]
    assert b["span_pt"] == pytest.approx(400.0)
    assert b["offset_pt"] == pytest.approx(700.0)


# ── 带 → 轴线 ─────────────────────────────────────────────────

def test_bands_to_axes_gives_one_axis_per_circle():
    """一个圈 = 一条轴线(§8.0.2)。24 个圈 → 24 条轴线。"""
    axes = bands_to_axes(detect_bands(_row(24, 2178.0)), page_h=2384.0)
    assert len(axes) == 24
    assert all(a["angle_deg"] == 90.0 for a in axes)


def test_axis_offsets_are_distinct_and_ordered():
    axes = bands_to_axes(detect_bands(_row(5, 700.0, x0=100.0, step=100.0)),
                         page_h=2384.0)
    offs = [a["offset_pt"] for a in axes]
    assert offs == sorted(offs)
    assert len(set(offs)) == 5


def test_axes_carry_source_and_band_ids_for_traceability():
    """来源必须可追溯 —— 圈锚定的轴线与几何猜出来的轴线不能混为一谈。

    band_ids 是复数:一条轴线两端的圈可能来自上下两条不同的带。
    """
    axes = bands_to_axes(detect_bands(_row(3, 700.0)), page_h=2384.0)
    assert all(a["source"] == "label_circle" for a in axes)
    assert all(a["band_ids"] for a in axes)


def test_axis_labelled_from_both_ends_records_both_bands():
    circles = _row(4, 200.0, x0=100.0, step=100.0) + \
              _row(4, 2100.0, x0=100.0, step=100.0)
    axes = bands_to_axes(detect_bands(circles), page_h=2384.0)
    assert all(len(a["band_ids"]) == 2 for a in axes)
    assert all(a["circle_count"] == 2 for a in axes)


def test_bands_to_axes_merges_circles_at_both_ends_of_one_axis():
    """一条轴线两端各一个圈(§8.0.2),不能算成两条轴线。"""
    circles = _row(4, 200.0, x0=100.0, step=100.0) + \
              _row(4, 2100.0, x0=100.0, step=100.0)      # 上下两行,同 4 条轴线
    axes = bands_to_axes(detect_bands(circles), page_h=2384.0)
    assert len(axes) == 4


def test_bands_to_axes_on_empty():
    assert bands_to_axes([], page_h=2384.0) == []
