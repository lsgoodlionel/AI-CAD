"""离线 mock spotting 后端（无 GPU/权重时的确定性桩）。

复用 C-04 自动标注（图层/块弱标签）从 ``PrimitiveDoc`` 合成符号候选，使
「预处理 → spotting → 融合」整链路在 CI（无 GPU）下可端到端跑通、可测试。
这是 mock 而非真实识别：候选来自确定性图层规则，非学习模型推理。
"""
from __future__ import annotations

import logging

from core.model3d.preprocess.schema import PrimitiveDoc

from .types import SpottingResult, SymbolCandidate

logger = logging.getLogger(__name__)


def _bbox_of(points: tuple[tuple[float, float], ...]) -> tuple[float, float, float, float] | None:
    if not points:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


class MockSpottingBackend:
    """基于 auto_label 弱标签的确定性 mock 后端。"""

    name = "mock"

    def is_available(self) -> bool:
        return True

    def spot(self, doc: PrimitiveDoc) -> SpottingResult:
        try:
            from core.model3d.dataset.auto_label import auto_label

            labeled = auto_label(doc).labeled
        except Exception as exc:  # noqa: BLE001 — mock 也优雅降级
            logger.warning("[spotting.mock] auto_label 失败: %s", exc)
            return SpottingResult(backend=self.name, warnings=(f"mock 降级: {exc}",))

        prim_by_id = {p.id: p for p in doc.primitives}
        candidates: list[SymbolCandidate] = []
        for lp in labeled:
            if lp.category is None:
                continue
            prim = prim_by_id.get(lp.primitive_id)
            if prim is None:
                continue
            bbox = _bbox_of(prim.points)
            if bbox is None:
                continue
            candidates.append(
                SymbolCandidate(
                    category=lp.category,
                    confidence=float(lp.confidence or 0.5),
                    bbox=bbox,
                    source="model",
                    mep_system=lp.mep_system,
                    primitive_ids=(lp.primitive_id,),
                    evidence={"backend": self.name, "label_source": lp.label_source},
                )
            )
        return SpottingResult(candidates=tuple(candidates), backend=self.name)
