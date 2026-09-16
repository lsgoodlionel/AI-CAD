"""合理性分析的几何核。复用既有实现，只测**这一层新增**的与边界行为。"""
from __future__ import annotations

import pytest

from core.model3d.plausibility import geometry as geo

SQUARE = [(0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (0.0, 2.0)]
BOWTIE = [(0.0, 0.0), (2.0, 2.0), (2.0, 0.0), (0.0, 2.0)]


@pytest.mark.unit
def test_bowtie_area_cancels_to_zero_and_is_detected_as_self_intersecting():
    """自交轮廓的鞋带面积正负相消 —— 存量 31.1% 的柱因此混凝土量算成 0。"""
    assert geo.polygon_area(BOWTIE) == pytest.approx(0.0)
    assert geo.is_self_intersecting(BOWTIE)
    assert not geo.is_self_intersecting(SQUARE)


@pytest.mark.unit
def test_overlap_area_of_two_squares():
    other = [(1.0, 1.0), (3.0, 1.0), (3.0, 3.0), (1.0, 3.0)]
    assert geo.polygon_overlap_area(SQUARE, other) == pytest.approx(1.0)


@pytest.mark.unit
def test_disjoint_polygons_do_not_overlap():
    far = [(10.0, 10.0), (11.0, 10.0), (11.0, 11.0), (10.0, 11.0)]
    assert geo.polygon_overlap_area(SQUARE, far) == pytest.approx(0.0)
    assert not geo.boxes_overlap(geo.bbox(SQUARE), geo.bbox(far))


@pytest.mark.unit
def test_overlap_is_computed_on_convex_hulls_and_therefore_over_estimates():
    """凹多边形按凸包裁剪会**高估**重叠。判据是「重叠大到不可能」，
    高估让结论偏保守（更容易报），所以接受；但不能拿它当精确体积。"""
    l_shape = [(0.0, 0.0), (2.0, 0.0), (2.0, 0.5), (0.5, 0.5), (0.5, 2.0), (0.0, 2.0)]
    hull_area = geo.polygon_area(geo.convex_hull(l_shape))
    assert geo.polygon_overlap_area(l_shape, SQUARE) == pytest.approx(hull_area)
    assert hull_area > geo.polygon_area(l_shape)


@pytest.mark.unit
def test_point_in_polygon_counts_the_boundary_as_inside():
    assert geo.point_in_polygon((1.0, 1.0), SQUARE)
    assert geo.point_in_polygon((0.0, 1.0), SQUARE), "贴边的支承不该被否定"
    assert not geo.point_in_polygon((3.0, 1.0), SQUARE)


@pytest.mark.unit
def test_solidity_is_one_for_convex_and_smaller_for_a_notched_ring():
    assert geo.solidity(SQUARE) == pytest.approx(1.0)
    notched = [(0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (1.0, 0.6), (0.0, 2.0)]
    assert 0.0 < geo.solidity(notched) < 1.0


@pytest.mark.unit
def test_centroid_of_a_square_is_its_middle():
    assert geo.centroid(SQUARE) == pytest.approx((1.0, 1.0))


@pytest.mark.unit
def test_degenerate_inputs_return_none_instead_of_raising():
    assert geo.centroid([(0.0, 0.0)]) is None
    assert geo.solidity([(0.0, 0.0), (1.0, 1.0)]) is None
    assert geo.bbox([]) is None
    assert geo.thick_segment_ring((0.0, 0.0), (0.0, 0.0), 0.2) == []
