"""PaddleOCR 后端（中文图纸全文识别，兼容 paddleocr 2.x / 3.x 双 API）。

重依赖 paddleocr/paddlepaddle **懒加载**；未安装或加载失败时 ``is_available``
返回 False，由 service 优雅降级到 mock/none。识别只负责「位图 → 文本框」，
分类与坐标换算交给 service。

API 差异（自适应）：
- 2.x：``PaddleOCR(use_angle_cls=True, lang="ch", show_log=False)``；
  ``ocr.ocr(arr, cls=True)`` 返回 ``[[ [box4pts, (text, conf)], ... ]]``。
- 3.x：``PaddleOCR(use_textline_orientation=True, lang="ch", ...)``（show_log 移除）；
  ``ocr.predict(arr)`` 返回 ``[{rec_texts, rec_scores, rec_polys|rec_boxes, ...}]``。
  工程图为平整单页，显式关掉文档方向分类/去弯曲，省权重与推理时间。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# 进程内缓存 PaddleOCR 实例（初始化重，模型加载一次复用）
_ocr_singleton = None
_load_failed = False

RawBox = tuple[str, tuple[float, float, float, float], float]


def _construct_paddleocr():
    """按 3.x → 2.x 顺序尝试构造（参数集不同，TypeError 即换代）。"""
    from paddleocr import PaddleOCR

    try:
        # 3.x：关文档方向/去弯曲（工程图是平整单页），保留文本行方向
        return PaddleOCR(
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=True,
            lang="ch",
        )
    except TypeError:
        # 2.x 参数集
        return PaddleOCR(use_angle_cls=True, lang="ch", show_log=False)


def _get_ocr():
    """懒加载 PaddleOCR 单例；失败返回 None 并置位 _load_failed。

    ⚠️ CAD_OCR_DISABLE_PADDLE=1 时直接禁用：paddlepaddle 3.x 原生推理引擎在
    linux/aarch64 容器构造 predictor 时 SIGSEGV——那是进程级崩溃，try/except
    拦不住，无法安全探测，只能由部署方显式关闭（service 会回退 RapidOCR）。
    """
    global _ocr_singleton, _load_failed
    if _ocr_singleton is not None:
        return _ocr_singleton
    if _load_failed:
        return None
    import os

    if os.environ.get("CAD_OCR_DISABLE_PADDLE") == "1":
        logger.info("[ocr.paddle] 已通过 CAD_OCR_DISABLE_PADDLE=1 显式禁用")
        _load_failed = True
        return None
    try:
        _ocr_singleton = _construct_paddleocr()
        return _ocr_singleton
    except Exception as exc:  # noqa: BLE001 — 未安装/加载失败一律降级
        logger.warning("[ocr.paddle] PaddleOCR 不可用，降级: %s", exc)
        _load_failed = True
        return None


def _poly_to_bbox(poly) -> tuple[float, float, float, float] | None:
    """4 点多边形（或 [x1,y1,x2,y2]）→ (x_min, y_min, x_max, y_max)。"""
    try:
        flat = [(float(p[0]), float(p[1])) for p in poly]
        xs = [p[0] for p in flat]
        ys = [p[1] for p in flat]
        return (min(xs), min(ys), max(xs), max(ys))
    except (TypeError, IndexError):
        try:
            x1, y1, x2, y2 = (float(v) for v in poly)
            return (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
        except Exception:  # noqa: BLE001
            return None


def _parse_v3_page(page) -> list[RawBox]:
    """解析 3.x 单页结果（dict-like：rec_texts / rec_scores / rec_polys|rec_boxes）。"""
    get = page.get if hasattr(page, "get") else lambda k, d=None: getattr(page, k, d)
    texts = get("rec_texts") or []
    scores = get("rec_scores") or []
    polys = get("rec_polys")
    boxes = polys if polys is not None and len(polys) else get("rec_boxes")
    out: list[RawBox] = []
    for i, text in enumerate(texts):
        conf = float(scores[i]) if i < len(scores) else 0.0
        bbox = _poly_to_bbox(boxes[i]) if boxes is not None and i < len(boxes) else None
        if bbox is None:
            continue
        out.append((str(text), bbox, conf))
    return out


def _parse_v2_page(page) -> list[RawBox]:
    """解析 2.x 单页结果（[[box4pts, (text, conf)], ...]）。"""
    out: list[RawBox] = []
    for line in page or []:
        try:
            box, (text, conf) = line[0], line[1]
            bbox = _poly_to_bbox(box)
            if bbox is None:
                continue
            out.append((str(text), bbox, float(conf)))
        except Exception:  # noqa: BLE001 — 跳过畸形行
            continue
    return out


def parse_paddle_output(raw) -> list[RawBox]:
    """按返回结构自适应解析 2.x / 3.x 输出（纯函数，离线可测）。"""
    out: list[RawBox] = []
    for page in raw or []:
        if page is None:
            continue
        is_v3 = hasattr(page, "get") or hasattr(page, "rec_texts")
        out.extend(_parse_v3_page(page) if is_v3 else _parse_v2_page(page))
    return out


class PaddleOcrBackend:
    """PaddleOCR 后端。"""

    name = "paddleocr"

    def is_available(self) -> bool:
        return _get_ocr() is not None

    def recognize(self, image_rgb, warnings: list[str]) -> list[RawBox]:
        ocr = _get_ocr()
        if ocr is None:
            warnings.append("paddleocr 不可用")
            return []
        try:
            import numpy as np

            arr = np.asarray(image_rgb)
            if hasattr(ocr, "predict"):
                raw = ocr.predict(arr)          # 3.x
            else:
                raw = ocr.ocr(arr, cls=True)    # 2.x
        except Exception as exc:  # noqa: BLE001
            logger.warning("[ocr.paddle] 识别失败: %s", exc)
            warnings.append(f"paddle 识别异常: {exc}")
            return []
        return parse_paddle_output(raw)
