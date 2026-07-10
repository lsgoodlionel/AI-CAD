"""``DrawingGeometry`` → 统一图元 JSON（``PrimitiveDoc``）。

复用 ``geometry_extractor`` 已提取的精确图元与其并行的图层/块列表
（``line_layers`` / ``rect_layers`` / ``rect_blocks`` / ``poly_layers`` / ``poly_blocks``），
不重复造轮子。严格保持「几何 ↔ 图层/块」索引对齐（见 types.py 契约）。
"""
from __future__ import annotations

from itertools import count

from core.model3d.types import DrawingGeometry

from .schema import Primitive, PrimitiveDoc


def _at(seq: list[str], idx: int) -> str:
    """安全取并行列表元素（长度不足时降级为空串，绝不抛越界）。"""
    return seq[idx] if idx < len(seq) else ""


def geometry_to_primitives(
    geom: DrawingGeometry, warnings: tuple[str, ...] = ()
) -> PrimitiveDoc:
    """把一张图纸的几何原语转为图元文档。"""
    ids = count()
    primitives: list[Primitive] = []

    # line: (x0, y0, x1, y1)
    for i, (x0, y0, x1, y1) in enumerate(geom.lines):
        primitives.append(
            Primitive(
                id=next(ids),
                type="line",
                points=((float(x0), float(y0)), (float(x1), float(y1))),
                layer=_at(geom.line_layers, i),
            )
        )

    # rect: (x, y, w, h, filled) → 四角
    for i, rect in enumerate(geom.rects):
        x, y, w, h, filled = rect
        corners = (
            (float(x), float(y)),
            (float(x) + float(w), float(y)),
            (float(x) + float(w), float(y) + float(h)),
            (float(x), float(y) + float(h)),
        )
        primitives.append(
            Primitive(
                id=next(ids),
                type="rect",
                points=corners,
                layer=_at(geom.rect_layers, i),
                block=_at(geom.rect_blocks, i),
                filled=bool(filled),
            )
        )

    # polyline: list[(x, y)]
    for i, poly in enumerate(geom.polys):
        pts = tuple((float(px), float(py)) for px, py in poly)
        closed = len(pts) >= 3 and pts[0] == pts[-1]
        primitives.append(
            Primitive(
                id=next(ids),
                type="polyline",
                points=pts,
                layer=_at(geom.poly_layers, i),
                block=_at(geom.poly_blocks, i),
                closed=closed,
            )
        )

    # text: (x, y, content)
    for x, y, content in geom.texts:
        primitives.append(
            Primitive(
                id=next(ids),
                type="text",
                points=((float(x), float(y)),),
                content=str(content),
            )
        )

    return PrimitiveDoc(
        page_w=float(geom.page_w),
        page_h=float(geom.page_h),
        primitives=tuple(primitives),
        warnings=tuple(warnings),
    )
