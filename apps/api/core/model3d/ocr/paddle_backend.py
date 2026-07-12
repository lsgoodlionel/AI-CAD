"""PaddleOCR 后端（中文图纸全文识别）。

重依赖 paddleocr/paddlepaddle **懒加载**；未安装或加载失败时 ``is_available``
返回 False，由 service 优雅降级到 mock/none。识别只负责「位图 → 文本框」，
分类与坐标换算交给 service。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# 进程内缓存 PaddleOCR 实例（初始化重，模型加载一次复用）
_ocr_singleton = None
_load_failed = False


def _get_ocr():
    """懒加载 PaddleOCR 单例；失败返回 None 并置位 _load_failed。"""
    global _ocr_singleton, _load_failed
    if _ocr_singleton is not None:
        return _ocr_singleton
    if _load_failed:
        return None
    try:
        from paddleocr import PaddleOCR

        _ocr_singleton = PaddleOCR(use_angle_cls=True, lang="ch", show_log=False)
        return _ocr_singleton
    except Exception as exc:  # noqa: BLE001 — 未安装/加载失败一律降级
        logger.warning("[ocr.paddle] PaddleOCR 不可用，降级: %s", exc)
        _load_failed = True
        return None


class PaddleOcrBackend:
    """PaddleOCR 后端。"""

    name = "paddleocr"

    def is_available(self) -> bool:
        return _get_ocr() is not None

    def recognize(
        self, image_rgb, warnings: list[str]
    ) -> list[tuple[str, tuple[float, float, float, float], float]]:
        ocr = _get_ocr()
        if ocr is None:
            warnings.append("paddleocr 不可用")
            return []
        try:
            import numpy as np

            arr = np.asarray(image_rgb)
            raw = ocr.ocr(arr, cls=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[ocr.paddle] 识别失败: %s", exc)
            warnings.append(f"paddle 识别异常: {exc}")
            return []

        out: list[tuple[str, tuple[float, float, float, float], float]] = []
        # PaddleOCR 返回 [[ [box4pts, (text, conf)], ... ]]（按页）
        for page in raw or []:
            for line in page or []:
                try:
                    box, (text, conf) = line[0], line[1]
                    xs = [float(p[0]) for p in box]
                    ys = [float(p[1]) for p in box]
                    bbox = (min(xs), min(ys), max(xs), max(ys))
                    out.append((str(text), bbox, float(conf)))
                except Exception:  # noqa: BLE001 — 跳过畸形行
                    continue
        return out
