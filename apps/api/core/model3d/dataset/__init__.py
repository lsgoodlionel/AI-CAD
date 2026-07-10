"""C-04 数据工程包：CAD 图层/块属性 → 弱标签自动标注引擎。

本包实现 Phase C 泳道 B 的「数据冷启动发动机」：借鉴 ArchCAD-400K「用 CAD 内在
图层/块属性自动标注」思路，把 C-03 展开后的 ``PrimitiveDoc`` 批量转成带弱标签的
样本（噪声可接受，人工在 C-06 精修）。

对外 API 见 ``auto_label``：
    - ``LabeledPrimitive`` / ``AutoLabelResult``：不可变输出契约
    - ``auto_label(doc, *, extra_map=None)``：逐图元弱标注
    - ``weak_label_report(labeled)``：弱标注质量报告
    - ``LayerClassMap`` / ``load_layer_class_map``：补充映射表（YAML，可维护）
"""
from __future__ import annotations

from .auto_label import (
    AutoLabelResult,
    LabeledPrimitive,
    LayerClassMap,
    auto_label,
    load_layer_class_map,
    weak_label_report,
)

__all__ = [
    "AutoLabelResult",
    "LabeledPrimitive",
    "LayerClassMap",
    "auto_label",
    "load_layer_class_map",
    "weak_label_report",
]
