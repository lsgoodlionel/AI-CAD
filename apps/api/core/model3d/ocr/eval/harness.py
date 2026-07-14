"""多后端 OCR 评测编排：一组图纸 + 金标签 → 可复现对比报告。

对每个候选后端，逐样本跑 ``run_ocr`` → ``evaluate_ocr``，再把逐样本的
TP/FP/FN **按类别求和**后重算 Precision/Recall/F1（而不是把跨样本的原始
预测/金标签直接拼接匹配——后者会让样本 A 的金标签被样本 B 的预测「误配对」，
数值/字符串匹配没有 IoU 匹配天然的空间局部性兜底，必须按样本隔离匹配再汇总）。

置信标定的 ``(is_correct, confidence)`` 对没有跨样本混淆风险（只是独立观测
点的集合），可以直接跨样本池化后统一算相关系数。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..service import run_ocr
from ..types import OcrBackend
from .metrics import (
    GoldLabels,
    TokenSetMetrics,
    confidence_calibration,
    evaluate_ocr,
)

_DEFAULT_ELEVATION_TOLERANCE_M = 0.05


@dataclass(frozen=True)
class OcrEvalSample:
    """单张图纸的评测样本：原始字节 + 金标签。"""
    file_bytes: bytes
    file_ext: str = "pdf"
    gold: GoldLabels = field(default_factory=GoldLabels)
    sample_id: str = ""


@dataclass(frozen=True)
class BackendMetrics:
    """单个后端在整个样本集上的聚合指标。"""
    backend_name: str
    elevation: TokenSetMetrics
    axis: TokenSetMetrics
    title: TokenSetMetrics
    elevation_calibration: float | None
    axis_calibration: float | None
    title_calibration: float | None
    overall_calibration: float | None
    sample_count: int
    unavailable_samples: int
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "backend_name": self.backend_name,
            "elevation": self.elevation.to_dict(),
            "axis": self.axis.to_dict(),
            "title": self.title.to_dict(),
            "elevation_calibration": self.elevation_calibration,
            "axis_calibration": self.axis_calibration,
            "title_calibration": self.title_calibration,
            "overall_calibration": self.overall_calibration,
            "sample_count": self.sample_count,
            "unavailable_samples": self.unavailable_samples,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class OcrComparisonReport:
    """多后端对比报告。"""
    backends: dict[str, BackendMetrics] = field(default_factory=dict)
    sample_count: int = 0
    elevation_tolerance_m: float = _DEFAULT_ELEVATION_TOLERANCE_M
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "sample_count": self.sample_count,
            "elevation_tolerance_m": self.elevation_tolerance_m,
            "backends": {k: v.to_dict() for k, v in self.backends.items()},
            "notes": list(self.notes),
        }


def _sum_counts(items: list[TokenSetMetrics]) -> TokenSetMetrics:
    tp = sum(m.tp for m in items)
    fp = sum(m.fp for m in items)
    fn = sum(m.fn for m in items)
    denom_p = tp + fp
    denom_r = tp + fn
    precision = tp / denom_p if denom_p else 0.0
    recall = tp / denom_r if denom_r else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return TokenSetMetrics(tp, fp, fn, precision, recall, f1)


def run_backend_comparison(
    samples: list[OcrEvalSample],
    backends: dict[str, OcrBackend],
    *,
    dpi: int = 200,
    elevation_tolerance_m: float = _DEFAULT_ELEVATION_TOLERANCE_M,
) -> OcrComparisonReport:
    """在样本集上跑多后端对比。``backends``: {显示名: OcrBackend 实例}。

    某后端在某样本上不可用（``OcrResult.available`` 为 False）时，该样本对
    这个后端贡献 0 条预测（金标签全部计为 FN），并计入 ``unavailable_samples``
    —— 如实反映「这个后端在这批图上根本没跑起来」，不能靠指标数值区分
    「跑了但读不准」与「压根没跑」，所以单列这个计数。
    """
    results: dict[str, BackendMetrics] = {}
    for name, backend in backends.items():
        elevation_parts: list[TokenSetMetrics] = []
        axis_parts: list[TokenSetMetrics] = []
        title_parts: list[TokenSetMetrics] = []
        elevation_hits: list[tuple[bool, float]] = []
        axis_hits: list[tuple[bool, float]] = []
        title_hits: list[tuple[bool, float]] = []
        warnings: list[str] = []
        unavailable = 0

        for sample in samples:
            result = run_ocr(
                sample.file_bytes, sample.file_ext, dpi=dpi, backend=backend
            )
            if not result.available:
                unavailable += 1
            warnings.extend(result.warnings)
            sample_metrics = evaluate_ocr(
                result, sample.gold, elevation_tolerance_m=elevation_tolerance_m
            )
            elevation_parts.append(sample_metrics.elevation)
            axis_parts.append(sample_metrics.axis)
            title_parts.append(sample_metrics.title)
            elevation_hits.extend(sample_metrics.elevation_hits)
            axis_hits.extend(sample_metrics.axis_hits)
            title_hits.extend(sample_metrics.title_hits)

        results[name] = BackendMetrics(
            backend_name=name,
            elevation=_sum_counts(elevation_parts),
            axis=_sum_counts(axis_parts),
            title=_sum_counts(title_parts),
            elevation_calibration=confidence_calibration(elevation_hits),
            axis_calibration=confidence_calibration(axis_hits),
            title_calibration=confidence_calibration(title_hits),
            overall_calibration=confidence_calibration(
                elevation_hits + axis_hits + title_hits
            ),
            sample_count=len(samples),
            unavailable_samples=unavailable,
            warnings=tuple(dict.fromkeys(warnings)),  # 去重保序
        )

    notes = (
        "指标按样本分别匹配后求和聚合（非跨样本拼接匹配），避免标高/轴号/图名"
        "的字符串·数值匹配在跨样本场景下产生虚假命中。",
        "unavailable_samples 反映后端在该样本集上「压根没跑起来」的次数，"
        "与「跑了但读不准」（体现在 recall/precision）是两回事，不可互相替代解读。",
        "本报告仅供参考是否调整默认回退顺序（paddle→rapid→mock），"
        "本评测基座本身不修改 service.py 的回退顺序。",
    )
    return OcrComparisonReport(
        backends=results,
        sample_count=len(samples),
        elevation_tolerance_m=elevation_tolerance_m,
        notes=notes,
    )
