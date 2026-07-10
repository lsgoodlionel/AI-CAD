"""构件级 3D 重建 — 公共数据结构（蓝图 MODEL_PRECISION_BLUEPRINT 第 3 节契约）。

坐标单位约定：
- ``DrawingGeometry``：页面点（pt，PDF 坐标系 / DXF 图纸单位）
- ``FloorElements``：米（经比例尺换算并平移到轴网原点）
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DrawingGeometry:
    """单张图纸的几何原语集合。

    图层/块并行列表（A-14/A-15，向后兼容）：
    ``*_layers`` / ``*_blocks`` 与对应几何列表**严格索引对齐**——
    ``line_layers[i]`` 是 ``lines[i]`` 的来源 CAD 图层名，``rect_blocks[i]``
    是 ``rects[i]`` 所属 ``INSERT`` 块引用名（非块来源为 ""）。
    构造几何时必须与并行列表**同步 append**，以保证 ``len(line_layers) == len(lines)``
    等长度契约恒成立（下游 A-16 图层约定识别依赖该契约）。
    无图层信息来源（PDF、无图层 DXF）一律填 ""（空串），长度仍与几何一致。
    """
    page_w: float = 0.0
    page_h: float = 0.0
    lines: list[tuple[float, float, float, float]] = field(default_factory=list)
    # (x, y, w, h, filled)
    rects: list[tuple[float, float, float, float, bool]] = field(default_factory=list)
    # 闭合多边形（含填充路径）
    polys: list[list[tuple[float, float]]] = field(default_factory=list)
    # (x, y, content)
    texts: list[tuple[float, float, str]] = field(default_factory=list)

    # 与 lines 索引对齐：每条线段来源 CAD 图层名（无则 ""）
    line_layers: list[str] = field(default_factory=list)
    # 与 rects 索引对齐：矩形来源图层名 / 所属 INSERT 块名（无则 ""）
    rect_layers: list[str] = field(default_factory=list)
    rect_blocks: list[str] = field(default_factory=list)
    # 与 polys 索引对齐：多边形来源图层名 / 所属 INSERT 块名（无则 ""）
    poly_layers: list[str] = field(default_factory=list)
    poly_blocks: list[str] = field(default_factory=list)

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
