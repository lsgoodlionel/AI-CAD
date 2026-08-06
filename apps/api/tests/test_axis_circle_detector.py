"""轴号圈识别单测(纯函数部分:形态判别/半径聚类/单调性)。"""
from core.model3d.axis_circle_detector import (
    crop_box,
    is_axis_label,
    monotonic_violation_rate,
    radius_mode_cluster,
)


def test_valid_axis_labels():
    for ok in ("1", "12", "A", "AB", "A'"):
        assert is_axis_label(ok) is True


def test_invalid_axis_labels():
    """尺寸数字/长串/空 都不是轴号(实测这类被误分类进 axis 档案)。"""
    for bad in ("8400", "123", "0.15", "板厚150", "", "标高"):
        assert is_axis_label(bad) is False


def test_radius_cluster_keeps_uniform_circles():
    """轴号圈大小统一;柱/桩截面圆大小各异 → 只留众数簇。"""
    circles = [(0, 0, 25.0), (10, 0, 26.0), (20, 0, 27.0),   # 轴号圈簇
               (30, 0, 60.0), (40, 0, 90.0)]                  # 柱截面(半径迥异)
    kept = radius_mode_cluster(circles)
    assert len(kept) == 3
    assert all(20 <= c[2] <= 30 for c in kept)


def test_radius_cluster_empty():
    assert radius_mode_cluster([]) == []


def test_monotonic_rate_perfect_grid():
    """真实轴网:1/2/3 从左到右 → 逆序率 0。"""
    assert monotonic_violation_rate([("1", 0.0), ("2", 10.0), ("3", 20.0)]) == 0.0


def test_monotonic_rate_detects_garbage():
    """乱序(实测档案数据中位逆序率 0.60)→ 高逆序率,应弃用。"""
    rate = monotonic_violation_rate([("3", 0.0), ("1", 10.0), ("2", 20.0)])
    assert rate is not None and rate > 0.3


def test_monotonic_rate_needs_three():
    assert monotonic_violation_rate([("1", 0.0), ("2", 5.0)]) is None


def test_monotonic_ignores_letters():
    """字母轴号不参与数值单调性判断。"""
    assert monotonic_violation_rate([("A", 0.0), ("B", 5.0), ("C", 9.0)]) is None


def test_crop_box_clamped_to_image():
    x0, y0, x1, y1 = crop_box(5.0, 5.0, 20.0, w=100, h=100)
    assert x0 == 0 and y0 == 0        # 不越界
    assert x1 <= 100 and y1 <= 100


def test_dedup_merges_repeated_detections():
    """Hough 对同一轴号圈常重复检出(实测同一「2」检出 5 次)→ 必须去重。"""
    from core.model3d.axis_circle_detector import dedup_circles
    circles = [(2517.0, 100.0, 25.0), (2518.0, 100.0, 25.0), (2519.0, 101.0, 26.0),
               (2900.0, 100.0, 25.0)]
    kept = dedup_circles(circles)
    assert len(kept) == 2       # 前三个是同一个圈


def test_dedup_keeps_distinct():
    from core.model3d.axis_circle_detector import dedup_circles
    circles = [(0.0, 0.0, 20.0), (100.0, 0.0, 20.0), (200.0, 0.0, 20.0)]
    assert len(dedup_circles(circles)) == 3
