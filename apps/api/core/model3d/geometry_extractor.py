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


# --- 图层/块对齐 append 辅助 ----------------------------------------------
# 正确性关键：每 append 一个几何原语，就同步 append 一个 layer/block，
# 保证 DrawingGeometry 的 *_layers/*_blocks 与几何列表严格索引对齐。
# 所有几何写入必须经这三个函数，禁止直接 geom.lines.append(...)。

def _add_line(geom: DrawingGeometry, x0: float, y0: float, x1: float, y1: float,
              layer: str = "") -> None:
    geom.lines.append((x0, y0, x1, y1))
    geom.line_layers.append(layer)


def _add_rect(geom: DrawingGeometry, x: float, y: float, w: float, h: float,
              filled: bool, layer: str = "", block: str = "") -> None:
    geom.rects.append((x, y, w, h, filled))
    geom.rect_layers.append(layer)
    geom.rect_blocks.append(block)


def _add_poly(geom: DrawingGeometry, pts: list[tuple[float, float]],
              layer: str = "", block: str = "") -> None:
    geom.polys.append(pts)
    geom.poly_layers.append(layer)
    geom.poly_blocks.append(block)


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
        # PDF 无图层/块概念：并行列表统一填 ""（经 _add_* 保证对齐）
        for item in drawing.get("items", []):
            kind = item[0]
            if kind == "l":
                p0, p1 = item[1], item[2]
                _add_line(geom, p0.x, p0.y, p1.x, p1.y)
                path_points.extend([(p0.x, p0.y), (p1.x, p1.y)])
            elif kind == "re":
                rect = item[1]
                _add_rect(geom, rect.x0, rect.y0, rect.width, rect.height, filled)
            elif kind == "qu":
                quad = item[1]
                pts = [(p.x, p.y) for p in (quad.ul, quad.ur, quad.lr, quad.ll)]
                _add_poly(geom, pts)
            elif kind == "c":
                # 贝塞尔曲线：按端点折线化
                p0, p3 = item[1], item[4]
                _add_line(geom, p0.x, p0.y, p3.x, p3.y)
                path_points.extend([(p0.x, p0.y), (p3.x, p3.y)])
        # 填充路径且首尾闭合 → 记为多边形（柱等实体填充识别依赖）
        if filled and len(path_points) >= 3:
            _add_poly(geom, path_points)


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
        _process_dxf_entity(entity, geom, block="")


def _process_dxf_entity(entity, geom: DrawingGeometry, block: str) -> None:
    """单实体 → 几何原语（记录 entity.dxf.layer / 所属块名 block）。

    ``block`` 为该实体所属 INSERT 块引用名（顶层实体为 ""）。
    每个几何写入均经 ``_add_*`` 保证图层/块并行列表严格索引对齐。
    """
    kind = entity.dxftype()
    try:
        if kind == "INSERT":
            _expand_insert(entity, geom)
            return
        layer = getattr(entity.dxf, "layer", "") or ""
        if kind == "LINE":
            s, e = entity.dxf.start, entity.dxf.end
            _add_line(geom, s.x, s.y, e.x, e.y, layer)
        elif kind in ("LWPOLYLINE", "POLYLINE"):
            points = _polyline_points(entity, kind)
            if len(points) >= 3 and getattr(entity, "closed", False):
                _add_poly(geom, points, layer, block)
            else:
                for i in range(len(points) - 1):
                    _add_line(geom, *points[i], *points[i + 1], layer)
        elif kind == "SOLID":
            pts = [(entity.dxf.vtx0.x, entity.dxf.vtx0.y),
                   (entity.dxf.vtx1.x, entity.dxf.vtx1.y),
                   (entity.dxf.vtx2.x, entity.dxf.vtx2.y)]
            _add_poly(geom, pts, layer, block)
        elif kind == "HATCH":
            for path in entity.paths:
                vertices = getattr(path, "vertices", None)
                if vertices:
                    _add_poly(geom, [(v[0], v[1]) for v in vertices], layer, block)
        elif kind in ("TEXT", "MTEXT"):
            insert = entity.dxf.insert
            content = (
                entity.plain_mtext() if kind == "MTEXT" else entity.dxf.text
            )
            if content and content.strip():
                geom.texts.append((insert.x, insert.y, content.strip()))
    except Exception:  # noqa: BLE001 — 单实体解析失败跳过
        return


def _expand_insert(entity, geom: DrawingGeometry) -> None:
    """展开 INSERT 块引用为原语，记录块名到对应 *_blocks 列表。

    用 ezdxf ``virtual_entities()`` 展开块内实体——自动应用 INSERT 的
    插入点/缩放/旋转变换，得到模型空间真实坐标。``virtual_entities`` 不可用
    （旧版本 / 特殊实体）时降级跳过，绝不抛异常。尊重 MAX_PRIMITIVES 上限。
    """
    block_name = getattr(entity.dxf, "name", "") or ""
    try:
        virtuals = entity.virtual_entities()
    except Exception:  # noqa: BLE001 — virtual_entities 不可用 → 降级跳过
        return
    for sub in virtuals:
        if geom.primitive_count() >= MAX_PRIMITIVES:
            return
        _process_dxf_entity(sub, geom, block=block_name)


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
