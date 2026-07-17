"""OCR 编排：渲染 → 后端识别 → 像素转页面点 → 分类 → OcrResult。

- ``run_ocr(file_bytes, ...)``：核心，纯字节入参，离线可测（配 mock 后端）。
- ``ocr_drawing(file_key, ...)``：薄封装，从 MinIO 取字节后调 run_ocr。

后端选择：显式传入优先；否则按 PaddleOCR → RapidOCR 有序回退（Rapid 为
PP-OCR 模型的 onnxruntime 移植，paddle 原生引擎在 aarch64 容器 SIGSEGV 时的
稳定替代），全都不可用则降级到 none（返回空 token + warning，绝不抛错阻断建模）。
"""
from __future__ import annotations

import logging

from .classify import classify_text
from .paddle_backend import PaddleOcrBackend
from .rapid_backend import RapidOcrBackend
from .types import OcrBackend, OcrResult, TextToken

logger = logging.getLogger(__name__)

_DEFAULT_DPI = 200
_POINTS_PER_INCH = 72.0

# 分块识别：工程图多为 A0/A1，200dpi 渲染近万像素；OCR 检测器会把长边缩到
# ~1k px，正文小字（标高/轴号）缩到 1-2px 全部丢失，只有标题栏大字幸存。
# 超过 _TILE_THRESHOLD 的图自动切重叠块逐块识别，坐标平移回全图后 IoU 去重。
_TILE_THRESHOLD_PX = 2000
_TILE_SIZE_PX = 1600
_TILE_OVERLAP_PX = 200
# E-末 提速:渲染最长边上限(大图自适应降 dpi,限分块数;≈120dpi 等效仍够标签级文字)
_MAX_RENDER_PX = 6000
_DEDUP_IOU = 0.5


def _tile_origins(length: int, tile: int, overlap: int) -> list[int]:
    """一维铺块起点：步进 tile-overlap，最后一块贴齐末端（不越界、全覆盖）。"""
    if length <= tile:
        return [0]
    step = tile - overlap
    origins = list(range(0, length - tile, step))
    origins.append(length - tile)
    return origins


def _bbox_iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    if inter <= 0:
        return 0.0
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    return inter / max(area_a + area_b - inter, 1e-9)


def _dedup_raw(raw: list[tuple[str, tuple[float, float, float, float], float]]):
    """重叠区同一文本会被两块各识别一次：IoU 超阈值时保留高置信者。"""
    kept: list[tuple[str, tuple[float, float, float, float], float]] = []
    for cand in sorted(raw, key=lambda r: -r[2]):
        if any(_bbox_iou(cand[1], k[1]) >= _DEDUP_IOU for k in kept):
            continue
        kept.append(cand)
    return kept


def _recognize_tiled(active: OcrBackend, image, warnings: list[str]):
    """大图分块识别：逐块 OCR → 坐标平移回全图 → IoU 去重。小图直接整图。"""
    width, height = image.size
    if max(width, height) <= _TILE_THRESHOLD_PX:
        return active.recognize(image, warnings)
    raw: list[tuple[str, tuple[float, float, float, float], float]] = []
    xs = _tile_origins(width, _TILE_SIZE_PX, _TILE_OVERLAP_PX)
    ys = _tile_origins(height, _TILE_SIZE_PX, _TILE_OVERLAP_PX)
    for oy in ys:
        for ox in xs:
            tile = image.crop((ox, oy, min(ox + _TILE_SIZE_PX, width), min(oy + _TILE_SIZE_PX, height)))
            for text, bbox, conf in active.recognize(tile, warnings):
                raw.append((text, (bbox[0] + ox, bbox[1] + oy, bbox[2] + ox, bbox[3] + oy), conf))
    return _dedup_raw(raw)


def _select_backend(warnings: list[str]) -> OcrBackend | None:
    """按序挑第一个可用后端：paddle → rapid；全不可用返回 None。"""
    for backend in (PaddleOcrBackend(), RapidOcrBackend()):
        try:
            if backend.is_available():
                return backend
        except Exception as exc:  # noqa: BLE001 — 探测失败视为不可用
            warnings.append(f"{backend.name} 探测异常: {exc}")
    return None


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
            # 自适应降 DPI(E-末 提速):A0/A1 图 200dpi 近万像素 → 35 块 × ~8s ≈ 280s。
            # 限渲染最长边 ≤ _MAX_RENDER_PX,大图按需降 dpi,块数减 ~3x,标签级文字
            # (标高/轴号/房间)在等效 ~120dpi 仍清晰(实测标高 97% 高置信不受损)。
            longest_px = max(page.rect.width, page.rect.height) * dpi / 72.0
            eff_dpi = dpi if longest_px <= _MAX_RENDER_PX else dpi * _MAX_RENDER_PX / longest_px
            pix = page.get_pixmap(dpi=int(eff_dpi))
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
    active = backend if backend is not None else _select_backend(warnings)

    if active is None or not active.is_available():
        name = getattr(active, "name", "paddle/rapid") if active else "paddle/rapid"
        warnings.append(f"OCR 后端 {name} 均不可用，跳过（none）")
        return OcrResult(backend="none", dpi=dpi, warnings=tuple(warnings))

    image, page_size = _render_first_page(file_bytes, file_ext, warnings, dpi)
    # mock 后端不依赖 image；paddle 需要 image
    if image is None and getattr(active, "name", "") != "mock":
        warnings.append("无可识别位图，跳过（none）")
        return OcrResult(backend="none", dpi=dpi, page_size=page_size, warnings=tuple(warnings))

    raw = (
        _recognize_tiled(active, image, warnings)
        if image is not None
        else active.recognize(image, warnings)  # mock 不依赖 image
    )
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
