"""评测度量引擎（锁定实现，确保可复现、与论文口径一致）。

匹配口径（FloorPlanCAD Panoptic 一致）：
- 预测框与真值框 **同类别** 且 **IoU > 0.5** 记为 TP（IoU>0.5 保证唯一匹配）；
- 未匹配预测 = FP；未匹配真值 = FN。
- SQ（分割质量）= TP 的平均 IoU；RQ（识别质量）= TP / (TP + 0.5·FP + 0.5·FN)；
- **PQ = SQ × RQ**。
- 精度 = TP/(TP+FP)，召回 = TP/(TP+FN)，F1 = 2PR/(P+R)。

混淆矩阵在「按位置匹配（不看类别，IoU>0.5）」的配对上统计 (真值类别, 预测类别)，
额外含 __missed__（真值无位置匹配）与 __spurious__（预测无位置匹配）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

IOU_THRESHOLD = 0.5


class _Boxed(Protocol):
    category: str
    bbox: tuple[float, float, float, float]


@dataclass(frozen=True)
class GtBox:
    """真值框（金标签）。"""
    category: str
    bbox: tuple[float, float, float, float]
    mep_system: str | None = None


@dataclass(frozen=True)
class MethodMetrics:
    """单方法在一批样本上的聚合指标。"""
    tp: int
    fp: int
    fn: int
    precision: float
    recall: float
    f1: float
    sq: float
    rq: float
    pq: float
    per_category: dict[str, dict] = field(default_factory=dict)
    per_discipline: dict[str, dict] = field(default_factory=dict)
    confusion: dict[str, dict[str, int]] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "tp": self.tp, "fp": self.fp, "fn": self.fn,
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1": round(self.f1, 4),
            "sq": round(self.sq, 4),
            "rq": round(self.rq, 4),
            "pq": round(self.pq, 4),
            "per_category": self.per_category,
            "per_discipline": self.per_discipline,
            "confusion": self.confusion,
        }


def iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    """两个 (x_min, y_min, x_max, y_max) 框的交并比。退化框（零面积）→ 0。"""
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    iw, ih = max(0.0, ix1 - ix0), max(0.0, iy1 - iy0)
    inter = iw * ih
    area_a = max(0.0, ax1 - ax0) * max(0.0, ay1 - ay0)
    area_b = max(0.0, bx1 - bx0) * max(0.0, by1 - by0)
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def _match(
    gt: list,
    pred: list,
    iou_thr: float,
    *,
    category_aware: bool,
) -> tuple[list[tuple[int, int, float]], set[int], set[int]]:
    """贪心 IoU 匹配。返回 (matched[(gt_i,pred_i,iou)], 未匹配gt下标, 未匹配pred下标)。"""
    pairs: list[tuple[float, int, int]] = []
    for gi, g in enumerate(gt):
        for pi, p in enumerate(pred):
            if category_aware and g.category != p.category:
                continue
            score = iou(g.bbox, p.bbox)
            if score > iou_thr:
                pairs.append((score, gi, pi))
    pairs.sort(reverse=True)  # 高 IoU 优先配对（IoU>0.5 保证唯一性）
    used_gt: set[int] = set()
    used_pred: set[int] = set()
    matched: list[tuple[int, int, float]] = []
    for score, gi, pi in pairs:
        if gi in used_gt or pi in used_pred:
            continue
        used_gt.add(gi)
        used_pred.add(pi)
        matched.append((gi, pi, score))
    unmatched_gt = {i for i in range(len(gt))} - used_gt
    unmatched_pred = {i for i in range(len(pred))} - used_pred
    return matched, unmatched_gt, unmatched_pred


def _prf(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return precision, recall, f1


def _pq(matched_ious: list[float], tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    sq = sum(matched_ious) / tp if tp else 0.0
    denom = tp + 0.5 * fp + 0.5 * fn
    rq = tp / denom if denom else 0.0
    return sq, rq, sq * rq


def evaluate(
    gt: list,
    pred: list,
    *,
    iou_thr: float = IOU_THRESHOLD,
    discipline_of=None,
) -> MethodMetrics:
    """在一批（已展平的）真值/预测框上计算全指标。

    gt / pred 元素需有 ``category`` 与 ``bbox``（``GtBox`` / ``SymbolCandidate`` 均满足）。
    ``discipline_of``：category -> 专业 的映射函数（缺省不分专业）。
    """
    matched, un_gt, un_pred = _match(gt, pred, iou_thr, category_aware=True)
    tp, fp, fn = len(matched), len(un_pred), len(un_gt)
    precision, recall, f1 = _prf(tp, fp, fn)
    sq, rq, pq = _pq([m[2] for m in matched], tp, fp, fn)

    per_category = _breakdown(gt, pred, iou_thr, key=lambda c: c)
    per_discipline = (
        _breakdown(gt, pred, iou_thr, key=discipline_of) if discipline_of else {}
    )
    confusion = _confusion(gt, pred, iou_thr)

    return MethodMetrics(
        tp=tp, fp=fp, fn=fn, precision=precision, recall=recall, f1=f1,
        sq=sq, rq=rq, pq=pq,
        per_category=per_category, per_discipline=per_discipline, confusion=confusion,
    )


def _breakdown(gt: list, pred: list, iou_thr: float, *, key) -> dict[str, dict]:
    """按 key(category) 分组各算一次指标（分类别 / 分专业）。"""
    groups: dict[str, dict[str, list]] = {}
    for g in gt:
        k = key(g.category) or "未标注"
        groups.setdefault(k, {"gt": [], "pred": []})["gt"].append(g)
    for p in pred:
        k = key(p.category) or "未标注"
        groups.setdefault(k, {"gt": [], "pred": []})["pred"].append(p)
    out: dict[str, dict] = {}
    for k, grp in sorted(groups.items()):
        matched, un_gt, un_pred = _match(grp["gt"], grp["pred"], iou_thr, category_aware=True)
        tp, fp, fn = len(matched), len(un_pred), len(un_gt)
        precision, recall, f1 = _prf(tp, fp, fn)
        sq, rq, pq = _pq([m[2] for m in matched], tp, fp, fn)
        out[k] = {
            "tp": tp, "fp": fp, "fn": fn,
            "precision": round(precision, 4), "recall": round(recall, 4),
            "f1": round(f1, 4), "pq": round(pq, 4),
        }
    return out


def _confusion(gt: list, pred: list, iou_thr: float) -> dict[str, dict[str, int]]:
    """按位置匹配（不看类别）统计 (真值类别 → 预测类别)，含漏检/误报。"""
    matched, un_gt, un_pred = _match(gt, pred, iou_thr, category_aware=False)
    matrix: dict[str, dict[str, int]] = {}

    def _bump(row: str, col: str) -> None:
        matrix.setdefault(row, {})
        matrix[row][col] = matrix[row].get(col, 0) + 1

    for gi, pi, _ in matched:
        _bump(gt[gi].category, pred[pi].category)
    for gi in un_gt:
        _bump(gt[gi].category, "__missed__")
    for pi in un_pred:
        _bump("__spurious__", pred[pi].category)
    return matrix
