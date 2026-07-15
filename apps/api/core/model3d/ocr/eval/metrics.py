"""OCR 识别质量度量（锁定实现，纯 Python，无 numpy/scipy 依赖，离线可测）。

匹配口径（与 ``core/model3d/eval/metrics.py`` 的 IoU 匹配同精神，换成 OCR 语义）：

- **标高**（``elevation``）：数值容差匹配——预测值与最近的未占用金标签值之差
  ``<= tolerance_m`` 记为 TP（默认容差 0.05m，即 5cm，覆盖 OCR 读数抖动但不
  掩盖读错整数位的严重错误）。贪心按预测置信度降序配对，保证高置信预测优先
  拿到「最近」的金标签，避免低置信噪声抢占。
- **轴号 / 图名·房间名**（``axis`` / ``title``）：归一化后精确字符串匹配，多重集
  （允许同图重复出现的标签各自计数），同样按置信度降序贪心配对。
- 未匹配预测 = FP；未匹配金标签 = FN。Precision = TP/(TP+FP)，
  Recall = TP/(TP+FN)，F1 = 2PR/(P+R)。

**置信标定**（confidence calibration）：识别置信度与「该预测是否命中金标签」
的点二列相关系数（point-biserial correlation）——

    r_pb = (M1 - M0) / SD_conf * sqrt(p * q)

其中 M1/M0 为命中/未命中预测的平均置信度，SD_conf 为全体预测置信度的标准差，
p 为命中比例、q=1-p。r_pb 越接近 +1 说明「模型越自信，读得越对」（置信度可信、
可用于自动化门槛）；接近 0 或负值说明置信度不可靠，即便识别准确率高也不能
放宽人工复核门槛。样本不足或退化（全命中/全未命中/置信度无方差）时返回
``None``（诚实标注「不可判定」，不用 0.0 冒充「已判定为不相关」）。
"""
from __future__ import annotations

from dataclasses import dataclass

from ..types import OcrResult

# 标高数值匹配容差：OCR 读数抖动容忍（米）。CAD 标高惯例三位小数，
# 5cm 容差覆盖小数点误读但不掩盖读错米位的严重错误。
DEFAULT_ELEVATION_TOLERANCE_M = 0.05

# title/room_name 归一化：标题类文本大小写无意义（多为 CJK），仅去首尾空白。
_TITLE_KINDS = ("title", "room_name")


@dataclass(frozen=True)
class GoldLabels:
    """单张图纸的金标签（人工标注真值）。

    ``elevations``：米制标高值列表（可重复，如同一图多处标注 ±0.000）。
    ``axes``：轴号字符串列表（如 "1"/"A"/"1/A"）。
    ``titles``：图名/房间名字符串列表（title 与 room_name 合并评测，二者共用
    同一下游馈线 ``space_labels``，评测口径不细分子类）。
    """
    elevations: tuple[float, ...] = ()
    axes: tuple[str, ...] = ()
    titles: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "elevations": list(self.elevations),
            "axes": list(self.axes),
            "titles": list(self.titles),
        }


@dataclass(frozen=True)
class TokenSetMetrics:
    """一类 token 在一个（或聚合多个）样本上的 Precision/Recall/F1。"""
    tp: int
    fp: int
    fn: int
    precision: float
    recall: float
    f1: float

    def to_dict(self) -> dict:
        return {
            "tp": self.tp, "fp": self.fp, "fn": self.fn,
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1": round(self.f1, 4),
        }


@dataclass(frozen=True)
class OcrSampleMetrics:
    """单张图纸单个后端的三类 token 指标 + 置信标定用的命中/置信对。

    ``*_hits``：``[(is_correct, confidence), ...]``，每个**预测** token 一条
    （FN 金标签没有对应预测，不产生置信度，不计入标定）。供跨样本聚合后算
    ``confidence_calibration``。
    """
    elevation: TokenSetMetrics
    axis: TokenSetMetrics
    title: TokenSetMetrics
    elevation_hits: tuple[tuple[bool, float], ...] = ()
    axis_hits: tuple[tuple[bool, float], ...] = ()
    title_hits: tuple[tuple[bool, float], ...] = ()


def _prf(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return precision, recall, f1


def match_elevation_values(
    pred: list[tuple[float, float]],
    gold: list[float],
    *,
    tolerance_m: float = DEFAULT_ELEVATION_TOLERANCE_M,
) -> tuple[TokenSetMetrics, tuple[tuple[bool, float], ...]]:
    """标高数值容差匹配。``pred``: [(value_m, confidence), ...]。

    贪心按置信度降序处理预测；每个预测取「未占用金标签中距离最近且在容差内」
    的一个。返回 (指标, hits)，hits 与 ``pred`` 等长，标注每条预测是否命中。
    """
    remaining = list(gold)
    hits: list[tuple[bool, float]] = []
    tp = 0
    for value, conf in sorted(pred, key=lambda p: -p[1]):
        best_idx: int | None = None
        best_diff = tolerance_m
        for i, g in enumerate(remaining):
            diff = abs(g - value)
            if diff <= best_diff:
                best_idx = i
                best_diff = diff
        if best_idx is not None:
            remaining.pop(best_idx)
            tp += 1
            hits.append((True, conf))
        else:
            hits.append((False, conf))
    fp = len(pred) - tp
    fn = len(remaining)
    precision, recall, f1 = _prf(tp, fp, fn)
    return TokenSetMetrics(tp, fp, fn, precision, recall, f1), tuple(hits)


def _normalize_label(text: str, *, casefold: bool) -> str:
    stripped = text.strip()
    return stripped.casefold() if casefold else stripped


def match_label_set(
    pred: list[tuple[str, float]],
    gold: list[str],
    *,
    casefold: bool = True,
) -> tuple[TokenSetMetrics, tuple[tuple[bool, float], ...]]:
    """归一化后精确字符串匹配（多重集，允许重复标签各自计数）。

    ``pred``: [(text, confidence), ...]。贪心按置信度降序配对，任一未占用的
    金标签与预测归一化后相等即命中。
    """
    remaining_norm = [_normalize_label(g, casefold=casefold) for g in gold]
    hits: list[tuple[bool, float]] = []
    tp = 0
    for text, conf in sorted(pred, key=lambda p: -p[1]):
        norm = _normalize_label(text, casefold=casefold)
        if norm in remaining_norm:
            remaining_norm.remove(norm)
            tp += 1
            hits.append((True, conf))
        else:
            hits.append((False, conf))
    fp = len(pred) - tp
    fn = len(remaining_norm)
    precision, recall, f1 = _prf(tp, fp, fn)
    return TokenSetMetrics(tp, fp, fn, precision, recall, f1), tuple(hits)


def confidence_calibration(hits: list[tuple[bool, float]]) -> float | None:
    """置信度 vs 命中的点二列相关系数。样本<2 或退化时返回 ``None``。"""
    n = len(hits)
    if n < 2:
        return None
    confidences = [c for _, c in hits]
    labels = [1.0 if h else 0.0 for h, _ in hits]
    mean_conf = sum(confidences) / n
    var_conf = sum((c - mean_conf) ** 2 for c in confidences) / n
    if var_conf <= 0:
        return None
    std_conf = var_conf ** 0.5
    p = sum(labels) / n
    if p <= 0.0 or p >= 1.0:
        return None
    hit_confs = [c for c, label in zip(confidences, labels) if label == 1.0]
    miss_confs = [c for c, label in zip(confidences, labels) if label == 0.0]
    m1 = sum(hit_confs) / len(hit_confs)
    m0 = sum(miss_confs) / len(miss_confs)
    q = 1.0 - p
    return (m1 - m0) / std_conf * (p * q) ** 0.5


def evaluate_ocr(
    result: OcrResult,
    gold: GoldLabels,
    *,
    elevation_tolerance_m: float = DEFAULT_ELEVATION_TOLERANCE_M,
) -> OcrSampleMetrics:
    """单张图纸单个后端：OcrResult + 金标签 → 三类 token 指标。

    直接读 ``result.tokens``（不经 ``consume.py`` 的置信门槛），刻意评全部
    置信度区间的预测——评测要看到低置信预测的对错分布，才能算出有意义的
    置信标定；下游消费时的门槛过滤是另一层策略，不在本函数职责内。
    """
    elevation_pred = [
        (t.value, t.confidence) for t in result.of_kind("elevation") if t.value is not None
    ]
    elevation_metrics, elevation_hits = match_elevation_values(
        elevation_pred, list(gold.elevations), tolerance_m=elevation_tolerance_m
    )

    axis_pred = [(t.text, t.confidence) for t in result.of_kind("axis")]
    axis_metrics, axis_hits = match_label_set(axis_pred, list(gold.axes))

    title_pred = [(t.text, t.confidence) for t in result.tokens if t.kind in _TITLE_KINDS]
    title_metrics, title_hits = match_label_set(title_pred, list(gold.titles))

    return OcrSampleMetrics(
        elevation=elevation_metrics, axis=axis_metrics, title=title_metrics,
        elevation_hits=elevation_hits, axis_hits=axis_hits, title_hits=title_hits,
    )
