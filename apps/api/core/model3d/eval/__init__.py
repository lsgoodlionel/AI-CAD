"""Phase C 统一评测基座（C-14）。

在自建 test 集上统一对比 **纯规则 vs 学习模型 vs 融合**，以 SymPoint（C-11，隔离
环境）为天花板参照。指标锁定实现（PQ / 精度 / 召回 / F1 / 分专业分类别 / 混淆矩阵），
规避「PQ 口径漂移导致与论文不可比」风险。

- ``metrics``：度量引擎（IoU 匹配 + Panoptic Quality + 精度召回 + 混淆矩阵）。
- ``harness``：三方法编排（rule/model/fusion）→ 可复现对比报告。

诚实边界：C-09 真实微调权重就绪前，「学习模型」端由 spotting mock 后端代入（规则派生），
model 对比暂为占位；harness 与指标本身就绪，权重到位后同一基座直接评真实模型。
"""
from __future__ import annotations

from .metrics import (
    GtBox,
    MethodMetrics,
    evaluate,
    iou,
)

__all__ = ["GtBox", "MethodMetrics", "evaluate", "iou"]
