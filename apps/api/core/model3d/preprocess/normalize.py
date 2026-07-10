"""C-03 坐标归一化子模块（供神经网络输入）。

把图元文档的页面点坐标平移到原点并**等比缩放**到单位域 [0,1]（保持长宽比，
避免各向异性拉伸破坏几何）。返回新文档 + 归一化参数（可逆、可入 provenance）。

纯函数、不可变：返回新 ``PrimitiveDoc``，不改入参。
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from .schema import Primitive, PrimitiveDoc


@dataclass(frozen=True)
class NormalizeParams:
    """归一化变换参数：``norm = (raw - offset) * scale``。"""
    offset_x: float
    offset_y: float
    scale: float
    src_width: float
    src_height: float

    def to_dict(self) -> dict:
        return {
            "offset_x": self.offset_x,
            "offset_y": self.offset_y,
            "scale": self.scale,
            "src_width": self.src_width,
            "src_height": self.src_height,
        }


def _iter_points(doc: PrimitiveDoc):
    for p in doc.primitives:
        yield from p.points


def _bounds(doc: PrimitiveDoc) -> tuple[float, float, float, float] | None:
    xs: list[float] = []
    ys: list[float] = []
    for x, y in _iter_points(doc):
        xs.append(x)
        ys.append(y)
    if not xs or not ys:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def normalize_doc(doc: PrimitiveDoc) -> tuple[PrimitiveDoc, NormalizeParams]:
    """等比归一化到 [0,1]（保持长宽比）。空文档/退化尺寸时返回恒等变换。"""
    bounds = _bounds(doc)
    if bounds is None:
        return doc, NormalizeParams(0.0, 0.0, 1.0, 0.0, 0.0)

    min_x, min_y, max_x, max_y = bounds
    width, height = max_x - min_x, max_y - min_y
    extent = max(width, height)
    scale = 1.0 / extent if extent > 0 else 1.0  # 退化（单点/共线）→ 仅平移

    def _tx(pt: tuple[float, float]) -> tuple[float, float]:
        x, y = pt
        return ((x - min_x) * scale, (y - min_y) * scale)

    new_primitives = tuple(
        replace(p, points=tuple(_tx(pt) for pt in p.points))
        for p in doc.primitives
    )
    params = NormalizeParams(
        offset_x=min_x, offset_y=min_y, scale=scale,
        src_width=width, src_height=height,
    )
    new_doc = replace(
        doc,
        primitives=new_primitives,
        page_w=width * scale,
        page_h=height * scale,
    )
    return new_doc, params
