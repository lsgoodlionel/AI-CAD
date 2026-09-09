"""环序修复纯函数测试。

背景：`geometry_extractor` 把 PDF 填充路径按**线段绘制顺序**累加两端点，
路径由多段拼成时该顺序不是环序。最坏情况矩形被画成两条对边，
点序构成 8 字形，鞋带面积恰好为 0 —— 而 `model_qto` 正是用它算混凝土量。
"""
import math

import pytest

from core.model3d.ring_order import (
    is_self_intersecting, polygon_area, repair_ring,
)

# 0.49×0.63m —— 实测 407 个零面积柱的轴对齐包围盒尺寸
W, H = 0.49, 0.63
_RECT_CCW = [(0.0, 0.0), (W, 0.0), (W, H), (0.0, H)]
#: 两条对边的绘制顺序：底边(A→B) + 顶边(D→C)。`path_points` 得 [A,B,D,C]
_RECT_BOWTIE = [(0.0, 0.0), (W, 0.0), (0.0, H), (W, H)]


def _bbox(ring):
    xs = [p[0] for p in ring]
    ys = [p[1] for p in ring]
    return min(xs), min(ys), max(xs), max(ys)


@pytest.mark.unit
def test_bowtie_rect_area_is_exactly_zero_before_repair():
    """先钉住缺陷本身：8 字形环的鞋带面积恰好为 0，而它并非退化图形。"""
    assert polygon_area(_RECT_BOWTIE) == 0.0
    assert _bbox(_RECT_BOWTIE) == (0.0, 0.0, W, H)   # 包围盒是正常柱尺寸


@pytest.mark.unit
def test_repair_makes_self_intersecting_ring_have_real_area():
    repaired = repair_ring(_RECT_BOWTIE)
    assert polygon_area(repaired) == pytest.approx(W * H)


@pytest.mark.unit
def test_repair_preserves_bounding_box():
    """历史教训：降点/重排都不得缩小真实范围（曾把 124 根柱存成碎条）。"""
    repaired = repair_ring(_RECT_BOWTIE)
    assert _bbox(repaired) == _bbox(_RECT_BOWTIE)
    assert len(repaired) == len(_RECT_BOWTIE)        # 不丢点（不是取凸包）


@pytest.mark.unit
def test_good_ring_returned_unchanged():
    """正常环一个点都不许动 —— 修复只针对坏环。"""
    assert repair_ring(_RECT_CCW) == _RECT_CCW


@pytest.mark.unit
def test_duplicated_vertices_ring_is_not_treated_as_broken():
    """`path_points` 逐线段两端点会产生重复顶点 [A,B,B,C,C,D,D,A]，
    这是**正常环**（相邻共点，不是自交），不得被判坏并重排。"""
    a, b, c, d = _RECT_CCW
    ring = [a, b, b, c, c, d, d, a]
    assert not is_self_intersecting(ring)
    assert repair_ring(ring) == ring
    assert polygon_area(ring) == pytest.approx(W * H)


@pytest.mark.unit
def test_concave_L_shape_is_preserved():
    """凹多边形（L 形角柱）不自交，必须原样保留 —— 取凸包会把面积抬高。"""
    ring = [(0.0, 0.0), (1.0, 0.0), (1.0, 0.4), (0.4, 0.4), (0.4, 1.0), (0.0, 1.0)]
    assert not is_self_intersecting(ring)
    assert repair_ring(ring) == ring
    assert polygon_area(ring) == pytest.approx(1.0 * 0.4 + 0.4 * 0.6)


@pytest.mark.unit
def test_bowtie_is_detected():
    assert is_self_intersecting(_RECT_BOWTIE)


@pytest.mark.unit
def test_repaired_ring_is_no_longer_self_intersecting():
    assert not is_self_intersecting(repair_ring(_RECT_BOWTIE))


@pytest.mark.unit
def test_collinear_points_stay_zero_area():
    """真退化（所有点共线）不是环序问题，修不出面积来，也不许假造。"""
    ring = [(0.0, 0.0), (1.0, 0.0), (2.0, 0.0), (3.0, 0.0)]
    assert polygon_area(repair_ring(ring)) == pytest.approx(0.0)


@pytest.mark.unit
@pytest.mark.parametrize("n", [0, 1, 2, 3])
def test_short_rings_do_not_raise(n):
    ring = [(float(i), float(i)) for i in range(n)]
    assert repair_ring(ring) == ring
    assert not is_self_intersecting(ring)


@pytest.mark.unit
def test_repair_handles_scrambled_octagon():
    """多点乱序也能恢复出真实面积（正八边形，交换两点造成打结）。

    注意乱序**不一定**丢面积：把正八边形按 (0,3,6,1,4,7,2,5) 重排得到的是
    星形多边形 {8/3}，鞋带面积与凸八边形恰好相同。取一个确实打结的排列。
    """
    pts = [(math.cos(k * math.pi / 4), math.sin(k * math.pi / 4)) for k in range(8)]
    expected = polygon_area(pts)
    scrambled = [pts[i] for i in (0, 1, 2, 3, 5, 4, 6, 7)]
    assert is_self_intersecting(scrambled)
    assert polygon_area(scrambled) < expected           # 打结确实丢面积
    repaired = repair_ring(scrambled)
    assert polygon_area(repaired) == pytest.approx(expected)
    assert not is_self_intersecting(repaired)


@pytest.mark.unit
def test_retraced_ring_is_repaired():
    """回描环（走出去又原路折回）：不穿越，但面积为 0 —— 也是坏环。

    实测改后仍剩 24 个零面积轮廓全是这一形态（`uniq_x=2, uniq_y=2` 却
    面积为 0），严格穿越判据碰不到它们。
    """
    a, b, c, d = _RECT_CCW
    ring = [a, b, a, d, c, d]                 # 出去又折回，鞋带正负相消
    assert not is_self_intersecting(ring)     # 确实没穿越
    assert polygon_area(ring) == 0.0
    repaired = repair_ring(ring)
    assert polygon_area(repaired) > 0.0
    assert _bbox(repaired) == _bbox(ring)


@pytest.mark.unit
def test_diagonally_collinear_points_are_not_falsely_repaired():
    """沿对角线共线：包围盒两向都有跨度，但这是**真退化**，重排也无面积。

    不许因此假造出面积来 —— 修复只是恢复环序，不是造几何。
    """
    ring = [(0.0, 0.0), (1.0, 1.0), (2.0, 2.0), (3.0, 3.0)]
    assert polygon_area(repair_ring(ring)) == pytest.approx(0.0)
