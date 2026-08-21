"""自训 YOLO 权重接入 Phase C 的 SpottingBackend 契约。

**推理必须与训练对称地切片**：模型是在 640px 块上训的，块内框中位
15.5×15.6 px；直接喂整图（框只有 3.7×4.8 px）等于喂它没见过的尺度，
检不出来是必然的——这个尺度问题在训练阶段已经量过一次，
推理端同样成立。

跨块重复必须去重：切片有 64px 重叠，同一根柱会在相邻两块各检出一次。
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from core.model3d.spotting.types import (SpottingResult, SymbolCandidate)
from core.model3d.yolo_export import CLASS_NAMES, TILE_OVERLAP_PX, TILE_PX

logger = logging.getLogger(__name__)

#: 默认权重位置。训练产物 `best.pt`，随镜像交付或挂载进来。
DEFAULT_WEIGHTS = os.getenv(
    "CAD_YOLO_WEIGHTS", "/app/data/model3d/weights/cad_yolo_best.pt")

#: 跨块去重的 IoU 阈值。
DEDUPE_IOU = 0.5

#: 低于此置信度的候选丢弃——宁可漏，不要给下游一堆噪声。
MIN_CONFIDENCE = 0.25


def tile_box_to_page(box: tuple, tile: tuple) -> tuple:
    """块内像素框 → 整图像素框。

    不换算的话**每个框都落在左上角 640px 里**。
    """
    x0, y0, _x1, _y1 = tile
    return (box[0] + x0, box[1] + y0, box[2] + x0, box[3] + y0)


def _iou(a: tuple, b: tuple) -> float:
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    inter = (ix1 - ix0) * (iy1 - iy0)
    area_a = max(a[2] - a[0], 0) * max(a[3] - a[1], 0)
    area_b = max(b[2] - b[0], 0) * max(b[3] - b[1], 0)
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def dedupe_boxes(boxes: list | None, iou_threshold: float = DEDUPE_IOU) -> list:
    """跨块去重。

    **不同类别不合并**——柱与板重叠是常态（柱站在板上）。
    重复时保留置信度更高的那个。
    """
    ordered = sorted(boxes or [], key=lambda b: -float(b.get("confidence", 0)))
    kept: list = []
    for box in ordered:
        if any(box.get("category") == k.get("category")
               and _iou(box["bbox"], k["bbox"]) >= iou_threshold for k in kept):
            continue
        kept.append(box)
    return kept


class YoloSpottingBackend:
    """自训 YOLO 权重后端。"""

    name = "yolo_cad"

    def __init__(self, weights_path: str | None = None):
        self.weights_path = weights_path or DEFAULT_WEIGHTS
        self._model = None

    def is_available(self) -> bool:
        """权重与依赖是否就绪；不就绪由服务降级到别的后端，绝不硬失败。"""
        if not Path(self.weights_path).is_file():
            return False
        try:
            import ultralytics  # noqa: F401
        except ImportError:
            return False
        return True

    def spot(self, doc) -> SpottingResult:
        """图元文档 → 符号候选。

        **权重缺失要明确说出来**，不能静默返回空结果——
        那会让「模型没接上」看起来像「这张图没有构件」。
        """
        if not Path(self.weights_path).is_file():
            return SpottingResult(
                backend=self.name,
                warnings=(f"YOLO 权重不存在：{self.weights_path}"
                          "（模型未接上，不是这张图没有构件）",))
        try:
            candidates = self._infer(doc)
        except Exception as exc:  # noqa: BLE001 — 契约要求绝不跨边界抛异常
            logger.warning("YOLO 推理失败：%s", exc)
            return SpottingResult(backend=self.name,
                                  warnings=(f"YOLO 推理失败：{exc}",))
        return SpottingResult(candidates=tuple(candidates), backend=self.name)

    def _infer(self, doc) -> list[SymbolCandidate]:
        """切片推理 + 跨块去重。`doc` 需带 `image`（PIL）或 `image_path`。"""
        from PIL import Image
        from ultralytics import YOLO

        from core.model3d.yolo_export import tile_grid

        if self._model is None:
            self._model = YOLO(self.weights_path)
        image = getattr(doc, "image", None)
        if image is None:
            path = getattr(doc, "image_path", None)
            if not path:
                return []
            image = Image.open(path)
        width, height = image.size

        raw: list[dict] = []
        for tile in tile_grid(width, height, TILE_PX, TILE_OVERLAP_PX):
            crop = image.crop(tile).convert("RGB")
            for result in self._model.predict(crop, verbose=False):
                for box in result.boxes:
                    conf = float(box.conf[0])
                    if conf < MIN_CONFIDENCE:
                        continue
                    xyxy = tuple(float(v) for v in box.xyxy[0])
                    cls = int(box.cls[0])
                    raw.append({
                        "bbox": tile_box_to_page(xyxy, tile),
                        "confidence": conf,
                        "category": CLASS_NAMES[cls] if cls < len(CLASS_NAMES)
                        else str(cls),
                    })
        return [
            SymbolCandidate(category=b["category"], confidence=b["confidence"],
                            bbox=b["bbox"], source="model",
                            evidence={"backend": self.name,
                                      "weights": self.weights_path})
            for b in dedupe_boxes(raw)
        ]
