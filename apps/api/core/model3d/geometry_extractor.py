"""几何原语提取：矢量 PDF（PyMuPDF get_drawings）与 DXF（ezdxf 实体）。

任何异常 / 空矢量都返回空 ``DrawingGeometry``（尽力填 page_w/page_h），绝不抛异常——
上层 model_builder 据此回退贴图模式。
"""
from __future__ import annotations

import io
import logging

from .types import DrawingGeometry

logger = logging.getLogger(__name__)

# 单页几何原语上限（超出截断，识别层记 truncated）
MAX_PRIMITIVES = 20_000


def extract_pdf_geometry(data: bytes, page_index: int = 0) -> DrawingGeometry:
    """从矢量 PDF 页提取线段/矩形/多边形/文本。"""
    geom = DrawingGeometry()
    try:
        import fitz

        doc = fitz.open(stream=data, filetype="pdf")
        try:
            if page_index >= len(doc):
                return geom
            page = doc[page_index]
            geom.page_w, geom.page_h = float(page.rect.width), float(page.rect.height)
            _collect_pdf_drawings(page, geom)
            _collect_pdf_texts(page, geom)
        finally:
            doc.close()
    except Exception as exc:  # noqa: BLE001 — 提取失败降级空结构
        logger.warning("[model3d] PDF 几何提取失败: %s", exc)
    return geom


def _collect_pdf_drawings(page, geom: DrawingGeometry) -> None:
    """解析 page.get_drawings() 的绘图项：l=线段 re=矩形 c/qu=折线化。"""
    for drawing in page.get_drawings():
        if geom.primitive_count() >= MAX_PRIMITIVES:
            return
        filled = drawing.get("fill") is not None
        path_points: list[tuple[float, float]] = []
        for item in drawing.get("items", []):
            kind = item[0]
            if kind == "l":
                p0, p1 = item[1], item[2]
                geom.lines.append((p0.x, p0.y, p1.x, p1.y))
                path_points.extend([(p0.x, p0.y), (p1.x, p1.y)])
            elif kind == "re":
                rect = item[1]
                geom.rects.append(
                    (rect.x0, rect.y0, rect.width, rect.height, filled)
                )
            elif kind == "qu":
                quad = item[1]
                pts = [(p.x, p.y) for p in (quad.ul, quad.ur, quad.lr, quad.ll)]
                geom.polys.append(pts)
            elif kind == "c":
                # 贝塞尔曲线：按端点折线化
                p0, p3 = item[1], item[4]
                geom.lines.append((p0.x, p0.y, p3.x, p3.y))
                path_points.extend([(p0.x, p0.y), (p3.x, p3.y)])
        # 填充路径且首尾闭合 → 记为多边形（柱等实体填充识别依赖）
        if filled and len(path_points) >= 3:
            geom.polys.append(path_points)


def _collect_pdf_texts(page, geom: DrawingGeometry) -> None:
    for word in page.get_text("words"):
        if len(geom.texts) >= MAX_PRIMITIVES:
            return
        x0, y0, _x1, _y1, content = word[0], word[1], word[2], word[3], word[4]
        if str(content).strip():
            geom.texts.append((float(x0), float(y0), str(content).strip()))


def extract_dxf_geometry(data: bytes) -> DrawingGeometry:
    """从 DXF（DWG 先经 ensure_dxf 转换）提取几何原语。"""
    geom = DrawingGeometry()
    try:
        from core.ai_review.dwg_support import ensure_dxf

        dxf_data, ext, warning = ensure_dxf(data, _sniff_ext(data))
        if warning:
            logger.info("[model3d] DWG 转换降级: %s", warning)
            return geom

        from ezdxf import recover

        doc, _auditor = recover.read(io.BytesIO(dxf_data))
        _collect_dxf_entities(doc.modelspace(), geom)
        _fill_dxf_extents(doc, geom)
    except Exception as exc:  # noqa: BLE001 — 提取失败降级空结构
        logger.warning("[model3d] DXF 几何提取失败: %s", exc)
    return geom


def _sniff_ext(data: bytes) -> str:
    return "dwg" if data[:4] == b"AC10" else "dxf"


def _collect_dxf_entities(msp, geom: DrawingGeometry) -> None:
    for entity in msp:
        if geom.primitive_count() >= MAX_PRIMITIVES:
            return
        kind = entity.dxftype()
        try:
            if kind == "LINE":
                s, e = entity.dxf.start, entity.dxf.end
                geom.lines.append((s.x, s.y, e.x, e.y))
            elif kind in ("LWPOLYLINE", "POLYLINE"):
                points = _polyline_points(entity, kind)
                if len(points) >= 3 and getattr(entity, "closed", False):
                    geom.polys.append(points)
                else:
                    for i in range(len(points) - 1):
                        geom.lines.append((*points[i], *points[i + 1]))
            elif kind == "SOLID":
                pts = [(entity.dxf.vtx0.x, entity.dxf.vtx0.y),
                       (entity.dxf.vtx1.x, entity.dxf.vtx1.y),
                       (entity.dxf.vtx2.x, entity.dxf.vtx2.y)]
                geom.polys.append(pts)
            elif kind == "HATCH":
                for path in entity.paths:
                    vertices = getattr(path, "vertices", None)
                    if vertices:
                        geom.polys.append([(v[0], v[1]) for v in vertices])
            elif kind in ("TEXT", "MTEXT"):
                insert = entity.dxf.insert
                content = (
                    entity.plain_mtext() if kind == "MTEXT" else entity.dxf.text
                )
                if content and content.strip():
                    geom.texts.append((insert.x, insert.y, content.strip()))
        except Exception:  # noqa: BLE001 — 单实体解析失败跳过
            continue


def _polyline_points(entity, kind: str) -> list[tuple[float, float]]:
    if kind == "LWPOLYLINE":
        return [(p[0], p[1]) for p in entity.get_points()]
    return [(v.dxf.location.x, v.dxf.location.y) for v in entity.vertices]


def _fill_dxf_extents(doc, geom: DrawingGeometry) -> None:
    """页尺寸：header extents 优先，否则由几何包络推算。"""
    try:
        ext_min = doc.header.get("$EXTMIN", (0, 0, 0))
        ext_max = doc.header.get("$EXTMAX", (0, 0, 0))
        geom.page_w = float(ext_max[0]) - float(ext_min[0])
        geom.page_h = float(ext_max[1]) - float(ext_min[1])
    except Exception:  # noqa: BLE001
        pass
    if geom.page_w <= 0 or geom.page_h <= 0:
        xs, ys = _bounds(geom)
        if xs and ys:
            geom.page_w = max(xs) - min(xs)
            geom.page_h = max(ys) - min(ys)


def _bounds(geom: DrawingGeometry) -> tuple[list[float], list[float]]:
    xs: list[float] = []
    ys: list[float] = []
    for x0, y0, x1, y1 in geom.lines:
        xs.extend([x0, x1]); ys.extend([y0, y1])
    for x, y, w, h, _ in geom.rects:
        xs.extend([x, x + w]); ys.extend([y, y + h])
    for poly in geom.polys:
        for x, y in poly:
            xs.append(x); ys.append(y)
    return xs, ys
