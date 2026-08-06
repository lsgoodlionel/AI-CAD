"""极坐标轴网单测(A-01-03A 中心轴网定位图)。

**为什么直线带模型不适用**:实测 A-01-03A 的 107 个轴号圈里,直线带只吃掉 66 个。
渲图后看清原因——这是**放射轴网**:放射线汇聚于一点,轴号圈绕中心**等角排布**
(外围 95 个,角间距中位 4.3°,开头连续 7 个都是 4.4°)。

**正确的模型**:角度扮演直线带里「沿带位置」的角色,半径扮演「法向偏移」。
同一角度上不同半径的两个圈,是**同一条径向轴线**的两个标注点
——正如直线轴线可以两端各注一个圈(§8.0.2)。

**圆心怎么定**:用「同角度圈对」计分。正确圆心下,大量圈对的角度差接近 0
(实测角间距序列里出现连片的 0.0°);圆心偏了,这种一致性立刻散掉。
"""
import math

import pytest

from core.model3d.axis_polar_band import (
    ANGULAR_TOLERANCE_DEG, angular_pair_score, detect_polar_bands,
    estimate_polar_center, polar_axes,
)

CENTER = (1680.0, 1080.0)


def _at(angle_deg: float, radius: float, center=CENTER) -> dict:
    rad = math.radians(angle_deg)
    return {"cx": center[0] + math.cos(rad) * radius,
            "cy": center[1] + math.sin(rad) * radius,
            "diameter_pt": 28.0}


def _ring(n: int, radius: float, step: float = 4.4) -> list[dict]:
    """等角排布的一圈轴号圈。"""
    return [_at(i * step, radius) for i in range(n)]


# ── 圆心估计 ──────────────────────────────────────────────────

def test_coverage_rules_out_a_runaway_center():
    """同角度圈对数在「圆心趋于无穷远」处有退化极大值 —— 必须用角覆盖挡住。

    实测不加这条时搜索会跑到页外 (3501, -95):圆心越远,所有圈的角度越挤,
    配对数越多。直线上的点从任何位置看角覆盖 ≤180°,放射轴网接近 360°。
    """
    circles = _ring(80, 900.0)
    assert angular_pair_score(circles, (9000.0, 0.0)) == 0


def test_pair_score_peaks_at_the_true_center():
    """正确圆心下同角度圈对最多 —— 这就是定心的判据。"""
    circles = _ring(80, 900.0) + _ring(80, 1200.0)   # 每条径向轴线两个圈
    good = angular_pair_score(circles, CENTER)
    off = angular_pair_score(circles, (CENTER[0] + 300.0, CENTER[1]))
    assert good > off


def test_estimates_the_center_from_circles_alone():
    circles = _ring(80, 900.0) + _ring(80, 1200.0)
    got = estimate_polar_center(circles, page_w=3370.0, page_h=2384.0)
    assert math.dist(got, CENTER) < 40.0


def test_center_estimate_on_too_few_circles():
    assert estimate_polar_center([_at(0.0, 100.0)], page_w=100.0,
                                 page_h=100.0) is None


def test_center_estimate_on_a_linear_layout_is_rejected():
    """正交轴网不该被当成放射轴网 —— 同角度圈对稀少时应返回 None。"""
    linear = [{"cx": 100.0 + i * 60.0, "cy": 2000.0, "diameter_pt": 28.0}
              for i in range(24)]
    got = estimate_polar_center(linear, page_w=3370.0, page_h=2384.0)
    assert got is None


# ── 等角分组 ──────────────────────────────────────────────────

def test_groups_circles_at_the_same_angle_into_one_axis():
    """同角度不同半径 = 同一条径向轴线的两个标注点(§8.0.2 两端各注一个)。"""
    circles = [_at(30.0, 900.0), _at(30.0, 1200.0), _at(60.0, 900.0)]
    got = detect_polar_bands(circles, CENTER)
    assert len(got) == 2
    two = next(g for g in got if len(g["circles"]) == 2)
    assert two["angle_deg"] == pytest.approx(30.0, abs=0.5)


def test_angular_tolerance_is_much_smaller_than_the_step():
    """容差必须远小于角间距(实测 4.4°),否则会把相邻轴线并成一条。"""
    assert ANGULAR_TOLERANCE_DEG < 4.4 / 2


def test_adjacent_axes_stay_separate():
    circles = _ring(8, 900.0)          # 间距 4.4°
    assert len(detect_polar_bands(circles, CENTER)) == 8


def test_wraparound_at_zero_degrees_is_handled():
    """359° 与 1° 只差 2°,不能因为跨 0 就被拆成两条。"""
    circles = [_at(359.5, 900.0), _at(0.2, 900.0)]
    assert len(detect_polar_bands(circles, CENTER)) == 1


def test_ignores_circles_too_close_to_the_center():
    """贴着圆心的圈角度极不稳定 —— 半径太小就不参与定向。"""
    circles = _ring(6, 900.0) + [_at(0.0, 5.0)]
    assert len(detect_polar_bands(circles, CENTER)) == 6


def test_detect_on_empty():
    assert detect_polar_bands([], CENTER) == []


def test_detect_without_center():
    assert detect_polar_bands(_ring(6, 900.0), None) == []


# ── 径向轴线 ──────────────────────────────────────────────────

def test_polar_axes_carry_direction_and_center():
    """径向轴线的身份是**角度**;下游要靠它+圆心还原直线。"""
    got = polar_axes(detect_polar_bands(_ring(6, 900.0), CENTER), CENTER)
    assert len(got) == 6
    assert all(a["kind"] == "radial" for a in got)
    assert all(a["center"] == CENTER for a in got)


def test_polar_axes_are_ordered_by_angle():
    """§8.0.3「依次注写」在放射轴网里就是**按角度依次**。"""
    got = polar_axes(detect_polar_bands(_ring(6, 900.0), CENTER), CENTER)
    angles = [a["angle_deg"] for a in got]
    assert angles == sorted(angles)


def test_polar_axes_record_how_many_circles_support_them():
    circles = [_at(30.0, 900.0), _at(30.0, 1200.0), _at(60.0, 900.0)]
    got = polar_axes(detect_polar_bands(circles, CENTER), CENTER)
    assert sorted(a["circle_count"] for a in got) == [1, 2]


def test_polar_axes_on_empty():
    assert polar_axes([], CENTER) == []


def test_refinement_beats_the_grid_plateau():
    """网格搜索有平台效应(实测粗搜后仍偏 79.6pt);
    同一射线上两个圈连成的直线必过圆心,最小二乘可精确定心。
    """
    from core.model3d.axis_polar_band import refine_center_by_ray_lines

    circles = _ring(80, 900.0) + _ring(80, 1200.0)
    got = refine_center_by_ray_lines(circles, (1600.0, 1070.0))
    assert math.dist(got, CENTER) < 1.0


def test_refinement_needs_at_least_two_rays():
    from core.model3d.axis_polar_band import refine_center_by_ray_lines

    assert refine_center_by_ray_lines([_at(0.0, 900.0), _at(0.0, 1200.0)],
                                      CENTER) is None


# ── 由同心弧定心(首选方法)────────────────────────────────────────

def test_arc_based_center_is_the_accurate_one():
    """同心弧定心实测距真值 1.2pt,而规律度搜索偏 67pt。

    两步都要做对:跨 path 平滑追链 + 拟合前抽稀。
    """
    from core.model3d.axis_polar_band import polar_center_from_arcs

    segs = []
    for radius in (300.0, 500.0, 700.0, 900.0):
        pts = [(CENTER[0] + math.cos(math.radians(i * 2.0)) * radius,
                CENTER[1] + math.sin(math.radians(i * 2.0)) * radius)
               for i in range(120)]
        segs += [(pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1])
                 for i in range(len(pts) - 1)]
    got = polar_center_from_arcs(segs)
    assert got is not None
    assert math.dist(got["center"], CENTER) < 2.0
    assert got["arcs"] == 4


def test_arc_center_returns_none_without_concentric_arcs():
    """正交轴网没有同心弧族 —— 返回 None,不硬给一个圆心。"""
    from core.model3d.axis_polar_band import polar_center_from_arcs

    grid = [(100.0, y, 2000.0, y) for y in range(200, 900, 60)]
    assert polar_center_from_arcs(grid) is None


def test_arc_center_on_empty():
    from core.model3d.axis_polar_band import polar_center_from_arcs

    assert polar_center_from_arcs([]) is None
