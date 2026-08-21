"""用世界锚点钉住轴网帧（K-3 第二级）。

**实测**：`axis_intersections` 里 5401 个交点**全部带世界坐标**，
覆盖大歌剧院 76 张图。含锚点的帧有 36 个，能钉住 **445 张图（32%）**——
比纯靠帧间共有轴号的 12% 高出一倍多。

锚点是**强证据**（轴号交点 → 实测世界坐标，Phase I 实测 RMSE 5.7 毫米），
帧间共有轴号是弱证据。两级配准：先用锚点钉，再让其余帧向已钉住的帧对齐。
"""
import pytest


@pytest.mark.unit
def test_frame_is_pinned_by_a_single_anchor():
    """一个锚点就能定平移（帧内相对关系已由 K-1 定死，只差整体位置）。"""
    from services.frame_world_anchor import solve_frame_world_offset

    # 帧内：轴号 1 在 x=0，A 在 y=0
    axes = {"x": {"1": 0.0, "2": 8.0}, "y": {"A": 0.0, "B": 6.0}}
    # 锚点：轴号 2×B 的实测世界坐标是 (1008, 606)
    offset = solve_frame_world_offset(
        axes, [{"label_x": "2", "label_y": "B", "world_x": 1008.0, "world_y": 606.0}])
    assert offset["x"] == pytest.approx(1000.0)
    assert offset["y"] == pytest.approx(600.0)


@pytest.mark.unit
def test_multiple_anchors_use_the_median():
    """多个锚点取中位——**一个锚点标错不会带偏整帧**。"""
    from services.frame_world_anchor import solve_frame_world_offset

    axes = {"x": {"1": 0.0, "2": 8.0, "3": 16.0}, "y": {"A": 0.0}}
    offset = solve_frame_world_offset(axes, [
        {"label_x": "1", "label_y": "A", "world_x": 100.0, "world_y": 50.0},
        {"label_x": "2", "label_y": "A", "world_x": 108.0, "world_y": 50.0},
        {"label_x": "3", "label_y": "A", "world_x": 999.0, "world_y": 50.0},  # 错的
    ])
    assert offset["x"] == pytest.approx(100.0)


@pytest.mark.unit
def test_anchor_with_unknown_labels_is_ignored():
    """锚点的轴号不在本帧里就用不上——**不能拿它硬凑**。"""
    from services.frame_world_anchor import solve_frame_world_offset

    axes = {"x": {"1": 0.0}, "y": {"A": 0.0}}
    assert solve_frame_world_offset(
        axes, [{"label_x": "99", "label_y": "Z",
                "world_x": 1.0, "world_y": 2.0}]) is None


@pytest.mark.unit
def test_missing_world_coordinate_is_not_treated_as_zero():
    """世界坐标为空的交点是「没测过」，不是「在原点」。"""
    from services.frame_world_anchor import solve_frame_world_offset

    axes = {"x": {"1": 0.0}, "y": {"A": 0.0}}
    assert solve_frame_world_offset(
        axes, [{"label_x": "1", "label_y": "A",
                "world_x": None, "world_y": None}]) is None


@pytest.mark.unit
def test_no_anchors_yields_none():
    from services.frame_world_anchor import solve_frame_world_offset

    assert solve_frame_world_offset({"x": {"1": 0.0}, "y": {"A": 0.0}}, []) is None
    assert solve_frame_world_offset(None, None) is None


@pytest.mark.unit
def test_one_direction_only_still_pins_that_direction():
    """只有 x 轴号对得上时，x 能钉、y 判不出 —— **不要因为一半缺失就整个放弃**，
    也不要给 y 编一个 0。"""
    from services.frame_world_anchor import solve_frame_world_offset

    axes = {"x": {"1": 0.0, "2": 8.0}, "y": {"A": 0.0}}
    offset = solve_frame_world_offset(
        axes, [{"label_x": "2", "label_y": "ZZ", "world_x": 108.0, "world_y": 55.0}])
    assert offset["x"] == pytest.approx(100.0)
    assert offset["y"] is None
