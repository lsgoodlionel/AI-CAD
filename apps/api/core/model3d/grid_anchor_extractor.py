"""轴网锚点提取（B-08）：视图几何 → 轴线 + 轴号，作跨视图配准锚点。

轴号（1/2/3、A/B/C）是三视图共享的天然对齐键，比纯 CV 图像匹配鲁棒。
复用 element_recognizer._detect_axes（长直线 + 端部轴号识别），产出结构化 GridSystem，
并提供 to_axes_dict 供 model_elements.register_offset 直接消费（配准复用现有一维平移）。

轴号缺失时返回可用轴线几何 + unlabeled 标记（配准可退化为纯几何），绝不丢弃。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from core.model3d.element_recognizer import _detect_axes
from core.model3d.types import DrawingGeometry


@dataclass(frozen=True)
class GridAxis:
    """单条轴线：归一化轴号（无则 ""）+ 页面坐标（pt）。"""
    label: str
    coord: float


@dataclass(frozen=True)
class GridSystem:
    axes_x: tuple[GridAxis, ...] = ()   # 竖向轴线（x 位置），常标 1/2/3
    axes_y: tuple[GridAxis, ...] = ()   # 横向轴线（y 位置），常标 A/B/C
    confidence: float = 0.0             # 带轴号轴线占比
    unlabeled: bool = True              # 全无轴号（仅几何可用）


def extract_grid_anchors(geom: DrawingGeometry) -> GridSystem:
    """从几何抽取轴网锚点。任何异常/空输入 → 空 GridSystem，绝不抛。"""
    try:
        axis_x, axis_y, _axis_idx = _detect_axes(
            geom.lines, geom.page_w, geom.page_h, geom.texts
        )
    except Exception:  # noqa: BLE001 — 轴网识别失败降级空
        return GridSystem()

    axes_x = tuple(GridAxis(label=label, coord=float(pos)) for label, pos in axis_x)
    axes_y = tuple(GridAxis(label=label, coord=float(pos)) for label, pos in axis_y)
    total = len(axes_x) + len(axes_y)
    labeled = sum(1 for axis in (*axes_x, *axes_y) if axis.label)
    confidence = round(labeled / total, 4) if total else 0.0
    return GridSystem(
        axes_x=axes_x,
        axes_y=axes_y,
        confidence=confidence,
        unlabeled=labeled == 0,
    )


def to_axes_dict(grid: GridSystem) -> dict[str, list[tuple[str, float]]]:
    """转 model_elements.register_offset 契约格式 {"x": [(label,pos)], "y": [...]}。"""
    return {
        "x": [(axis.label, axis.coord) for axis in grid.axes_x],
        "y": [(axis.label, axis.coord) for axis in grid.axes_y],
    }
