"""学习模型 × 确定性规则融合策略引擎（C-13，Phase C 方法论核心）。

对外稳定入口：``fuse`` 融合两组 ``SymbolCandidate``（规则 / 模型）→
带 ``source`` + ``confidence`` 的 ``FusionResult``，规则强命中不被模型覆盖。
"""
from __future__ import annotations

from .arbitration import (
    DEFAULT_POLICY,
    FusionPolicy,
    arbitrate,
    bbox_iou,
    is_rule_strong,
    pair_candidates,
)
from .fusion_engine import (
    ArbitrationRecord,
    FusionResult,
    fuse,
    load_fusion_policy,
)

__all__ = [
    "fuse",
    "FusionResult",
    "ArbitrationRecord",
    "load_fusion_policy",
    "FusionPolicy",
    "DEFAULT_POLICY",
    "arbitrate",
    "pair_candidates",
    "bbox_iou",
    "is_rule_strong",
]
