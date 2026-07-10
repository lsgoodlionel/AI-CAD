"""融合仲裁策略（C-13 纯函数层）。

把「规则候选 vs 模型候选」的裁决拆成一组**无副作用、确定性**的纯函数：

    - ``bbox_iou``       —— bbox 交并比，判定两候选是否落在「同一处」。
    - ``pair_candidates``—— 贪心 IoU 配对（每规则 ≤1 模型、每模型 ≤1 规则）。
    - ``is_rule_strong`` —— 规则强命中判定（确定性优先原则的硬边界）。
    - ``arbitrate``      —— 单对（规则,模型）裁决 → 融合候选 + 决策记录。

方法论（融合的灵魂）：学习模型**补规则的模糊边界，不替代规则**——
    · 同处同类 → 共识增强置信（source=fused）；
    · 同处异类 + 规则强命中 → 规则**不被覆盖**（source=rule，仅记录被拒模型）；
    · 同处异类 + 规则弱 → 按「置信度 + 规则优先级」仲裁，模型可翻案（source=fused）。

设计约束：frozen dataclass、完整类型注解、不可变（dataclasses.replace）、
确定性、纯函数、绝不抛异常。本模块只依赖共享契约 ``SymbolCandidate``，
可脱离融合引擎独立测试。
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace

from core.model3d.spotting.types import SymbolCandidate

# 决策类型（供人审与评测归因）
Decision = str  # consensus / rule_protected / model_override / rule_wins


@dataclass(frozen=True)
class FusionPolicy:
    """融合仲裁配置（不可变）。默认值即内置降级策略 DEFAULT_POLICY。"""

    iou_threshold: float = 0.30
    rule_strong_confidence: float = 0.85
    model_min_confidence: float = 0.45
    agreement_boost: float = 0.10
    priority_gain: float = 0.20
    default_priority: int = 5
    rule_priority: dict[str, int] = field(default_factory=dict)  # read-only 视之

    def priority_of(self, category: str) -> int:
        """类别规则优先级；缺失回退 default_priority。"""
        return self.rule_priority.get(category, self.default_priority)

    def _max_priority(self) -> int:
        return max([*self.rule_priority.values(), self.default_priority], default=1) or 1


DEFAULT_POLICY = FusionPolicy(
    rule_priority={
        "column": 10, "beam": 9, "slab": 8, "wall": 8,
        "door": 6, "window": 6, "pipe": 5, "equipment": 5, "axis": 4,
    }
)


@dataclass(frozen=True)
class ArbitrationOutcome:
    """单对裁决结果：融合后候选 + 决策归因（供 FusionResult 记录）。"""

    candidate: SymbolCandidate
    decision: Decision
    iou: float
    note: str


# ── 几何 ─────────────────────────────────────────────────
def bbox_iou(
    a: tuple[float, float, float, float],
    b: tuple[float, float, float, float],
) -> float:
    """两 bbox 的交并比 IoU ∈ [0,1]。坐标顺序容错（自动归一 min/max）。"""
    ax0, ax1 = min(a[0], a[2]), max(a[0], a[2])
    ay0, ay1 = min(a[1], a[3]), max(a[1], a[3])
    bx0, bx1 = min(b[0], b[2]), max(b[0], b[2])
    by0, by1 = min(b[1], b[3]), max(b[1], b[3])

    inter_w = max(0.0, min(ax1, bx1) - max(ax0, bx0))
    inter_h = max(0.0, min(ay1, by1) - max(ay0, by0))
    inter = inter_w * inter_h
    if inter <= 0.0:
        return 0.0
    union = (ax1 - ax0) * (ay1 - ay0) + (bx1 - bx0) * (by1 - by0) - inter
    return inter / union if union > 0.0 else 0.0


# ── 配对 ─────────────────────────────────────────────────
def pair_candidates(
    rule_candidates: tuple[SymbolCandidate, ...],
    model_candidates: tuple[SymbolCandidate, ...],
    iou_threshold: float,
) -> tuple[
    tuple[tuple[SymbolCandidate, SymbolCandidate, float], ...],
    tuple[SymbolCandidate, ...],
    tuple[SymbolCandidate, ...],
]:
    """贪心 IoU 配对，返回 (配对对, 未配对规则, 未配对模型)。

    每条规则按输入顺序取「未占用且 IoU ≥ 阈值」中 IoU 最大的模型（并列取先者），
    保证每规则 ≤1 模型、每模型 ≤1 规则，结果确定。规则永不被丢弃（召回保底）。
    """
    used_models: set[int] = set()
    pairs: list[tuple[SymbolCandidate, SymbolCandidate, float]] = []
    unmatched_rules: list[SymbolCandidate] = []

    for rule in rule_candidates:
        best_j: int | None = None
        best_iou = iou_threshold
        for j, model in enumerate(model_candidates):
            if j in used_models:
                continue
            iou = bbox_iou(rule.bbox, model.bbox)
            if iou >= best_iou and (best_j is None or iou > best_iou):
                best_j, best_iou = j, iou
        if best_j is None:
            unmatched_rules.append(rule)
        else:
            used_models.add(best_j)
            pairs.append((rule, model_candidates[best_j], best_iou))

    unmatched_models = tuple(
        m for j, m in enumerate(model_candidates) if j not in used_models
    )
    return tuple(pairs), tuple(unmatched_rules), unmatched_models


# ── 强命中判定 ───────────────────────────────────────────
def is_rule_strong(rule: SymbolCandidate, policy: FusionPolicy) -> bool:
    """规则候选是否为强命中（置信 ≥ 阈值）→ 确定性优先，不被模型覆盖。"""
    return rule.confidence >= policy.rule_strong_confidence


# ── 单对仲裁 ─────────────────────────────────────────────
def arbitrate(
    rule: SymbolCandidate,
    model: SymbolCandidate,
    iou: float,
    policy: FusionPolicy,
) -> ArbitrationOutcome:
    """裁决一对（规则,模型）同处候选，返回融合候选 + 决策归因。

    确定性优先原则：规则强命中时**永不被模型翻案**；弱规则冲突才按
    「置信度 + 规则优先级」仲裁。任何分支都保留该处一个输出（召回保底）。
    """
    if rule.category == model.category:
        return _consensus(rule, model, iou, policy)
    if is_rule_strong(rule, policy):
        return _rule_protected(rule, model, iou)
    return _weak_conflict(rule, model, iou, policy)


def _consensus(
    rule: SymbolCandidate, model: SymbolCandidate, iou: float, policy: FusionPolicy
) -> ArbitrationOutcome:
    """同处同类：共识 → 增强置信，source=fused。"""
    conf = min(1.0, max(rule.confidence, model.confidence) + policy.agreement_boost)
    cand = _merge(
        rule, model, category=rule.category, confidence=conf, arbitration="consensus"
    )
    return ArbitrationOutcome(cand, "consensus", iou, "rule_model_agree")


def _rule_protected(
    rule: SymbolCandidate, model: SymbolCandidate, iou: float
) -> ArbitrationOutcome:
    """同处异类 + 规则强命中：规则不被覆盖（source 保持 rule）。"""
    cand = replace(
        rule,
        evidence={
            **rule.evidence,
            "arbitration": "rule_protected",
            "rejected_model_category": model.category,
            "rejected_model_confidence": model.confidence,
        },
    )
    return ArbitrationOutcome(cand, "rule_protected", iou, "strong_rule_not_overridden")


def _weak_conflict(
    rule: SymbolCandidate, model: SymbolCandidate, iou: float, policy: FusionPolicy
) -> ArbitrationOutcome:
    """同处异类 + 规则弱：按「置信 + 规则优先级」仲裁，模型可翻案。"""
    rule_score = rule.confidence + policy.priority_gain * (
        policy.priority_of(rule.category) / policy._max_priority()
    )
    model_wins = (
        model.confidence >= policy.model_min_confidence
        and model.confidence > rule_score
    )
    if model_wins:
        cand = _merge(
            rule, model,
            category=model.category,
            confidence=model.confidence,
            arbitration="model_override",
        )
        return ArbitrationOutcome(cand, "model_override", iou, "weak_rule_overridden")

    cand = replace(
        rule,
        evidence={
            **rule.evidence,
            "arbitration": "rule_wins",
            "rejected_model_category": model.category,
            "rejected_model_confidence": model.confidence,
        },
    )
    return ArbitrationOutcome(cand, "rule_wins", iou, "rule_kept_over_model")


def _merge(
    rule: SymbolCandidate,
    model: SymbolCandidate,
    *,
    category: str,
    confidence: float,
    arbitration: str,
) -> SymbolCandidate:
    """合成融合候选：source=fused，规则 bbox 为锚，图元 id 并集，证据合并。"""
    return replace(
        rule,
        category=category,
        confidence=confidence,
        source="fused",
        mep_system=rule.mep_system or model.mep_system,
        primitive_ids=_merge_ids(rule.primitive_ids, model.primitive_ids),
        evidence={
            **rule.evidence,
            "arbitration": arbitration,
            "rule_category": rule.category,
            "rule_confidence": rule.confidence,
            "model_category": model.category,
            "model_confidence": model.confidence,
        },
    )


def _merge_ids(a: tuple[int, ...], b: tuple[int, ...]) -> tuple[int, ...]:
    """图元 id 并集，保持确定顺序（a 优先，b 去重追加）。"""
    seen = set(a)
    return a + tuple(i for i in b if not (i in seen or seen.add(i)))
