"""轴号圈识别单测。

实测依据(A-01-02A/03A/04A 三张 A0 定位图):轴号圈在 PDF 里是**一个独立 path、
恰 4 条贝塞尔弧、包围盒正方形**,且**图内直径完全均匀**(108/108 个 28.0pt)。
此前三次定位失败都因为在跨 path 聚类弧——圈本身就是一个完整 path。
"""
import math

import pytest

from core.model3d.axis_label_circle import (
    ARCS_PER_CIRCLE, STANDARD_DIAMETER_MM, assign_circles_to_axes,
    circle_candidates, circle_offsets, diameter_mm, dominant_diameter,
    find_circles, is_standard_diameter,
)


def _circle(cx: float, cy: float, d: float, kinds: str = "cccc") -> dict:
    """构造一个 path 记录(与 fitz get_drawings 的字段对齐)。"""
    r = d / 2
    return {"rect": (cx - r, cy - r, cx + r, cy + r), "kinds": list(kinds)}


# ── 候选筛选 ──────────────────────────────────────────────────

def test_four_arc_square_path_is_a_circle():
    got = circle_candidates([_circle(100.0, 200.0, 28.0)])
    assert len(got) == 1
    assert got[0]["cx"] == 100.0 and got[0]["cy"] == 200.0
    assert got[0]["diameter_pt"] == 28.0


def test_path_containing_a_line_is_rejected():
    """混入直线的 path 不是纯圆——可能是带引线的图例。"""
    assert circle_candidates([_circle(0, 0, 28.0, "cccl")]) == []


def test_non_square_bbox_is_rejected():
    """椭圆/圆弧段的包围盒不是方的。"""
    p = {"rect": (0.0, 0.0, 28.0, 14.0), "kinds": list("cccc")}
    assert circle_candidates([p]) == []


def test_wrong_arc_count_is_rejected():
    """实测三张图只有 4 弧一种形式,放宽弧数只会引噪声。"""
    assert circle_candidates([_circle(0, 0, 28.0, "cc")]) == []
    assert circle_candidates([_circle(0, 0, 28.0, "cccccc")]) == []
    assert ARCS_PER_CIRCLE == 4


def test_degenerate_rect_does_not_crash():
    assert circle_candidates([{"rect": (5.0, 5.0, 5.0, 5.0), "kinds": list("cccc")}]) == []


def test_empty_path_list():
    assert circle_candidates([]) == []


# ── 直径众数(不能硬编码全局常量)────────────────────────────────

def test_dominant_diameter_is_the_mode_not_the_mean():
    """A-01-04A 有 126 个 16.0pt + 3 个离群。均值会被离群拉偏,众数不会。"""
    circles = [_c(16.0) for _ in range(126)] + [_c(32.0), _c(29.6), _c(13.6)]
    assert dominant_diameter(circles) == 16.0


def _c(d: float) -> dict:
    return {"cx": 0.0, "cy": 0.0, "diameter_pt": d}


def test_dominant_diameter_tolerates_small_drift():
    """同一批圈实测 28.02,四舍五入前后要归为一类。"""
    circles = [_c(28.0), _c(28.02), _c(27.99), _c(16.0)]
    assert dominant_diameter(circles) == pytest.approx(28.0, abs=0.05)


def test_dominant_diameter_on_empty():
    assert dominant_diameter([]) == 0.0


def test_find_circles_drops_off_mode_diameters():
    paths = [_circle(i * 40.0, 0.0, 16.0) for i in range(10)]
    paths += [_circle(500.0, 0.0, 32.0)]          # 离群
    got = find_circles(paths)
    assert len(got["circles"]) == 10
    assert got["diameter_pt"] == 16.0
    assert got["dropped"] == 1


def test_find_circles_on_no_candidates():
    got = find_circles([])
    assert got["circles"] == [] and got["diameter_pt"] == 0.0


# ── §8.0.2 直径合规 ───────────────────────────────────────────

def test_diameter_mm_conversion():
    assert diameter_mm(28.0) == pytest.approx(9.88, abs=0.01)


def test_standard_diameter_range_is_8_to_10_mm():
    assert STANDARD_DIAMETER_MM == (8.0, 10.0)
    assert is_standard_diameter(28.0)              # 9.88mm,A-01-02A 实测
    assert not is_standard_diameter(16.0)          # 5.64mm,A-01-04A 实测,偏小


# ── 圈 → 轴线(§8.0.2 圆心在轴线延长线上)──────────────────────

def test_circle_offsets_use_the_same_normal_as_axes():
    """圈的法向偏移必须与轴线用同一套法向,否则配不上。

    45° 方向上,(10,10) 与 (20,20) 同在一条线上 → 偏移相同。
    """
    offs = circle_offsets([_at(10.0, 10.0), _at(20.0, 20.0)], 45.0)
    assert offs[0] == pytest.approx(offs[1], abs=1e-6)


def _at(cx: float, cy: float) -> dict:
    return {"cx": cx, "cy": cy, "diameter_pt": 28.0}


def test_circle_offset_is_perpendicular_distance():
    """0° 方向上,法向偏移就是 y 坐标。"""
    assert circle_offsets([_at(500.0, 137.0)], 0.0)[0] == pytest.approx(137.0)


def test_assign_matches_circle_to_the_axis_on_its_extension():
    axes = [{"offset_pt": 100.0}, {"offset_pt": 300.0}]
    got = assign_circles_to_axes([_at(0.0, 101.0)], axes, 0.0)
    assert got["confirmed"] == [0]                # 命中第 0 条轴线
    assert got["orphan_circles"] == []
    assert got["axes_without_circle"] == [1]


def test_orphan_circle_marks_a_recoverable_missed_axis():
    """圈找不到线 = 漏检的轴线,但位置和方向已知,可以直接补出来。"""
    got = assign_circles_to_axes([_at(0.0, 900.0)], [{"offset_pt": 100.0}], 0.0)
    assert got["orphan_circles"] == [0]
    assert got["confirmed"] == []


def test_axis_without_circle_is_a_false_positive_suspect():
    """线没有圈 = 疑似误检——旋转系过检 44 条正需要这个判据。"""
    axes = [{"offset_pt": float(i) * 200.0} for i in range(5)]
    got = assign_circles_to_axes([_at(0.0, 0.0)], axes, 0.0)
    assert got["axes_without_circle"] == [1, 2, 3, 4]


def test_two_circles_on_one_axis_count_once():
    """一条轴线两端各有一个圈(§8.0.2),不能重复计数。"""
    got = assign_circles_to_axes(
        [_at(0.0, 100.0), _at(2000.0, 100.5)], [{"offset_pt": 100.0}], 0.0)
    assert got["confirmed"] == [0]
    assert len(got["circles_per_axis"][0]) == 2


def test_assign_with_no_axes_makes_every_circle_an_orphan():
    got = assign_circles_to_axes([_at(0.0, 10.0), _at(0.0, 20.0)], [], 0.0)
    assert got["orphan_circles"] == [0, 1]


def test_assign_tolerance_is_tighter_than_axis_spacing():
    """容差必须远小于最小轴距(实测 4500mm→约 26pt),否则会串轴。"""
    axes = [{"offset_pt": 0.0}, {"offset_pt": 26.0}]
    got = assign_circles_to_axes([_at(0.0, 25.5)], axes, 0.0)
    assert got["confirmed"] == [1]                # 归到更近的那条


def test_rotated_family_assignment_works():
    """旋转系(42°)也要能配上——正交侥幸正确曾掩盖过法向 bug。"""
    angle = 42.0
    rad = math.radians(angle)
    # 沿 42° 方向、法向偏移 500 的一条线上取两点
    base = (-math.sin(rad) * 500.0, math.cos(rad) * 500.0)
    pts = [(base[0] + math.cos(rad) * t, base[1] + math.sin(rad) * t)
           for t in (0.0, 800.0)]
    got = assign_circles_to_axes([_at(*p) for p in pts],
                                 [{"offset_pt": 500.0}], angle)
    assert got["confirmed"] == [0]
    assert len(got["circles_per_axis"][0]) == 2
