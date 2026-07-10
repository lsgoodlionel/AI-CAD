"""符号 spotting 共享契约（C-08/C-12/C-13 的公共缝）。

统一「图纸 → 符号候选 + 置信度 + 来源」的稳定契约，使推理封装（C-08）、
推理服务（C-12）、融合引擎（C-13）可契约先行、并行开发、互不耦合具体实现。

类别对齐既有 9 类 taxonomy（``layer_conventions`` / C-04）：
``column/beam/slab/wall/door/window/pipe/equipment/axis``（或其精化子类）。
坐标沿用 ``PrimitiveDoc`` 的页面点（pt）或归一化域，由后端在 evidence 标注口径。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable

from core.model3d.preprocess.schema import PrimitiveDoc

# 候选来源：model=学习模型 / rule=确定性规则 / fused=融合裁决（C-13 用）
CandidateSource = Literal["model", "rule", "fused"]


@dataclass(frozen=True)
class SymbolCandidate:
    """单个符号识别候选。"""
    category: str                                    # 9 类 taxonomy 或其子类
    confidence: float                                # 0~1
    bbox: tuple[float, float, float, float]          # (x_min, y_min, x_max, y_max)
    source: CandidateSource = "model"
    mep_system: str | None = None                    # 消防/给排水/电气/暖通 或 None
    primitive_ids: tuple[int, ...] = ()              # 关联的 PrimitiveDoc 图元 id
    evidence: dict = field(default_factory=dict)     # {backend, model, ...}

    def to_dict(self) -> dict:
        return {
            "category": self.category,
            "confidence": self.confidence,
            "bbox": list(self.bbox),
            "source": self.source,
            "mep_system": self.mep_system,
            "primitive_ids": list(self.primitive_ids),
            "evidence": self.evidence,
        }


@dataclass(frozen=True)
class SpottingResult:
    """一张图纸的 spotting 输出。"""
    candidates: tuple[SymbolCandidate, ...] = ()
    backend: str = ""                                # cadtransformer / mock / vecformer
    warnings: tuple[str, ...] = ()

    @property
    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for c in self.candidates:
            out[c.category] = out.get(c.category, 0) + 1
        return out

    def to_dict(self) -> dict:
        return {
            "backend": self.backend,
            "counts": self.counts,
            "candidates": [c.to_dict() for c in self.candidates],
            "warnings": list(self.warnings),
        }


@runtime_checkable
class SpottingBackend(Protocol):
    """符号识别后端抽象。CADTransformer(C-08) / VecFormer(C-10) / Mock 均实现之。"""

    name: str

    def is_available(self) -> bool:
        """权重/依赖/设备是否就绪（不就绪则服务降级到 mock，绝不硬失败）。"""
        ...

    def spot(self, doc: PrimitiveDoc) -> SpottingResult:
        """图元文档 → 符号候选。实现须优雅降级，绝不跨边界抛异常。"""
        ...
