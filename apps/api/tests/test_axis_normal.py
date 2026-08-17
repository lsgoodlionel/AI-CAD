"""法向偏移单测 —— 钉住那个曾经错过的符号。

该公式此前在三处各写了一遍(`vector_axis_extractor`、`axis_label_circle`、
`services/axis_geometry`),其中两处逐字重复。用过 `(sinθ, cosθ)` 当法向,
它与方向向量 `(cosθ, sinθ)` 的点积是 `sin2θ`——**只在 0°/90° 为零**,
于是斜向轴线上同一条线的碎段被算出完全不同的偏移,永远聚不成一条,
实测导致整套 42°/132° 旋转分区被判成「斜撑构件」。

现在只有一份实现,这些断言就是它的防线。
"""
import math

import pytest

from core.model3d.axis_normal import along_offset, normal_offset, normal_vector

#: 覆盖正交与斜向;45° 与 90° 是历史 bug 的分界(旧公式只在 0°/90° 正确)
ANGLES = (0.0, 30.0, 42.0, 45.0, 90.0, 132.0, 175.0)


def test_normal_is_perpendicular_to_direction_at_every_angle():
    """**核心断言**:法向与方向向量点积必须恒为 0。

    旧公式 (sinθ,cosθ) 的点积是 sin2θ,在 42°/132° 上接近 1,正是它错的地方。
    """
    for angle in ANGLES:
        rad = math.radians(angle)
        dx, dy = math.cos(rad), math.sin(rad)
        nx, ny = normal_vector(angle)
        assert dx * nx + dy * ny == pytest.approx(0.0, abs=1e-12), f"{angle}°"


def test_points_on_the_same_line_share_one_offset():
    """同一条线上任取两点,偏移必须相同 —— 这才能把碎段聚成一条轴线。"""
    for angle in ANGLES:
        rad = math.radians(angle)
        base = (137.0, -42.0)
        p1 = (base[0] + math.cos(rad) * 5.0, base[1] + math.sin(rad) * 5.0)
        p2 = (base[0] + math.cos(rad) * 900.0, base[1] + math.sin(rad) * 900.0)
        assert normal_offset(*p1, angle) == pytest.approx(
            normal_offset(*p2, angle), abs=1e-9), f"{angle}°"


def test_offset_degenerates_to_minus_x_for_vertical_axes():
    """90° 轴线的偏移退化为 -x —— 轴号递增⇔偏移递减这条规律依赖它。"""
    assert normal_offset(992.0, 2178.0, 90.0) == pytest.approx(-992.0)


def test_offset_degenerates_to_y_for_horizontal_axes():
    assert normal_offset(992.0, 2178.0, 0.0) == pytest.approx(2178.0)


def test_parallel_lines_have_different_offsets():
    """平行但不重合的两条线必须区分开,否则相邻轴线会被并成一条。"""
    for angle in ANGLES:
        nx, ny = normal_vector(angle)
        a = normal_offset(0.0, 0.0, angle)
        b = normal_offset(nx * 50.0, ny * 50.0, angle)
        assert abs(b - a) == pytest.approx(50.0, abs=1e-9), f"{angle}°"


def test_offset_is_signed_and_flips_across_the_line():
    nx, ny = normal_vector(42.0)
    assert normal_offset(nx * 10.0, ny * 10.0, 42.0) > 0
    assert normal_offset(-nx * 10.0, -ny * 10.0, 42.0) < 0


def test_along_offset_is_orthogonal_to_normal_offset():
    """沿向与法向构成正交坐标系:沿向移动不改变法向偏移。"""
    for angle in ANGLES:
        rad = math.radians(angle)
        moved = (math.cos(rad) * 300.0, math.sin(rad) * 300.0)
        assert normal_offset(*moved, angle) == pytest.approx(0.0, abs=1e-9)
        assert along_offset(*moved, angle) == pytest.approx(300.0, abs=1e-9)


def test_angle_is_periodic_by_360_not_180_for_sign():
    """偏移随 180° 翻符号 —— 所以跨方向比较无意义,只能同向内比。"""
    assert normal_offset(100.0, 50.0, 0.0) == pytest.approx(
        -normal_offset(100.0, 50.0, 180.0))


def test_all_callers_share_the_single_implementation():
    """三处调用点必须指向同一实现,不能再各写一遍。"""
    from core.model3d import axis_label_circle, vector_axis_extractor

    assert vector_axis_extractor._normal_offset is normal_offset
    # axis_label_circle 通过 circle_offsets 使用,验证结果一致即可
    got = axis_label_circle.circle_offsets(
        [{"cx": 992.0, "cy": 2178.0, "diameter_pt": 28.0}], 42.0)
    assert got[0] == pytest.approx(normal_offset(992.0, 2178.0, 42.0))


def test_services_axis_geometry_delegates_here():
    from services.axis_geometry import axis_offset

    ref = {"x1_norm": 0.3, "y1_norm": 0.7, "x2_norm": 0.9, "y2_norm": 0.7}
    assert axis_offset(ref) == pytest.approx(round(normal_offset(0.3, 0.7, 0.0), 6))
