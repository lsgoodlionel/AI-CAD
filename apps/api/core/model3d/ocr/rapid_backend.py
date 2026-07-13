"""RapidOCR 后端（PP-OCR 模型的 ONNX Runtime 移植，中文识别）。

为什么需要它：paddlepaddle 3.x 的原生推理引擎在 linux/aarch64 容器
（Apple Silicon Docker）构造 predictor 时 SIGSEGV（3.0/3.1/3.2 均复现，
崩于 C++ `PreparePirProgram/SaveOrLoadPirParameters`）。RapidOCR 用同源
PP-OCR 检测/识别模型的 ONNX 版 + onnxruntime 推理，aarch64 稳定、依赖轻
（数十 MB，无 paddle 运行时），精度与 PP-OCR 同代模型一致。

后端优先级由 service 决定：paddle 可用用 paddle，否则回退本后端。
与 PaddleOcrBackend 一样懒加载 + 优雅降级。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_engine_singleton = None
_load_failed = False

RawBox = tuple[str, tuple[float, float, float, float], float]


def _get_engine():
    """懒加载 RapidOCR 单例；失败返回 None 并置位 _load_failed。"""
    global _engine_singleton, _load_failed
    if _engine_singleton is not None:
        return _engine_singleton
    if _load_failed:
        return None
    try:
        from rapidocr_onnxruntime import RapidOCR

        _engine_singleton = RapidOCR()
        return _engine_singleton
    except Exception as exc:  # noqa: BLE001 — 未安装/加载失败一律降级
        logger.warning("[ocr.rapid] RapidOCR 不可用，降级: %s", exc)
        _load_failed = True
        return None


def parse_rapid_output(raw) -> list[RawBox]:
    """解析 RapidOCR 输出（纯函数，离线可测）。

    结构：``[[box4pts, text, score], ...]``；box 为 4 点多边形。
    新版（rapidocr>=2）返回 ``RapidOCROutput`` 对象（.boxes/.txts/.scores），
    也一并兼容。
    """
    if raw is None:
        return []
    # 新版对象形态
    if hasattr(raw, "boxes") and hasattr(raw, "txts"):
        boxes = raw.boxes if raw.boxes is not None else []
        txts = raw.txts or []
        scores = raw.scores or []
        out: list[RawBox] = []
        for i, text in enumerate(txts):
            try:
                poly = boxes[i]
                xs = [float(p[0]) for p in poly]
                ys = [float(p[1]) for p in poly]
                conf = float(scores[i]) if i < len(scores) else 0.0
                out.append((str(text), (min(xs), min(ys), max(xs), max(ys)), conf))
            except Exception:  # noqa: BLE001
                continue
        return out
    # 旧版列表形态
    out = []
    for line in raw:
        try:
            poly, text, score = line[0], line[1], line[2]
            xs = [float(p[0]) for p in poly]
            ys = [float(p[1]) for p in poly]
            out.append((str(text), (min(xs), min(ys), max(xs), max(ys)), float(score)))
        except Exception:  # noqa: BLE001
            continue
    return out


class RapidOcrBackend:
    """RapidOCR（onnxruntime）后端。"""

    name = "rapidocr"

    def is_available(self) -> bool:
        return _get_engine() is not None

    def recognize(self, image_rgb, warnings: list[str]) -> list[RawBox]:
        engine = _get_engine()
        if engine is None:
            warnings.append("rapidocr 不可用")
            return []
        try:
            import numpy as np

            arr = np.asarray(image_rgb)
            result = engine(arr)
            # 旧版返回 (result, elapse) 元组；新版直接返回输出对象
            raw = result[0] if isinstance(result, tuple) else result
        except Exception as exc:  # noqa: BLE001
            logger.warning("[ocr.rapid] 识别失败: %s", exc)
            warnings.append(f"rapidocr 识别异常: {exc}")
            return []
        return parse_rapid_output(raw)
