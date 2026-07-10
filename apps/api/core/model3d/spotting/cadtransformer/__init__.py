"""CADTransformer(MIT) 符号 spotting 推理封装（C-08）。

分层：
- ``adapter``：``PrimitiveDoc`` ↔ CADTransformer 输入/输出的纯函数适配（**无 torch 依赖**，可完整单测）。
- ``backend``：``CADTransformerBackend`` 实现 ``SpottingBackend`` Protocol，
  torch/dgl/torch-geometric 懒加载，缺依赖/权重时优雅降级到不可用。

合规：CADTransformer 许可 **MIT**（可商用，见 docs/PHASE_C_LICENSE_AUDIT.md §3.1）。
上游出处与许可证副本随封装保留（README 记录获取方式）。真实推理依赖见
``apps/api/requirements-spotting.txt``（独立 extra，不并入主 requirements）。
"""
from __future__ import annotations

from .adapter import (
    CADTInput,
    NodeFeature,
    NodePrediction,
    build_model_input,
    map_class_name,
    parse_predictions,
)
from .backend import CADTransformerBackend

__all__ = [
    "CADTransformerBackend",
    "CADTInput",
    "NodeFeature",
    "NodePrediction",
    "build_model_input",
    "parse_predictions",
    "map_class_name",
]
