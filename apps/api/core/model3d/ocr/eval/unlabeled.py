"""D-16 无金标签评测（后端间一致性 + 识别量/置信分布 + 三馈线产出量）。

上海大歌剧院这类真实项目图纸**没有 OCR 金标签**（人工标注标高/轴号/图名真值
的成本极高，且不是本次目标）。对这类语料，``harness.py`` 的 Precision/Recall
（需要金标签才能定义 TP/FP/FN）无从谈起——本模块提供诚实的替代方案，**不假装
有真值**，只产出可横向比较后端强弱的三类信号：

1. **后端间一致性**（``pairwise_agreement`` / ``PairwiseAgreement``）：同一张图
   两个后端各自识别出的标高/轴号/图名·房间名，用与 ``metrics.py`` 相同的
   容差数值匹配 / 归一化字符串匹配算法两两比较，输出 ``matched/only_a/only_b``
   计数 + 对称的 Jaccard 重合率。**这不是准确率**——两个后端一致不代表它们都对
   （可能一起读错），不一致也不代表谁错——只说明"重合多少"，供人判断哪个
   后端更值得信赖（例如与已知稳定后端一致性高的新后端更可信）。
2. **识别量与置信分布**（``BackendVolumeMetrics``）：各 kind 的 token 数量 +
   置信度均值/中位数/极值——识别量过低（漏检多）或置信度普遍偏低都是后端
   偏弱的直接信号，无需金标签即可判断。
3. **三馈线产出量**（``consume_elevation_count`` 等）：过 ``consume.py`` 默认
   置信门槛后，真正能喂给 section-z / 跨图配准 / 语义树的候选数量——这是
   对下游最有意义的"有效识别量"，比原始 token 数更贴近实际价值。

聚合口径与 ``harness.py`` 一致：逐样本匹配后按 tp/fp/fn 求和再算比率，不做
跨样本拼接匹配（理由同 ``harness.py`` 模块文档）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations
from statistics import mean, median

from ..consume import axis_anchors, elevation_candidates, space_labels
from ..service import run_ocr
from ..types import OcrBackend, OcrResult
from .metrics import (
    DEFAULT_ELEVATION_TOLERANCE_M,
    TokenSetMetrics,
    match_elevation_values,
    match_label_set,
)

# title 一致性口径与 metrics.py::evaluate_ocr 对齐：title + room_name 合并比较
_TITLE_KINDS = ("title", "room_name")


@dataclass(frozen=True)
class UnlabeledSample:
    """单张图纸的无金标签评测样本：只有原始字节，没有真值。"""
    file_bytes: bytes
    file_ext: str = "pdf"
    sample_id: str = ""


@dataclass(frozen=True)
class ConfidenceStats:
    """某 kind 的置信度描述统计（``count=0`` 时其余字段为 ``None``，如实标注无数据）。"""
    count: int
    mean: float | None
    median: float | None
    min: float | None
    max: float | None

    def to_dict(self) -> dict:
        return {
            "count": self.count,
            "mean": round(self.mean, 4) if self.mean is not None else None,
            "median": round(self.median, 4) if self.median is not None else None,
            "min": round(self.min, 4) if self.min is not None else None,
            "max": round(self.max, 4) if self.max is not None else None,
        }


def _confidence_stats(confidences: list[float]) -> ConfidenceStats:
    if not confidences:
        return ConfidenceStats(count=0, mean=None, median=None, min=None, max=None)
    return ConfidenceStats(
        count=len(confidences),
        mean=mean(confidences),
        median=median(confidences),
        min=min(confidences),
        max=max(confidences),
    )


@dataclass(frozen=True)
class BackendVolumeMetrics:
    """单个后端在整个样本集上的识别量 + 三馈线产出量（无真值，纯描述统计）。"""
    backend_name: str
    sample_count: int
    unavailable_samples: int
    kind_counts: dict[str, int]
    confidence_by_kind: dict[str, ConfidenceStats]
    consume_elevation_count: int
    consume_axis_count: int
    consume_title_count: int
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "backend_name": self.backend_name,
            "sample_count": self.sample_count,
            "unavailable_samples": self.unavailable_samples,
            "kind_counts": dict(self.kind_counts),
            "confidence_by_kind": {k: v.to_dict() for k, v in self.confidence_by_kind.items()},
            "consume_elevation_count": self.consume_elevation_count,
            "consume_axis_count": self.consume_axis_count,
            "consume_title_count": self.consume_title_count,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class AgreementMetrics:
    """两后端在一类 token 上的一致性（无真值，仅描述重合度，不是 precision/recall）。

    ``matched``：容差数值匹配 / 归一化字符串匹配算法找到的重合对数。
    ``only_a`` / ``only_b``：仅某一后端识别到、另一后端未识别到的数量。
    ``jaccard``：``matched / (matched + only_a + only_b)``，对称重合率——两后端
    都没识别到任何该类 token 时（分母为 0）返回 ``None``（不可判定，不用 1.0
    冒充"完全一致"）。
    """
    matched: int
    only_a: int
    only_b: int
    jaccard: float | None

    def to_dict(self) -> dict:
        return {
            "matched": self.matched,
            "only_a": self.only_a,
            "only_b": self.only_b,
            "jaccard": round(self.jaccard, 4) if self.jaccard is not None else None,
        }


@dataclass(frozen=True)
class PairwiseAgreement:
    """一对后端在整个样本集上的一致性（三类 token 各一份）。"""
    backend_a: str
    backend_b: str
    comparable_samples: int
    elevation: AgreementMetrics
    axis: AgreementMetrics
    title: AgreementMetrics

    def to_dict(self) -> dict:
        return {
            "backend_a": self.backend_a,
            "backend_b": self.backend_b,
            "comparable_samples": self.comparable_samples,
            "elevation": self.elevation.to_dict(),
            "axis": self.axis.to_dict(),
            "title": self.title.to_dict(),
        }


@dataclass(frozen=True)
class UnlabeledComparisonReport:
    """无金标签多后端对比报告。"""
    backends: dict[str, BackendVolumeMetrics] = field(default_factory=dict)
    pairwise: tuple[PairwiseAgreement, ...] = ()
    sample_count: int = 0
    elevation_tolerance_m: float = DEFAULT_ELEVATION_TOLERANCE_M
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "sample_count": self.sample_count,
            "elevation_tolerance_m": self.elevation_tolerance_m,
            "backends": {k: v.to_dict() for k, v in self.backends.items()},
            "pairwise": [p.to_dict() for p in self.pairwise],
            "notes": list(self.notes),
        }


def _sum_token_metrics(items: list[TokenSetMetrics]) -> TokenSetMetrics:
    """按 tp/fp/fn 求和后重算比率（同 ``harness.py`` 的跨样本聚合口径）。"""
    tp = sum(m.tp for m in items)
    fp = sum(m.fp for m in items)
    fn = sum(m.fn for m in items)
    denom_p = tp + fp
    denom_r = tp + fn
    precision = tp / denom_p if denom_p else 0.0
    recall = tp / denom_r if denom_r else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return TokenSetMetrics(tp, fp, fn, precision, recall, f1)


def _to_agreement(m: TokenSetMetrics) -> AgreementMetrics:
    """``TokenSetMetrics``（视 A 为 pred、B 为 gold 算出的 tp/fp/fn）→ 对称一致性。

    ``fp`` 即「A 有、B 没有」= only_a；``fn`` 即「B 有、A 没有」= only_b；
    两者与 ``tp`` 一起构成对称的 Jaccard 分母，故转换后不再有"谁是金标签"之分。
    """
    denom = m.tp + m.fp + m.fn
    jaccard = m.tp / denom if denom else None
    return AgreementMetrics(matched=m.tp, only_a=m.fp, only_b=m.fn, jaccard=jaccard)


def pairwise_agreement(
    results_a: list[OcrResult],
    results_b: list[OcrResult],
    *,
    elevation_tolerance_m: float = DEFAULT_ELEVATION_TOLERANCE_M,
) -> tuple[AgreementMetrics, AgreementMetrics, AgreementMetrics, int]:
    """两个后端在同一批样本（逐张对齐）上的三类 token 一致性。

    仅统计双方在该样本上均 ``available`` 的样本（否则"不一致"可能只是因为
    一方压根没跑，而非读得不一样，混进去会误导一致性判断）。返回
    ``(elevation, axis, title, comparable_samples)``。
    """
    elevation_parts: list[TokenSetMetrics] = []
    axis_parts: list[TokenSetMetrics] = []
    title_parts: list[TokenSetMetrics] = []
    comparable = 0
    for ra, rb in zip(results_a, results_b):
        if not ra.available or not rb.available:
            continue
        comparable += 1

        elev_pred = [(t.value, t.confidence) for t in ra.of_kind("elevation") if t.value is not None]
        elev_gold = [t.value for t in rb.of_kind("elevation") if t.value is not None]
        m_elev, _ = match_elevation_values(elev_pred, elev_gold, tolerance_m=elevation_tolerance_m)
        elevation_parts.append(m_elev)

        axis_pred = [(t.text, t.confidence) for t in ra.of_kind("axis")]
        axis_gold = [t.text for t in rb.of_kind("axis")]
        m_axis, _ = match_label_set(axis_pred, axis_gold)
        axis_parts.append(m_axis)

        title_pred = [(t.text, t.confidence) for t in ra.tokens if t.kind in _TITLE_KINDS]
        title_gold = [t.text for t in rb.tokens if t.kind in _TITLE_KINDS]
        m_title, _ = match_label_set(title_pred, title_gold)
        title_parts.append(m_title)

    zero = TokenSetMetrics(0, 0, 0, 0.0, 0.0, 0.0)
    elevation = _to_agreement(_sum_token_metrics(elevation_parts) if elevation_parts else zero)
    axis = _to_agreement(_sum_token_metrics(axis_parts) if axis_parts else zero)
    title = _to_agreement(_sum_token_metrics(title_parts) if title_parts else zero)
    return elevation, axis, title, comparable


def _volume_metrics(
    name: str, results: list[OcrResult], *, consume_min_confidence: float
) -> BackendVolumeMetrics:
    kind_counts: dict[str, int] = {}
    confidences_by_kind: dict[str, list[float]] = {}
    warnings: list[str] = []
    unavailable = 0
    consume_elev = consume_axis = consume_title = 0

    for result in results:
        if not result.available:
            unavailable += 1
        warnings.extend(result.warnings)
        for kind, count in result.kind_counts.items():
            kind_counts[kind] = kind_counts.get(kind, 0) + count
        for token in result.tokens:
            confidences_by_kind.setdefault(token.kind, []).append(token.confidence)
        consume_elev += len(elevation_candidates(result, min_confidence=consume_min_confidence))
        consume_axis += len(axis_anchors(result, min_confidence=consume_min_confidence))
        consume_title += len(space_labels(result, min_confidence=consume_min_confidence))

    return BackendVolumeMetrics(
        backend_name=name,
        sample_count=len(results),
        unavailable_samples=unavailable,
        kind_counts=kind_counts,
        confidence_by_kind={k: _confidence_stats(v) for k, v in confidences_by_kind.items()},
        consume_elevation_count=consume_elev,
        consume_axis_count=consume_axis,
        consume_title_count=consume_title,
        warnings=tuple(dict.fromkeys(warnings)),
    )


def run_unlabeled_comparison(
    samples: list[UnlabeledSample],
    backends: dict[str, OcrBackend],
    *,
    dpi: int = 200,
    elevation_tolerance_m: float = DEFAULT_ELEVATION_TOLERANCE_M,
    consume_min_confidence: float = 0.6,
) -> UnlabeledComparisonReport:
    """无金标签多后端横向比较：一致性 + 识别量/置信分布 + 三馈线产出量。

    每个后端在样本集上只跑一次 OCR（结果复用于该后端参与的所有两两比较），
    避免为 N 个后端做 C(N,2) 次两两比较时重复调用 ``run_ocr``。
    """
    results_by_backend: dict[str, list[OcrResult]] = {
        name: [run_ocr(s.file_bytes, s.file_ext, dpi=dpi, backend=backend) for s in samples]
        for name, backend in backends.items()
    }

    backend_metrics = {
        name: _volume_metrics(name, results, consume_min_confidence=consume_min_confidence)
        for name, results in results_by_backend.items()
    }

    pairwise: list[PairwiseAgreement] = []
    for name_a, name_b in combinations(sorted(backends), 2):
        elevation, axis, title, comparable = pairwise_agreement(
            results_by_backend[name_a],
            results_by_backend[name_b],
            elevation_tolerance_m=elevation_tolerance_m,
        )
        pairwise.append(
            PairwiseAgreement(
                backend_a=name_a, backend_b=name_b, comparable_samples=comparable,
                elevation=elevation, axis=axis, title=title,
            )
        )

    notes = (
        "本报告无金标签，全部指标为后端间横向比较（重合率/识别量/置信分布），"
        "不是准确率——两后端一致不代表都对，不一致不代表谁错。",
        "一致性（Jaccard）只统计双方在该样本均可用（available）时的比较，"
        "避免把「一方没跑起来」误算成「读得不一样」。",
        "三馈线产出量已过 consume.py 默认置信门槛，是真正到达下游的候选数，"
        "比原始 token 数更贴近实际价值。",
        "结论仅供参考是否调整默认回退顺序（paddle→rapid→mock），"
        "本评测基座本身不修改 service.py 的回退顺序。",
    )
    return UnlabeledComparisonReport(
        backends=backend_metrics,
        pairwise=tuple(pairwise),
        sample_count=len(samples),
        elevation_tolerance_m=elevation_tolerance_m,
        notes=notes,
    )
