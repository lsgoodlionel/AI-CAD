"""多边形真实尺寸（最小面积外接矩形）—— 判据本身的单元测试。

实测来源与判据依据见 `core/model3d/true_extent.py` 模块文档。
"""
import math

import pytest

from core.model3d.true_extent import convex_hull, min_area_rect


def _rotate(points, deg):
    a = math.radians(deg)
    ca, sa = math.cos(a), math.sin(a)
    return [(x * ca - y * sa, x * sa + y * ca) for x, y in points]


def _aabb_aspect(points):
    xs = [p[0] for p in points]; ys = [p[1] for p in points]
    w, h = max(xs) - min(xs), max(ys) - min(ys)
    return max(w, h) / max(min(w, h), 1e-9)


def test_axis_aligned_square():
    """轴对齐正方形：真实尺寸就是边长，与包围盒一致。"""
    long_m, short_m = min_area_rect([(0, 0), (2, 0), (2, 2), (0, 2)])
    assert long_m == pytest.approx(2.0, abs=1e-6)
    assert short_m == pytest.approx(2.0, abs=1e-6)


def test_rotated_square_keeps_its_side():
    """**菱形姿态的柱仍是柱**（`gold/CRITERIA.md#columns` 明列）。

    45° 旋转后包围盒边长涨到 √2 倍，真实边长不变 —— 判据换成真实尺寸
    之后，旋转柱反而比原来更不容易被尺寸窗口挤出去。
    """
    square = [(-1, -1), (1, -1), (1, 1), (-1, 1)]
    long_m, short_m = min_area_rect(_rotate(square, 45))
    assert long_m == pytest.approx(2.0, abs=1e-6)
    assert short_m == pytest.approx(2.0, abs=1e-6)


def test_diagonal_slender_bar_is_the_case_that_matters():
    """斜细条：**包围盒近方形，真实形状细长** —— 这正是尺寸判据分不开
    标高符号笔画的原因（`col_sheet/quad4.png` 实测形态）。"""
    bar = [(0, 0), (10, 0), (10, 1), (0, 1)]
    tilted = _rotate(bar, 45)
    assert _aabb_aspect(tilted) < 1.3, "斜 45° 的细条，包围盒本来就近方形"
    long_m, short_m = min_area_rect(tilted)
    assert long_m / short_m == pytest.approx(10.0, rel=1e-3)


def test_result_is_rotation_invariant():
    """真实尺寸不随图纸摆放角度漂移 —— 包围盒会。"""
    shape = [(0, 0), (3, 0), (3, 1), (0, 1)]
    base = min_area_rect(shape)
    for deg in (7, 30, 45, 63, 90, 137):
        got = min_area_rect(_rotate(shape, deg))
        assert got[0] == pytest.approx(base[0], rel=1e-6)
        assert got[1] == pytest.approx(base[1], rel=1e-6)


def test_duplicated_endpoints_do_not_change_the_answer():
    """**三角形是由 3 条线段画的**，端点两两重复共 6 个点。

    `geometry_extractor._collect_pdf_drawings` 把每条线段的两个端点都塞进
    `path_points`，所以填充三角形进到识别器时点数恒为 6 —— 判据不能被
    这个编码细节影响（2026-08-28 的「三顶点多边形占 0%」正是栽在这里）。
    """
    tri = [(0, 0), (4, 0), (4, 0), (0, 3), (0, 3), (0, 0)]
    assert min_area_rect(tri) == pytest.approx(min_area_rect([(0, 0), (4, 0), (0, 3)]))


def test_degenerate_inputs_return_none():
    """点不足或共线 → None。**由调用方决定怎么办**，这里不替它做判断。"""
    assert min_area_rect([]) is None
    assert min_area_rect([(0, 0), (1, 1)]) is None
    assert min_area_rect([(0, 0), (1, 1), (2, 2), (3, 3)]) is None


def test_convex_hull_drops_interior_points():
    """凸包只保留外轮廓点 —— 内部点不影响真实尺寸。"""
    hull = convex_hull([(0, 0), (4, 0), (4, 4), (0, 4), (2, 2), (1, 3)])
    assert set(hull) == {(0, 0), (4, 0), (4, 4), (0, 4)}


def test_self_intersecting_point_order_still_measures_the_shape():
    """**点序自交不得影响结果**。

    `path_points` 按绘制顺序堆点，直接对它求面积会在自交（蝴蝶结）时
    算出近 0 —— 实测 33% 的候选面积比 <0.05，而肉眼看它们是实心的。
    最小外接矩形走凸包，与点序无关。
    """
    bowtie = [(0, 0), (2, 2), (2, 0), (0, 2)]
    long_m, short_m = min_area_rect(bowtie)
    assert long_m == pytest.approx(2.0, abs=1e-6)
    assert short_m == pytest.approx(2.0, abs=1e-6)
