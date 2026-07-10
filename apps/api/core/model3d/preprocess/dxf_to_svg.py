"""``PrimitiveDoc`` → SVG（模型可吃的矢量输入）。

手写 SVG 序列化（不引入 svgwrite 依赖，KISS）。每个图元附 ``data-layer`` /
``data-block`` / ``data-id`` 属性，使下游（标注/训练/审校）可溯源到原始 CAD 元数据。
坐标沿用页面点（pt），SVG viewBox 覆盖整页。
"""
from __future__ import annotations

from xml.sax.saxutils import escape, quoteattr

from .schema import Primitive, PrimitiveDoc


def _fmt(v: float) -> str:
    """紧凑数值格式：整数省小数，浮点保留至多 3 位。"""
    return f"{v:.3f}".rstrip("0").rstrip(".") if v % 1 else str(int(v))


def _attrs(p: Primitive) -> str:
    parts = [f'data-id="{p.id}"']
    if p.layer:
        parts.append(f"data-layer={quoteattr(p.layer)}")
    if p.block:
        parts.append(f"data-block={quoteattr(p.block)}")
    return " ".join(parts)


def _points_str(p: Primitive) -> str:
    return " ".join(f"{_fmt(x)},{_fmt(y)}" for x, y in p.points)


def _primitive_svg(p: Primitive) -> str | None:
    if p.type == "line":
        (x0, y0), (x1, y1) = p.points
        return (
            f'<line x1="{_fmt(x0)}" y1="{_fmt(y0)}" x2="{_fmt(x1)}" y2="{_fmt(y1)}" '
            f'{_attrs(p)} />'
        )
    if p.type == "rect":
        return f'<polygon points="{_points_str(p)}" fill="none" {_attrs(p)} />'
    if p.type == "polyline":
        tag = "polygon" if p.closed else "polyline"
        return f'<{tag} points="{_points_str(p)}" fill="none" {_attrs(p)} />'
    if p.type == "text":
        (x, y) = p.points[0]
        content = escape(p.content or "")
        return f'<text x="{_fmt(x)}" y="{_fmt(y)}" {_attrs(p)}>{content}</text>'
    return None


def doc_to_svg(doc: PrimitiveDoc) -> str:
    """把图元文档序列化为完整 SVG 文档字符串。"""
    w = _fmt(doc.page_w) if doc.page_w else "1000"
    h = _fmt(doc.page_h) if doc.page_h else "1000"
    body = [s for p in doc.primitives if (s := _primitive_svg(p)) is not None]
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{w}" height="{h}" viewBox="0 0 {w} {h}">\n'
        + "\n".join(body)
        + "\n</svg>\n"
    )
