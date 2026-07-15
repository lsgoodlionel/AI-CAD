"""D-16 离线 OCR 后端评测基座。

在自建评测集上横向比较不同 OCR 后端（paddleocr / rapidocr / paddleocr_vl / mock）
在**本项目真正消费的三类 token**上的识别质量：

  - ``elevation``（标高）：数值容差匹配，喂 ``ocr/consume.py::elevation_candidates``
    → section-z / model_story。
  - ``axis``（轴号）：精确匹配（归一化后），喂 ``axis_anchors`` → 跨图配准。
  - ``title`` / ``room_name``（图名·房间名）：精确匹配，喂 ``space_labels`` → 语义树。

只评这三类，是因为它们是 ``consume.py`` 三条下游馈线真正读取的信号——其余
kind（dimension/note/other）不影响任何下游决策，评了也不能指导切换后端。

- ``metrics``：度量引擎（容差/精确匹配 + Precision/Recall/F1 + 置信标定）。
- ``harness``：多后端编排 → 可复现对比报告（``OcrComparisonReport``，**有金标签**）。
- ``unlabeled``：多后端编排 → 无金标签横向对比报告（``UnlabeledComparisonReport``）。
- ``report``：对比报告 → Markdown 渲染（``render_markdown`` / ``render_unlabeled_markdown``）。

诚实边界：结果只对**跑评测时实际可用的后端**打分；不可用后端如实标注
「不可用」，不用 0 分冒充「测过但很差」。默认后端回退顺序（paddle→rapid→mock）
由本模块的评测结果**参考**决定是否调整，本模块自身**不改**该顺序。

## 两档模式

- **有金标签**（``harness.run_backend_comparison`` + ``GoldLabels``）：算
  Precision/Recall/F1 + 置信标定，适合有人工标注真值的评测集。
- **无金标签**（``unlabeled.run_unlabeled_comparison``）：无真值时的默认模式
  （如上海大歌剧院全量真实图纸，未做人工标注）——产出后端间一致性
  （``PairwiseAgreement``，重合率而非准确率）、识别量与置信分布
  （``BackendVolumeMetrics``）、consume.py 三馈线产出量，用于横向比较
  后端强弱，不假装有真值。
"""
from __future__ import annotations

from .harness import (
    BackendMetrics,
    OcrComparisonReport,
    OcrEvalSample,
    run_backend_comparison,
)
from .metrics import (
    GoldLabels,
    OcrSampleMetrics,
    TokenSetMetrics,
    confidence_calibration,
    evaluate_ocr,
    match_elevation_values,
    match_label_set,
)
from .report import render_markdown, render_unlabeled_markdown
from .unlabeled import (
    AgreementMetrics,
    BackendVolumeMetrics,
    ConfidenceStats,
    PairwiseAgreement,
    UnlabeledComparisonReport,
    UnlabeledSample,
    pairwise_agreement,
    run_unlabeled_comparison,
)

__all__ = [
    "GoldLabels",
    "OcrSampleMetrics",
    "TokenSetMetrics",
    "confidence_calibration",
    "evaluate_ocr",
    "match_elevation_values",
    "match_label_set",
    "BackendMetrics",
    "OcrComparisonReport",
    "OcrEvalSample",
    "run_backend_comparison",
    "render_markdown",
    "AgreementMetrics",
    "BackendVolumeMetrics",
    "ConfidenceStats",
    "PairwiseAgreement",
    "UnlabeledComparisonReport",
    "UnlabeledSample",
    "pairwise_agreement",
    "run_unlabeled_comparison",
    "render_unlabeled_markdown",
]
