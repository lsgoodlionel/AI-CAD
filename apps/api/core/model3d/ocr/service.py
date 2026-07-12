"""OCR 编排：渲染 → 后端识别 → 像素转页面点 → 分类 → OcrResult。

- ``run_ocr(file_bytes, ...)``：核心，纯字节入参，离线可测（配 mock 后端）。
- ``ocr_drawing(file_key, ...)``：薄封装，从 MinIO 取字节后调 run_ocr。

后端选择：显式传入优先；否则 PaddleOCR 可用则用之，不可用则降级到 none（返回
空 token + warning，绝不抛错阻断建模）。
"""
from __future__ import annotations

import logging

from .classify import classify_text
from .paddle_backend import PaddleOcrBackend
from .types import OcrBackend, OcrResult, TextToken

logger = logging.getLogger(__name__)

_DEFAULT_DPI = 200
_POINTS_PER_INCH = 72.0


def _render_first_page(file_bytes: bytes, file_ext: str, warnings: list[str], dpi: int):
    """返回 (image_rgb 或 None, page_size_pt)。best-effort，失败降级。"""
    ext = (file_ext or "").lower().lstrip(".")
    if ext == "pdf" or file_bytes[:5] == b"%PDF-":
        try:
            import fitz  # PyMuPDF

            doc = fitz.open(stream=file_bytes, filetype="pdf")
            if doc.page_count == 0:
                warnings.append("PDF 无页面")
                return None, (0.0, 0.0)
            page = doc[0]
            page_size = (float(page.rect.width), float(page.rect.height))
            pix = page.get_pixmap(dpi=dpi)
            from PIL import Image

            image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            return image, page_size
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"PDF 渲染失败: {exc}")
            return None, (0.0, 0.0)
    # 位图
    try:
        import io

        from PIL import Image

        image = Image.open(io.BytesIO(file_bytes)).convert("RGB")
        # 位图无"点"概念，按 dpi 折算等效页面点尺寸
        page_size = (image.width * _POINTS_PER_INCH / dpi, image.height * _POINTS_PER_INCH / dpi)
        return image, page_size
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"图像打开失败: {exc}")
        return None, (0.0, 0.0)


def run_ocr(
    file_bytes: bytes,
    file_ext: str = "pdf",
    *,
    dpi: int = _DEFAULT_DPI,
    backend: OcrBackend | None = None,
    min_confidence: float = 0.0,
) -> OcrResult:
    """对图纸首页做全文 OCR，返回结构化 token。

    坐标从渲染像素换算为页面点（point = pixel * 72 / dpi）。
    """
    warnings: list[str] = []
    active = backend or PaddleOcrBackend()

    if not active.is_available():
        warnings.append(f"OCR 后端 {getattr(active, 'name', '?')} 不可用，跳过（none）")
        return OcrResult(backend="none", dpi=dpi, warnings=tuple(warnings))

    image, page_size = _render_first_page(file_bytes, file_ext, warnings, dpi)
    # mock 后端不依赖 image；paddle 需要 image
    if image is None and getattr(active, "name", "") != "mock":
        warnings.append("无可识别位图，跳过（none）")
        return OcrResult(backend="none", dpi=dpi, page_size=page_size, warnings=tuple(warnings))

    raw = active.recognize(image, warnings)
    scale = _POINTS_PER_INCH / dpi  # 像素 → 点
    tokens: list[TextToken] = []
    for text, bbox_px, conf in raw:
        if conf < min_confidence:
            continue
        kind, value = classify_text(text)
        bbox_pt = (bbox_px[0] * scale, bbox_px[1] * scale, bbox_px[2] * scale, bbox_px[3] * scale)
        tokens.append(TextToken(text=text, bbox=bbox_pt, confidence=conf, kind=kind, value=value))

    return OcrResult(
        tokens=tuple(tokens),
        backend=getattr(active, "name", "unknown"),
        dpi=dpi,
        page_size=page_size,
        warnings=tuple(warnings),
    )


def ocr_drawing(
    file_key: str,
    file_ext: str = "pdf",
    *,
    dpi: int = _DEFAULT_DPI,
    backend: OcrBackend | None = None,
    min_confidence: float = 0.0,
) -> OcrResult:
    """从 MinIO 取图纸字节后 OCR。存储不可用时优雅降级。"""
    try:
        from core.storage import get_file_bytes

        data = get_file_bytes(file_key)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[ocr] 下载图纸失败 %s: %s", file_key, exc)
        return OcrResult(backend="none", dpi=dpi, warnings=(f"下载失败: {exc}",))
    return run_ocr(data, file_ext, dpi=dpi, backend=backend, min_confidence=min_confidence)
