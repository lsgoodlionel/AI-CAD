"""D-18 GraphRAG 合规审查评测基座（对齐 docs/PHASE_D_GRAPHRAG.md §3/§4）。

- ``metrics``：判定单元匹配 + precision/recall/F1 + 条文引用命中率 + 义务等级
  混淆矩阵 + 义务降级率 + FP/FN 细分（度量核心，纯函数）。
- ``harness``：kg_only / rag_only / graphrag 三方法对比编排（复用
  ``core/model3d/eval/harness.py`` 的三方法对比模式）。
- ``bootstrap_gold``：从 ``model_review_actions``（migrations/024）人审动作埋点
  自举评测金标准（冷启动来源，见 §3.6/§5）。
"""
from __future__ import annotations

from .bootstrap_gold import bootstrap_gold_from_review_actions
from .harness import (
    ComplianceComparisonReport,
    ComplianceEvalSample,
    run_compliance_comparison,
)
from .metrics import (
    ComplianceGt,
    ComplianceMetrics,
    evaluate_compliance,
    extract_obligation_level,
    normalize_regulation_ref,
)

__all__ = [
    "ComplianceGt",
    "ComplianceMetrics",
    "evaluate_compliance",
    "extract_obligation_level",
    "normalize_regulation_ref",
    "ComplianceEvalSample",
    "ComplianceComparisonReport",
    "run_compliance_comparison",
    "bootstrap_gold_from_review_actions",
]
