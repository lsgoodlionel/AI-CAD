"""C-02 预处理器输出契约（图元 JSON schema）。

所有开源符号识别模型（CADTransformer / VecFormer）不吃 DXF/DWG，只吃 SVG + 图元
序列。本模块定义**统一图元 JSON**，作为 DXF/PDF → 模型输入的稳定中间表示。

坐标单位：页面点（pt，与 ``core/model3d/types.py:DrawingGeometry`` 一致）。
几何原语类型：line / rect / polyline / text。

图层（``layer``）与块引用（``block``）从 ``DrawingGeometry`` 的并行列表透传，
供 C-04 自动标注引擎复用为弱标签溯源字段。``color`` / ``linetype`` 为**预留字段**
（当前 ``geometry_extractor`` 未提取，恒为 None；后续增强时填充，不改契约）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

SCHEMA_VERSION = 1

PrimitiveType = Literal["line", "rect", "polyline", "text"]


@dataclass(frozen=True)
class Primitive:
    """单个几何原语。

    - ``points``：坐标序列 ``[[x, y], ...]``。line 两点；rect 四角（顺时针）；
      polyline N 点；text 单点（其位置）。
    - ``filled``：仅 rect / polyline 有意义（是否填充）。
    - ``closed``：仅 polyline 有意义（是否闭合）。
    - ``content``：仅 text 有意义（文本内容）。
    """
    id: int
    type: PrimitiveType
    points: tuple[tuple[float, float], ...]
    layer: str = ""
    block: str = ""
    filled: bool | None = None
    closed: bool | None = None
    content: str | None = None
    color: str | None = None       # 预留
    linetype: str | None = None    # 预留

    def to_dict(self) -> dict:
        out = {
            "id": self.id,
            "type": self.type,
            "layer": self.layer,
            "block": self.block,
            "points": [list(p) for p in self.points],
        }
        if self.filled is not None:
            out["filled"] = self.filled
        if self.closed is not None:
            out["closed"] = self.closed
        if self.content is not None:
            out["content"] = self.content
        if self.color is not None:
            out["color"] = self.color
        if self.linetype is not None:
            out["linetype"] = self.linetype
        return out


@dataclass(frozen=True)
class PrimitiveDoc:
    """一张图纸的图元文档（预处理主产物）。"""
    schema_version: int = SCHEMA_VERSION
    units: str = "pt"
    page_w: float = 0.0
    page_h: float = 0.0
    primitives: tuple[Primitive, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {"line": 0, "rect": 0, "polyline": 0, "text": 0}
        for p in self.primitives:
            out[p.type] = out.get(p.type, 0) + 1
        return out

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "units": self.units,
            "page": {"w": self.page_w, "h": self.page_h},
            "counts": self.counts,
            "primitives": [p.to_dict() for p in self.primitives],
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class PreprocessResult:
    """预处理器统一输出：图元 JSON + SVG。"""
    doc: PrimitiveDoc
    svg: str
    source_ext: str = ""                       # dxf / pdf / dwg（降级）
    provenance: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "source_ext": self.source_ext,
            "doc": self.doc.to_dict(),
            "svg": self.svg,
            "provenance": self.provenance,
        }
