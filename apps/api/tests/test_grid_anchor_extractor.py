"""轴网锚点提取测试（B-08）。

从视图几何抽轴线 + 轴号，作为平面↔剖面↔立面配准锚点。
横（纵轴 y）/ 纵（横轴 x）分辨；轴号缺失返回几何 + unlabeled。
"""
import pytest

from core.model3d.grid_anchor_extractor import (
    GridAxis,
    GridSystem,
    extract_grid_anchors,
    to_axes_dict,
)
from core.model3d.types import DrawingGeometry


def _plan_geom(with_labels: bool = True) -> DrawingGeometry:
    """双向轴网：竖线 x=100/300/500（轴号 1/2/3）、横线 y=100/400/700（轴号 A/B/C）。"""
    lines: list[tuple[float, float, float, float]] = []
    texts: list[tuple[float, float, str]] = []
    for x, label in ((100, "1"), (300, "2"), (500, "3")):
        lines.append((x, 40, x, 760))
        if with_labels:
            texts.append((x, 20, label))
    for y, label in ((100, "A"), (400, "B"), (700, "C")):
        lines.append((40, y, 560, y))
        if with_labels:
            texts.append((20, y, label))
    return DrawingGeometry(page_w=600, page_h=800, lines=lines, texts=texts)


# ── 基本提取 ────────────────────────────────────────────────────

@pytest.mark.unit
def test_extracts_labeled_grid_both_directions():
    grid = extract_grid_anchors(_plan_geom())
    assert isinstance(grid, GridSystem)
    x_labels = {axis.label for axis in grid.axes_x}
    y_labels = {axis.label for axis in grid.axes_y}
    assert x_labels == {"1", "2", "3"}
    assert y_labels == {"A", "B", "C"}
    assert grid.unlabeled is False
    assert grid.confidence == pytest.approx(1.0)


@pytest.mark.unit
def test_grid_axis_carries_coord():
    grid = extract_grid_anchors(_plan_geom())
    axis_1 = next(a for a in grid.axes_x if a.label == "1")
    assert isinstance(axis_1, GridAxis)
    assert axis_1.coord == pytest.approx(100.0)


@pytest.mark.unit
def test_unlabeled_grid_returns_geometry_flagged():
    grid = extract_grid_anchors(_plan_geom(with_labels=False))
    assert grid.unlabeled is True
    assert grid.confidence == pytest.approx(0.0)
    # 轴线几何仍可用（配准可退化用几何）
    assert len(grid.axes_x) == 3
    assert len(grid.axes_y) == 3


@pytest.mark.unit
def test_partial_labels_confidence_between():
    geom = _plan_geom()
    # 去掉一个轴号文本
    geom.texts = [t for t in geom.texts if t[2] != "2"]
    grid = extract_grid_anchors(geom)
    assert 0.0 < grid.confidence < 1.0


@pytest.mark.unit
def test_empty_geometry_returns_empty_grid():
    grid = extract_grid_anchors(DrawingGeometry())
    assert grid.axes_x == ()
    assert grid.axes_y == ()
    assert grid.confidence == pytest.approx(0.0)


# ── 与 register_offset 互操作 ──────────────────────────────────

@pytest.mark.unit
def test_to_axes_dict_shape_matches_register_offset_contract():
    grid = extract_grid_anchors(_plan_geom())
    axes = to_axes_dict(grid)
    assert set(axes) == {"x", "y"}
    assert ("1", 100.0) in [(label, coord) for label, coord in axes["x"]]
