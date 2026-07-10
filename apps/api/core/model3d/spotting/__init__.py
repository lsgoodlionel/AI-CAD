"""Phase C 符号 spotting（学习模型）子系统。

分层：
- ``types``：符号候选契约 ``SymbolCandidate`` / ``SpottingResult`` + 后端 ``SpottingBackend`` Protocol。
- ``mock_backend``：离线 mock 后端（无 GPU/权重时的确定性桩，供 CI 与融合链路端到端测试）。
- ``cadtransformer/``（C-08）：CADTransformer(MIT) 推理封装 + 输入适配 + 环境锁定。
- ``vecformer/``（C-10）：VecFormer(Apache2.0) 迁移占位（权重待释放）。
- ``service``（C-12）：spotting 推理微服务（接 ModelRouter 引擎治理，离线 mock 兜底）。

**合规边界**：本子系统只允许 CADTransformer(MIT) / VecFormer(Apache2.0)；SymPoint 系
（非商用⛔）绝不进入（见 docs/PHASE_C_LICENSE_AUDIT.md，CI license 门禁强制）。
"""
from __future__ import annotations

from .mock_backend import MockSpottingBackend
from .types import (
    CandidateSource,
    SpottingBackend,
    SpottingResult,
    SymbolCandidate,
)

__all__ = [
    "SymbolCandidate",
    "SpottingResult",
    "SpottingBackend",
    "CandidateSource",
    "MockSpottingBackend",
]
