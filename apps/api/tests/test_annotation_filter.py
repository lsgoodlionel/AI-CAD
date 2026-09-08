"""图面标注/非实体过滤器的用例。

分两层：
1. **几何原语**（凸包 / 最小面积外接矩形 / 真实角点 / 多边形面积）——
   纯函数，先于任何判据存在，普查脚本也用它们量数。
2. **判据**（`find_annotation_flags`）—— 阈值来自实测，见模块文档的实测表。
"""
from __future__ import annotations

import math

import pytest

from core.model3d.annotation_filter import (
    DEFAULT_MAX_CORNERS,
    DEFAULT_MIN_SHORT_M,
    DEFAULT_MIN_THIN_ASPECT,
    convex_hull,
    corner_points,
    find_annotation_flags,
    min_area_rect,
    polygon_area,
)


# --------------------------------------------------------------------------
# 1. 几何原语
# --------------------------------------------------------------------------

def test_convex_hull_drops_interior_points():
    # Arrange：正方形四角 + 一个内点
    pts = [(0, 0), (2, 0), (2, 2), (0, 2), (1, 1)]

    # Act
    hull = convex_hull(pts)

    # Assert
    assert len(hull) == 4
    assert (1, 1) not in hull


def test_convex_hull_of_two_points_is_the_segment():
    assert len(convex_hull([(0.0, 0.0), (1.0, 1.0)])) == 2


def test_min_area_rect_of_axis_aligned_rectangle_is_its_own_size():
    long_side, short_side = min_area_rect([(0, 0), (4, 0), (4, 1), (0, 1)])

    assert long_side == pytest.approx(4.0, abs=1e-6)
    assert short_side == pytest.approx(1.0, abs=1e-6)


def test_min_area_rect_sees_through_a_rotated_thin_bar():
    """45° 斜放的细条：轴对齐包围盒近正方形，真实范围仍是细条。

    这是标高符号 `∨` 的一条斜笔画进入柱候选的方式（见模块文档）。
    """
    # Arrange：长 1.0、宽 0.1 的细条，绕原点转 45°
    half_l, half_w = 0.5, 0.05
    ang = math.radians(45)
    local = [(-half_l, -half_w), (half_l, -half_w), (half_l, half_w), (-half_l, half_w)]
    pts = [(x * math.cos(ang) - y * math.sin(ang),
            x * math.sin(ang) + y * math.cos(ang)) for x, y in local]

    # Act
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    aabb_aspect = (max(xs) - min(xs)) / (max(ys) - min(ys))
    long_side, short_side = min_area_rect(pts)

    # Assert：包围盒看着是方的，真实范围是 10:1 的细条
    assert aabb_aspect == pytest.approx(1.0, abs=0.05)
    assert long_side == pytest.approx(1.0, abs=1e-6)
    assert short_side == pytest.approx(0.1, abs=1e-6)


def test_min_area_rect_of_degenerate_input_is_zero():
    assert min_area_rect([(0.0, 0.0)]) == (0.0, 0.0)
    assert min_area_rect([]) == (0.0, 0.0)


def test_corner_points_removes_duplicates_and_collinear_points():
    """描边编码会把一条边的端点重复收进来 —— 角点数要量形状，不是量点数。"""
    # Arrange：一个三角形，每条边中点被额外收了一次，首尾点重复
    pts = [(0, 0), (1, 0), (2, 0), (1, 1), (0, 0)]

    # Act
    corners = corner_points(pts)

    # Assert
    assert len(corners) == 3


def test_corner_points_keeps_a_square_at_four():
    assert len(corner_points([(0, 0), (1, 0), (1, 1), (0, 1)])) == 4


def test_polygon_area_is_orientation_independent():
    square = [(0, 0), (1, 0), (1, 1), (0, 1)]

    assert polygon_area(square) == pytest.approx(1.0)
    assert polygon_area(list(reversed(square))) == pytest.approx(1.0)


# --------------------------------------------------------------------------
# 2. 判据
# --------------------------------------------------------------------------

def _bar(cx, cy, length, width, ang_deg):
    """以 (cx,cy) 为中心、旋转 ang_deg 的细条多边形。"""
    ang = math.radians(ang_deg)
    hl, hw = length / 2, width / 2
    local = [(-hl, -hw), (hl, -hw), (hl, hw), (-hl, hw)]
    return [[cx + x * math.cos(ang) - y * math.sin(ang),
             cy + x * math.sin(ang) + y * math.cos(ang)] for x, y in local]


def _square(cx, cy, side):
    h = side / 2
    return [[cx - h, cy - h], [cx + h, cy - h], [cx + h, cy + h], [cx - h, cy + h]]


def test_a_slanted_thin_stroke_is_flagged_as_annotation():
    """引线/尺寸斜线那类细笔画：真实短边 0.07m、长宽比 10。"""
    elements = [{"outline": _bar(0, 0, 0.7, 0.07, 40)}]

    flags = find_annotation_flags(elements)

    assert flags[0] is not None
    assert flags[0].reason == "thin_stroke"


def test_a_real_column_section_is_not_flagged():
    """0.6m 见方的柱截面 —— 判据必须放过它。"""
    flags = find_annotation_flags([{"outline": _square(0, 0, 0.6)}])

    assert flags[0] is None


def test_a_small_but_square_column_is_not_flagged():
    """0.2m 的小柱：短边小，但不是细条（长宽比 1）。"""
    flags = find_annotation_flags([{"outline": _square(0, 0, 0.2)}])

    assert flags[0] is None


def _star(n=60):
    """n 点星形轮廓，尺寸落在柱窗口正中 —— 代笔画/格线那类多角点轮廓。"""
    pts = []
    for i in range(n):
        ang = 2 * math.pi * i / n
        r = 0.25 if i % 2 else 0.15
        pts.append([r * math.cos(ang), r * math.sin(ang)])
    return pts


def test_a_dense_stroke_polygon_is_flagged_when_the_raw_outline_is_supplied():
    """汉字笔画：角点极多。真柱 4~6 个角点。"""
    raw = _star()

    flags = find_annotation_flags([{"outline": raw}], raw_outlines=[raw])

    assert flags[0] is not None
    assert flags[0].reason == "stroke_cluster"
    assert flags[0].corners > DEFAULT_MAX_CORNERS


def test_the_corner_criterion_cannot_fire_on_a_downsampled_outline():
    """构件字典里的轮廓被 `_downsample_ring` 压到 8 点 —— 实测 0/2497 命中。

    这一条钉住的是**判据的适用边界**，不是它的能力：不传原始多边形就不判，
    别让它看起来判过。
    """
    downsampled = _star()[:8]

    assert find_annotation_flags([{"outline": downsampled}]) == [None]


def test_a_star_outline_is_not_flagged_without_raw_outlines():
    star = _star()

    assert find_annotation_flags([{"outline": star}]) == [None]


def test_flags_are_none_for_degenerate_outlines():
    flags = find_annotation_flags([{"outline": []}, {"outline": [[0, 0], [1, 1]]}, {}])

    assert flags == [None, None, None]


def test_input_is_not_mutated():
    elements = [{"outline": _bar(0, 0, 0.7, 0.10, 40)}]
    before = [dict(e) for e in elements]

    find_annotation_flags(elements)

    assert elements == before


def test_thresholds_are_overridable_for_calibration():
    """阈值必须能被调用方压过 —— 回测脚本要扫阈值。"""
    element = {"outline": _bar(0, 0, 0.7, 0.07, 40)}

    assert find_annotation_flags([element], min_short_m=0.01) == [None]
    assert find_annotation_flags([element], min_short_m=DEFAULT_MIN_SHORT_M)[0] is not None


def test_a_squat_candidate_survives_any_scale_error():
    """比例错 10 倍时，真柱的**米数**全错，长宽比不变 —— 判据必须靠后者。

    识别器允许的最扁的柱是 8:1（`_COLUMN_LAYER_MAX_ASPECT`）。
    这里造一个 8:1 的扁柱，把尺寸缩到荒谬的毫米级（模拟比例错），
    判据仍须放过它。全库比例只有 30% 站得住，这条不是假设。
    """
    squat = {"outline": _bar(0, 0, 0.8 / 10, 0.1 / 10, 30)}   # 0.08×0.01m，8:1

    assert find_annotation_flags([squat]) == [None]


def test_flag_carries_the_measured_numbers():
    """不静默：判据要能解释自己为什么删。"""
    flag = find_annotation_flags([{"outline": _bar(0, 0, 0.7, 0.05, 40)}])[0]

    assert flag.short_m == pytest.approx(0.05, abs=1e-3)
    assert flag.long_m == pytest.approx(0.70, abs=1e-3)
    assert "0.700" in flag.detail and "0.050" in flag.detail


def test_a_measured_elevation_mark_glyph_is_not_flagged():
    """实测边界：标高符号 `∨` 整体进来是 0.32×0.32m 的近方形，本判据管不了。

    钉死这条**是为了不让边界悄悄漂移** —— 谁要是把阈值放宽到能删它，
    就会同时删掉同尺寸的真柱。金标准复跑里那 8 张「标注」图命中率 0.0%，
    根因就在这里。
    """
    glyph = {"outline": [[0, 0], [0.32, 0], [0.16, 0.32], [0, 0.32]]}

    assert find_annotation_flags([glyph]) == [None]


def test_default_corner_cap_is_far_above_a_real_column():
    """默认角点上限必须远高于真柱（4~6），否则会误删。"""
    assert DEFAULT_MAX_CORNERS >= 12


def test_default_aspect_floor_is_at_least_the_recognisers_own_column_limit():
    """长宽比下限不得低于识别器允许的最扁的柱（8:1），否则比例一错就误删。"""
    from core.model3d.element_recognizer import _COLUMN_LAYER_MAX_ASPECT

    assert DEFAULT_MIN_THIN_ASPECT >= _COLUMN_LAYER_MAX_ASPECT


def test_a_measured_elevation_mark_stroke_from_the_census_is_flagged():
    """普查里量到的真实一行：包围盒 0.601×0.529m，真实范围 0.792×0.012m。

    包围盒落在柱窗口（0.2~1.5m）正中，尺寸判据分不开；真实短边 12 毫米。
    """
    # Arrange：0.792×0.012 的细条，斜 42°（还原出那一行的包围盒）
    outline = _bar(0, 0, 0.792, 0.012, 42)
    xs = [p[0] for p in outline]
    ys = [p[1] for p in outline]

    # Act
    flag = find_annotation_flags([{"outline": outline}])[0]

    # Assert：包围盒确实像一根柱，判据仍然认得出它是细条
    assert max(xs) - min(xs) == pytest.approx(0.60, abs=0.03)
    assert max(ys) - min(ys) == pytest.approx(0.54, abs=0.03)
    assert flag is not None and flag.reason == "thin_stroke"
    assert flag.short_m == pytest.approx(0.012, abs=1e-3)
