"""构件级 3D 重建 — 公共数据结构（蓝图 MODEL_PRECISION_BLUEPRINT 第 3 节契约）。

坐标单位约定：
- ``DrawingGeometry``：页面点（pt，PDF 坐标系 / DXF 图纸单位）
- ``FloorElements``：米（经比例尺换算并平移到轴网原点）
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DrawingGeometry:
    """单张图纸的几何原语集合。"""
    page_w: float = 0.0
    page_h: float = 0.0
    lines: list[tuple[float, float, float, float]] = field(default_factory=list)
    # (x, y, w, h, filled)
    rects: list[tuple[float, float, float, float, bool]] = field(default_factory=list)
    # 闭合多边形（含填充路径）
    polys: list[list[tuple[float, float]]] = field(default_factory=list)
    # (x, y, content)
    texts: list[tuple[float, float, str]] = field(default_factory=list)

    def primitive_count(self) -> int:
        return len(self.lines) + len(self.rects) + len(self.polys)


@dataclass
class FloorElements:
    """一张平面图识别出的构件集合（米坐标）。"""
    scale: float = 0.0                 # 米/点 换算系数
    axes: dict = field(default_factory=dict)   # {"x":[(label,pos_m)], "y":[...], 可含 truncated}
    columns: list[dict] = field(default_factory=list)
    walls: list[dict] = field(default_factory=list)
    beams: list[dict] = field(default_factory=list)
    slabs: list[dict] = field(default_factory=list)
    pipes: list[dict] = field(default_factory=list)
    equipment: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "columns": self.columns,
            "walls": self.walls,
            "beams": self.beams,
            "slabs": self.slabs,
            "pipes": self.pipes,
            "equipment": self.equipment,
        }

    def stats(self) -> dict:
        return {key: len(value) for key, value in self.as_dict().items()}
