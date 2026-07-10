"""C-02 预处理器：DXF/DWG/PDF → 统一 SVG + 图元 JSON。

模型（CADTransformer / VecFormer）不吃 DXF/DWG，本包提供统一入口把图纸转成
所有开源模型能吃的 **SVG + 图元序列**，并透传图层/块元数据供 C-04 自动标注复用。

复用现有栈，不重复造轮子：
- ``core.model3d.geometry_extractor``：已提取精确图元 + 图层/块并行列表；
- ``core.ai_review.dwg_support``：DWG → DXF（ODA / LibreDWG，缺失优雅降级）。

异常一律优雅降级为空文档 + warning，绝不抛出（对齐 ``element_recognizer`` 风格）。
"""
from __future__ import annotations

import logging

from core.model3d.types import DrawingGeometry

from .block_expander import expand_blocks
from .dxf_to_svg import doc_to_svg
from .normalize import NormalizeParams, normalize_doc
from .primitive_json import geometry_to_primitives
from .schema import (
    SCHEMA_VERSION,
    PreprocessResult,
    Primitive,
    PrimitiveDoc,
)

logger = logging.getLogger(__name__)

__all__ = [
    "SCHEMA_VERSION",
    "Primitive",
    "PrimitiveDoc",
    "PreprocessResult",
    "NormalizeParams",
    "preprocess_geometry",
    "preprocess_drawing",
    "expand_blocks",
    "normalize_doc",
    "doc_to_svg",
]

_DXF_EXTS = {"dxf", "dwg"}
_PDF_EXTS = {"pdf"}


def preprocess_geometry(
    geom: DrawingGeometry,
    *,
    source_ext: str = "",
    warnings: tuple[str, ...] = (),
) -> PreprocessResult:
    """从已提取的 ``DrawingGeometry`` 产出 SVG + 图元 JSON（纯函数，便于测试）。"""
    doc = geometry_to_primitives(geom, warnings=warnings)
    svg = doc_to_svg(doc)
    provenance = {
        "schema_version": SCHEMA_VERSION,
        "source_ext": source_ext,
        "primitive_count": len(doc.primitives),
    }
    return PreprocessResult(doc=doc, svg=svg, source_ext=source_ext, provenance=provenance)


def preprocess_drawing(data: bytes, file_ext: str) -> PreprocessResult:
    """从原始字节（DXF/DWG/PDF）产出预处理结果。

    - ``dxf`` / ``dwg``：经 ``extract_dxf_geometry``（内部含 DWG→DXF 与 INSERT 展开）。
    - ``pdf``：经 ``extract_pdf_geometry``（首页矢量提取）。
    - 未知扩展名 / 提取失败：返回空文档 + warning，不抛异常。
    """
    ext = (file_ext or "").lower().lstrip(".")
    warnings: list[str] = []
    geom = DrawingGeometry()

    try:
        if ext in _DXF_EXTS:
            from core.model3d.geometry_extractor import extract_dxf_geometry

            geom = extract_dxf_geometry(data)
        elif ext in _PDF_EXTS:
            from core.model3d.geometry_extractor import extract_pdf_geometry

            geom = extract_pdf_geometry(data)
        else:
            warnings.append(f"不支持的扩展名「{ext}」，产出空文档")
    except Exception as exc:  # noqa: BLE001 — 预处理失败优雅降级
        logger.warning("[preprocess] 几何提取失败(%s): %s", ext, exc)
        warnings.append(f"几何提取失败: {exc}")

    if geom.primitive_count() == 0 and not geom.texts and not warnings:
        warnings.append("未提取到任何图元（可能为扫描件或空图）")

    return preprocess_geometry(geom, source_ext=ext, warnings=tuple(warnings))
