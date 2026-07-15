"""GraphRAG 融合召回层（D-18，Phase D 泳道5 · 研究型升级）。

对外只暴露 ``run_graphrag_fusion`` 编排入口与数据契约，不导出内部实现细节。
详见 docs/PHASE_D_GRAPHRAG.md（设计 + 合规审查评价标准判定建议 + 评测口径）。
"""
from .fusion import DEFAULT_CONFIG, run_graphrag_fusion
from .types import (
    FusionCandidate,
    FusionConfig,
    GraphRAGFusionResult,
    RetrievalCandidate,
)

__all__ = [
    "DEFAULT_CONFIG",
    "run_graphrag_fusion",
    "FusionCandidate",
    "FusionConfig",
    "GraphRAGFusionResult",
    "RetrievalCandidate",
]
