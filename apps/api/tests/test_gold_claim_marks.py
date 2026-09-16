"""「系统读数」类的标记几何：标高框、轴线段、坐标十字。

这三类问的是「系统在这里读出的东西，图上真有吗」，所以**标记画错位就等于
量自己的 bug**：判读者看到的是空白，只会答「没有」，而系统读数其实没问题。
"""
from __future__ import annotations

import pytest

from core.model3d.gold.claim_marks import (
    LEGACY_LONG_SIDE, anchor_mark, archive_bbox_to_page, axis_segment, elevation_mark,
)


@pytest.mark.unit
def test_archive_bbox_is_scaled_from_the_legacy_2160_canvas():
    # 档案 OCR 的位置量在「最长边 2160」的画布上；3370 宽的页面要乘 1.56
    box = archive_bbox_to_page((100.0, 200.0, 140.0, 210.0), page_w=3370.0, page_h=2384.0)
    k = 3370.0 / LEGACY_LONG_SIDE
    assert box == pytest.approx((100 * k, 200 * k, 140 * k, 210 * k))
    assert elevation_mark((100.0, 200.0, 140.0, 210.0),
                          page_w=3370.0, page_h=2384.0).shape == "box"


@pytest.mark.unit
def test_archive_bbox_survives_a_missing_page_size():
    box = (1.0, 2.0, 3.0, 4.0)
    assert archive_bbox_to_page(box, page_w=0.0, page_h=0.0) == box


@pytest.mark.unit
def test_axis_segment_is_clipped_to_the_page_and_drawn_dashed():
    mark = axis_segment(0.0, 500.0, page_w=800.0, page_h=1000.0, at=0.5, span_pt=200.0)
    assert mark is not None
    (ax, ay), (bx, by) = mark.line
    assert (ay, by) == (500.0, 500.0), "θ=0 是一条 y=offset 的水平线"
    assert (ax, bx) == pytest.approx((300.0, 500.0)), "只画中间 200pt 的一段"
    # 实线会把底下的点划线完全盖住，而线型正是判轴线的依据
    assert mark.shape == "dashed_line"


@pytest.mark.unit
def test_vertical_axis_uses_the_single_normal_convention():
    # `axes_to_scene` 的约定：θ≈90° 时 x = -offset
    mark = axis_segment(90.0, -300.0, page_w=800.0, page_h=1000.0, at=0.0, span_pt=100.0)
    assert mark is not None
    (ax, _ay), (bx, _by) = mark.line
    assert (ax, bx) == pytest.approx((300.0, 300.0))


@pytest.mark.unit
def test_axis_outside_the_page_is_not_pretended_to_be_drawn():
    assert axis_segment(90.0, -3000.0, page_w=800.0, page_h=1000.0) is None
    assert axis_segment(0.0, 5000.0, page_w=800.0, page_h=1000.0) is None


@pytest.mark.unit
def test_anchor_divides_both_coordinates_by_the_page_height():
    """锚点的 x_norm/y_norm **同除页高**（axis_world_anchors.py）。

    按页宽还原时，实测 35 个锚点里 16 个（x_norm>1）被甩到页面外，
    整格看不见十字 —— 判读者会把它当成空白格，而错的是我的还原。
    """
    page_w, page_h = 3399.0, 2412.0
    mark = anchor_mark(1.201, 0.906, page_h=page_h)
    cx = (mark.bbox[0] + mark.bbox[2]) / 2
    cy = (mark.bbox[1] + mark.bbox[3]) / 2
    assert (cx, cy) == pytest.approx((1.201 * page_h, 0.906 * page_h))
    assert 0 < cx < page_w and 0 < cy < page_h, "x_norm>1 在横向页面上仍在页内"
    assert mark.shape == "cross"
