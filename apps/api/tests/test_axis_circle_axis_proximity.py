"""轴号圈必须**贴着轴线** —— 排除被当成轴号圈的圆形构件(J6)。

**实测缺陷**:「58 基础底板换撑平面布置图」报出 **862 个圈 / 434 条轴线**,
而真正的轴网定位图 A-01-02A 只有 108 圈 / 99 条轴线。
读判据发现圈检测与带聚类**两级都是纯几何**:

| 阶段 | 现有判据 | 缺什么 |
|---|---|---|
| `circle_candidates` | 贝塞尔四段圆、包围盒方形、直径区间 | **不看圈是否连着轴线** |
| `axis_label_band` | 共线容差、成员数、沿带间隔 | 不看带在图上什么位置 |

⇒ 基坑图上规则排列的立柱桩、钢立柱**必然**被当成轴号带。

**国标依据**:GB/T 50001 **§8.0.2「定位轴线应用细单点长画线绘制…
编号注写在轴线端部的圆内…**圆心应在定位轴线的延长线上**」**
⇒ 轴线画到圈心附近，而桩是孤立的圆，圆内没有线。

**两条被证伪的路(不要重走)**:

1. **距图幅边缘** —— 方向正好相反:真轴号圈距边中位 **0.219**、
   基坑图桩 **0.105**(46% 的桩距边 <8%)。因为轴号圈画在**图形**边缘，
   而图幅外还有图框、标题栏、留白。
2. **「圈内有字符」** —— E3-0 审计已证这批图**矢量文字取不到**
   (Phase I 按 §8.0.3 推导轴号正因如此)，加字符判据会把所有圈都排除。

**阈值 0.30r 的实测依据**(三张真值图 + 两张误检图):

| 图 | ≤0.20r | **≤0.30r** |
|---|---:|---:|
| A-01-02A(真值 108) | 56.5% | **100.0%** |
| A-01-03A(真值 107) | 75.7% | **100.0%** |
| A-01-04A(真值 126) | 58.7% | **100.0%** |
| 基坑图(桩) | 15.3% | 29.7% |
| 围护体图 | 14.9% | 46.0% |

真值图在 0.30 处**全部跳到 100%**，误检图仅 30~46% —— 分界尖锐，
不是勉强凑出来的阈值。
"""
from __future__ import annotations

import pytest

from core.model3d.axis_label_circle import (
    AXIS_PROXIMITY_MAX_RATIO, filter_circles_near_axes,
)


def _circle(cx: float, cy: float, diameter_pt: float = 28.0) -> dict:
    return {"cx": cx, "cy": cy, "diameter_pt": diameter_pt}


@pytest.mark.unit
def test_circle_with_a_line_ending_at_its_centre_is_kept():
    """**核心用例**:§8.0.2 圆心在轴线延长线上 ⇒ 线端点落在圈心附近。"""
    got = filter_circles_near_axes([_circle(100, 100)], [(100.0, 100.0)])
    assert len(got) == 1


@pytest.mark.unit
def test_isolated_circle_is_dropped():
    """桩是孤立的圆,圆内没有线 —— 最近端点在圈外。"""
    got = filter_circles_near_axes([_circle(100, 100)], [(500.0, 500.0)])
    assert got == []


@pytest.mark.unit
def test_line_touching_only_the_rim_is_dropped():
    """线只碰到圈**边缘**(1.0r)不算 —— 桩的实测中位正是 0.88r。"""
    got = filter_circles_near_axes([_circle(100, 100, 28.0)], [(114.0, 100.0)])
    assert got == []


@pytest.mark.unit
def test_threshold_boundary_is_inclusive():
    """恰在阈值上保留 —— 三张真值图正是在 0.30r 处跳到 100%。"""
    radius = 14.0
    offset = radius * AXIS_PROXIMITY_MAX_RATIO
    got = filter_circles_near_axes([_circle(100, 100, 28.0)],
                                   [(100.0 + offset, 100.0)])
    assert len(got) == 1


@pytest.mark.unit
def test_just_past_the_threshold_is_dropped():
    radius = 14.0
    offset = radius * AXIS_PROXIMITY_MAX_RATIO * 1.2
    got = filter_circles_near_axes([_circle(100, 100, 28.0)],
                                   [(100.0 + offset, 100.0)])
    assert got == []


@pytest.mark.unit
def test_scales_with_circle_diameter():
    """判据按**半径的比例**,不是绝对距离 —— 实测圈径 16.0~28.0pt 都要适配。"""
    small, large = _circle(100, 100, 16.0), _circle(300, 300, 28.0)
    offset_ok = 14.0 * AXIS_PROXIMITY_MAX_RATIO
    got = filter_circles_near_axes(
        [small, large], [(100.0 + offset_ok, 100.0), (300.0 + offset_ok, 300.0)])
    # 同样的绝对偏移，对小圈超标、对大圈刚好
    assert [c["cx"] for c in got] == [300]


@pytest.mark.unit
def test_no_endpoints_keeps_everything():
    """**取不到线段时不做过滤** —— 判不出就不判,不能把整张图清空。"""
    circles = [_circle(100, 100), _circle(200, 200)]
    assert filter_circles_near_axes(circles, []) == circles
    assert filter_circles_near_axes(circles, None) == circles


@pytest.mark.unit
def test_empty_circles_is_safe():
    assert filter_circles_near_axes([], [(1.0, 1.0)]) == []
    assert filter_circles_near_axes(None, [(1.0, 1.0)]) == []


@pytest.mark.unit
def test_order_is_preserved():
    """保持原顺序 —— 下游的带聚类依赖稳定顺序。"""
    circles = [_circle(100, 100), _circle(200, 200), _circle(300, 300)]
    got = filter_circles_near_axes(
        circles, [(100.0, 100.0), (200.0, 200.0), (300.0, 300.0)])
    assert [c["cx"] for c in got] == [100, 200, 300]


@pytest.mark.unit
def test_degenerate_diameter_does_not_divide_by_zero():
    got = filter_circles_near_axes([_circle(100, 100, 0.0)], [(100.0, 100.0)])
    assert isinstance(got, list)


@pytest.mark.unit
def test_a_line_passing_through_the_circle_also_counts_as_adjacent():
    """轴线**穿过**圈而不在圈处终止时，圈也该保留。

    §8.0.2 说的是「圆心应在定位轴线的**延长线**上」——判据是圆心到
    **轴线**的距离，而实现只查到**线段端点**的距离。轴线若穿过圈继续
    向外延伸，圈附近就没有端点，于是真轴号圈被当成孤立圆丢掉。

    **实测**（首层框架梁平面整体配筋图）：数字带原始候选 14 个圈
    （x = 542…2522，直径全为 20.0pt），过此闸后只剩 12 个 ——
    丢的正是 ⑦（x=1470）与 ⑪（x=2088），而它们身上恰好有轴线穿过。
    后果不止漏两条：轴号按顺序推导，⑦ 一丢，其后编号**整体偏移**。
    """
    from core.model3d.axis_label_circle import filter_circles_near_axes

    circle = {"cx": 1470.0, "cy": 227.0, "diameter_pt": 20.0}
    # 一条竖直轴线从圈上方穿到图纸深处，端点都离圈很远
    endpoints = [(1470.0, 60.0), (1470.0, 1600.0)]
    assert len(filter_circles_near_axes([circle], endpoints)) == 1


@pytest.mark.unit
def test_an_isolated_circle_far_from_any_line_is_still_dropped():
    """孤立的圆（桩、钢立柱）照旧丢掉——放宽判据不能把这条护栏冲掉。"""
    from core.model3d.axis_label_circle import filter_circles_near_axes

    circle = {"cx": 1470.0, "cy": 227.0, "diameter_pt": 20.0}
    endpoints = [(300.0, 900.0), (400.0, 900.0)]
    assert filter_circles_near_axes([circle], endpoints) == []
