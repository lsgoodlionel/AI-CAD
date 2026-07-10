"""C-03 块 INSERT 展开器（保留块名/图层弱标签溯源）。

C-02 复用的 ``geometry_extractor`` 把块内 lwpolyline 分解为线段，且 ``DrawingGeometry``
的线段无块名字段——**块级弱标签在线段上丢失**。本模块提供 ML 数据专用的展开器：
递归展开 ``INSERT``（含嵌套块、缩放/旋转、MINSERT 阵列），并在**每个**展开图元
（含线段）上保留其来源块名 + 图层，供 C-04 自动标注引擎复用为弱标签。

用 ezdxf ``virtual_entities()`` 完成变换（插入点/缩放/旋转，MINSERT 阵列自动逐格
展开），嵌套块递归下钻并**沿用顶层 INSERT 块名**（放置符号的块名才是有意义的弱标签）。
异常一律优雅降级为空文档 + warning，绝不抛异常；尊重图元上限与递归深度上限。
"""
from __future__ import annotations

import io
import logging
from itertools import count

from .schema import Primitive, PrimitiveDoc

logger = logging.getLogger(__name__)

MAX_PRIMITIVES = 200_000
MAX_BLOCK_DEPTH = 16


def expand_blocks(data: bytes) -> PrimitiveDoc:
    """从 DXF/DWG 字节展开所有块引用为带块名溯源的图元文档。"""
    warnings: list[str] = []
    primitives: list[Primitive] = []
    ids = count()

    try:
        from core.ai_review.dwg_support import ensure_dxf

        dxf_data, _ext, warning = ensure_dxf(data, _sniff_ext(data))
        if warning:
            return PrimitiveDoc(warnings=(f"DWG 转换降级: {warning}",))

        from ezdxf import recover

        doc, _auditor = recover.read(io.BytesIO(dxf_data))
        msp = doc.modelspace()
        for entity in msp:
            if len(primitives) >= MAX_PRIMITIVES:
                warnings.append("达到图元上限，截断")
                break
            _emit_entity(entity, block="", ids=ids, out=primitives, depth=0)
        page_w, page_h = _page_size(doc, primitives)
    except Exception as exc:  # noqa: BLE001 — 展开失败优雅降级
        logger.warning("[block_expander] 展开失败: %s", exc)
        return PrimitiveDoc(warnings=(f"块展开失败: {exc}",))

    return PrimitiveDoc(
        page_w=page_w,
        page_h=page_h,
        primitives=tuple(primitives),
        warnings=tuple(warnings),
    )


def _sniff_ext(data: bytes) -> str:
    return "dwg" if data[:4] == b"AC10" else "dxf"


def _emit_entity(
    entity, *, block: str, ids, out: list[Primitive], depth: int
) -> None:
    """单实体 → 图元（保留 block/layer）；INSERT 递归展开。"""
    if len(out) >= MAX_PRIMITIVES:
        return
    kind = entity.dxftype()
    try:
        if kind == "INSERT":
            _expand_insert(entity, block=block, ids=ids, out=out, depth=depth)
            return
        layer = getattr(entity.dxf, "layer", "") or ""
        prim = _entity_to_primitive(entity, kind, layer, block, next(ids))
        if prim is not None:
            out.append(prim)
    except Exception:  # noqa: BLE001 — 单实体失败跳过，不影响整图
        return


def _expand_insert(entity, *, block: str, ids, out: list[Primitive], depth: int) -> None:
    if depth >= MAX_BLOCK_DEPTH:
        return
    # 顶层 INSERT 块名作为整棵子树的弱标签；嵌套沿用顶层块名
    block_name = block or (getattr(entity.dxf, "name", "") or "")
    # MINSERT 阵列（row/column > 1）：virtual_entities 不展开栅格，先用
    # multi_insert 拆成逐格 INSERT，再各自展开。
    for cell in _grid_inserts(entity):
        try:
            virtuals = cell.virtual_entities()
        except Exception:  # noqa: BLE001 — virtual_entities 不可用 → 降级跳过
            continue
        for sub in virtuals:
            if len(out) >= MAX_PRIMITIVES:
                return
            _emit_entity(sub, block=block_name, ids=ids, out=out, depth=depth + 1)


def _grid_inserts(entity) -> list:
    """MINSERT 阵列拆成逐格 INSERT；非阵列原样返回单元素列表。"""
    rows = getattr(entity.dxf, "row_count", 1) or 1
    cols = getattr(entity.dxf, "column_count", 1) or 1
    if (rows > 1 or cols > 1) and hasattr(entity, "multi_insert"):
        try:
            return list(entity.multi_insert())
        except Exception:  # noqa: BLE001 — 拆分失败 → 退回单格
            return [entity]
    return [entity]


def _entity_to_primitive(
    entity, kind: str, layer: str, block: str, pid: int
) -> Primitive | None:
    if kind == "LINE":
        s, e = entity.dxf.start, entity.dxf.end
        return Primitive(
            id=pid, type="line",
            points=((float(s.x), float(s.y)), (float(e.x), float(e.y))),
            layer=layer, block=block,
        )
    if kind in ("LWPOLYLINE", "POLYLINE"):
        pts = _polyline_points(entity, kind)
        if len(pts) < 2:
            return None
        closed = bool(getattr(entity, "closed", False)) and len(pts) >= 3
        return Primitive(
            id=pid, type="polyline",
            points=tuple(pts), layer=layer, block=block, closed=closed,
        )
    if kind == "SOLID":
        pts = [
            (float(entity.dxf.vtx0.x), float(entity.dxf.vtx0.y)),
            (float(entity.dxf.vtx1.x), float(entity.dxf.vtx1.y)),
            (float(entity.dxf.vtx2.x), float(entity.dxf.vtx2.y)),
        ]
        return Primitive(
            id=pid, type="polyline", points=tuple(pts),
            layer=layer, block=block, closed=True, filled=True,
        )
    if kind in ("TEXT", "MTEXT"):
        insert = entity.dxf.insert
        content = entity.plain_mtext() if kind == "MTEXT" else entity.dxf.text
        if content and content.strip():
            return Primitive(
                id=pid, type="text",
                points=((float(insert.x), float(insert.y)),),
                layer=layer, block=block, content=content.strip(),
            )
    return None


def _polyline_points(entity, kind: str) -> list[tuple[float, float]]:
    if kind == "LWPOLYLINE":
        return [(float(p[0]), float(p[1])) for p in entity.get_points()]
    return [
        (float(v.dxf.location.x), float(v.dxf.location.y))
        for v in entity.vertices
    ]


def _page_size(doc, primitives: list[Primitive]) -> tuple[float, float]:
    try:
        ext_min = doc.header.get("$EXTMIN", (0, 0, 0))
        ext_max = doc.header.get("$EXTMAX", (0, 0, 0))
        w = float(ext_max[0]) - float(ext_min[0])
        h = float(ext_max[1]) - float(ext_min[1])
        if w > 0 and h > 0:
            return w, h
    except Exception:  # noqa: BLE001
        pass
    xs: list[float] = []
    ys: list[float] = []
    for p in primitives:
        for x, y in p.points:
            xs.append(x)
            ys.append(y)
    if xs and ys:
        return max(xs) - min(xs), max(ys) - min(ys)
    return 0.0, 0.0
