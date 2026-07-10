"""融合策略引擎（C-13，Phase C 方法论核心）。

对**两组 SymbolCandidate**（规则候选 source="rule" 与模型候选 source="model"）
做确定性融合，产出带 ``source`` + ``confidence`` 的融合候选与仲裁记录，供人审。

灵魂：学习模型**补规则的模糊边界，不替代规则**——
    1. 规则候选**全保留**（召回保底：融合召回 ≥ 纯规则）。
    2. 与规则同处的模型候选进入仲裁：同类增强、异类且规则强命中则**规则不被覆盖**。
    3. 落在无规则区域的模型候选按门槛**补召回**（source="model"）。

结构性保证「融合召回 ≥ 纯规则、精度 ≥ 纯规则」：
    规则候选从不被删除；模型只在弱规则处翻案或在空白处补召回，
    强规则命中恒不被翻案（确定性优先原则不破坏）。

设计约束：frozen dataclass、完整类型注解、不可变、确定性；配置加载优雅降级
（pyyaml/文件缺失、单条无效项均降级，绝不抛异常），风格仿 layer_conventions.py。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path

from core.model3d.spotting.types import SymbolCandidate

from .arbitration import (
    DEFAULT_POLICY,
    ArbitrationOutcome,
    FusionPolicy,
    arbitrate,
    pair_candidates,
)

try:
    import yaml

    _HAS_YAML = True
except ImportError:  # pragma: no cover - 环境缺依赖时降级
    _HAS_YAML = False

logger = logging.getLogger(__name__)

# __file__ = apps/api/core/model3d/fusion/fusion_engine.py → parents[3] = apps/api
_POLICY_FILE = Path(__file__).parents[3] / "data" / "model3d" / "fusion_policy.yaml"


@dataclass(frozen=True)
class ArbitrationRecord:
    """单个融合决策的可审计记录（source/confidence/归因全透明）。"""

    category: str
    source: str            # rule / model / fused
    confidence: float
    decision: str          # rule_only / model_recall / consensus / rule_protected / ...
    iou: float = 0.0
    rule_category: str | None = None
    model_category: str | None = None
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "category": self.category,
            "source": self.source,
            "confidence": self.confidence,
            "decision": self.decision,
            "iou": self.iou,
            "rule_category": self.rule_category,
            "model_category": self.model_category,
            "note": self.note,
        }


@dataclass(frozen=True)
class FusionResult:
    """融合输出：候选（每个带 source+confidence）+ 逐项仲裁记录。"""

    candidates: tuple[SymbolCandidate, ...] = ()
    records: tuple[ArbitrationRecord, ...] = ()

    @property
    def counts_by_source(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for c in self.candidates:
            out[c.source] = out.get(c.source, 0) + 1
        return out

    def to_dict(self) -> dict:
        return {
            "counts_by_source": self.counts_by_source,
            "candidates": [c.to_dict() for c in self.candidates],
            "records": [r.to_dict() for r in self.records],
        }


# ── 配置加载（降级安全）─────────────────────────────────
def _coerce_priority(raw: object, default: dict[str, int]) -> dict[str, int]:
    """rule_priority 解析：仅收 {str: int/float→int} 项，无效项跳过。"""
    if not isinstance(raw, dict):
        return dict(default)
    out: dict[str, int] = {}
    for key, val in raw.items():
        if isinstance(key, str) and isinstance(val, (int, float)) and not isinstance(val, bool):
            out[key] = int(val)
    return out or dict(default)


def _coerce_float(raw: object, fallback: float) -> float:
    return float(raw) if isinstance(raw, (int, float)) and not isinstance(raw, bool) else fallback


@lru_cache(maxsize=1)
def load_fusion_policy() -> FusionPolicy:
    """加载融合策略（缓存）。任何失败 → 内置 DEFAULT_POLICY，绝不抛异常。"""
    if not _HAS_YAML:
        logger.warning("[fusion] pyyaml 未安装，融合策略降级为默认")
        return DEFAULT_POLICY
    if not _POLICY_FILE.exists():
        logger.warning("[fusion] 配置缺失（降级默认）: %s", _POLICY_FILE)
        return DEFAULT_POLICY
    try:
        data = yaml.safe_load(_POLICY_FILE.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - 任何解析异常都降级
        logger.error("[fusion] 解析失败（降级默认）: %s", exc)
        return DEFAULT_POLICY
    if not isinstance(data, dict):
        logger.warning("[fusion] 配置根节点非映射，降级默认")
        return DEFAULT_POLICY

    return FusionPolicy(
        iou_threshold=_coerce_float(data.get("iou_threshold"), DEFAULT_POLICY.iou_threshold),
        rule_strong_confidence=_coerce_float(
            data.get("rule_strong_confidence"), DEFAULT_POLICY.rule_strong_confidence
        ),
        model_min_confidence=_coerce_float(
            data.get("model_min_confidence"), DEFAULT_POLICY.model_min_confidence
        ),
        agreement_boost=_coerce_float(data.get("agreement_boost"), DEFAULT_POLICY.agreement_boost),
        priority_gain=_coerce_float(data.get("priority_gain"), DEFAULT_POLICY.priority_gain),
        default_priority=int(_coerce_float(data.get("default_priority"), DEFAULT_POLICY.default_priority)),
        rule_priority=_coerce_priority(data.get("rule_priority"), DEFAULT_POLICY.rule_priority),
    )


# ── 融合主流程 ───────────────────────────────────────────
def fuse(
    rule_candidates: tuple[SymbolCandidate, ...] | list[SymbolCandidate],
    model_candidates: tuple[SymbolCandidate, ...] | list[SymbolCandidate],
    *,
    policy: FusionPolicy | None = None,
) -> FusionResult:
    """融合规则候选与模型候选 → 带 source/confidence 的融合结果 + 仲裁记录。

    流程（确定性）：
        1. bbox IoU 贪心配对，识别「同处」的规则/模型对。
        2. 每对交由 ``arbitrate`` 裁决（共识增强 / 强规则保护 / 弱规则仲裁）。
        3. 未配对规则**原样保留**（source 保持 "rule"，召回保底）。
        4. 未配对模型：置信 ≥ 门槛则**补召回**（source="model"），否则记录拒收。

    参数 policy 缺省时按配置加载（降级安全）。输入可为 list/tuple，纯操作不改入参。
    """
    active = policy or load_fusion_policy()
    rules = tuple(rule_candidates)
    models = tuple(model_candidates)

    pairs, unmatched_rules, unmatched_models = pair_candidates(
        rules, models, active.iou_threshold
    )

    fused: list[SymbolCandidate] = []
    records: list[ArbitrationRecord] = []

    for rule, model, iou in pairs:
        outcome = arbitrate(rule, model, iou, active)
        fused.append(outcome.candidate)
        records.append(_record_from_outcome(outcome, rule, model))

    for rule in unmatched_rules:
        fused.append(rule)  # 规则原样保留 —— 召回保底
        records.append(
            ArbitrationRecord(
                category=rule.category,
                source="rule",
                confidence=rule.confidence,
                decision="rule_only",
                rule_category=rule.category,
                note="rule_no_model_overlap",
            )
        )

    for model in unmatched_models:
        if model.confidence >= active.model_min_confidence:
            cand = replace(model, source="model")  # 补召回：显式标 source
            fused.append(cand)
            records.append(
                ArbitrationRecord(
                    category=model.category,
                    source="model",
                    confidence=model.confidence,
                    decision="model_recall",
                    model_category=model.category,
                    note="model_fills_rule_gap",
                )
            )
        else:
            records.append(
                ArbitrationRecord(
                    category=model.category,
                    source="model",
                    confidence=model.confidence,
                    decision="model_rejected",
                    model_category=model.category,
                    note="below_model_min_confidence",
                )
            )

    return FusionResult(candidates=tuple(fused), records=tuple(records))


def _record_from_outcome(
    outcome: ArbitrationOutcome, rule: SymbolCandidate, model: SymbolCandidate
) -> ArbitrationRecord:
    cand = outcome.candidate
    return ArbitrationRecord(
        category=cand.category,
        source=cand.source,
        confidence=cand.confidence,
        decision=outcome.decision,
        iou=outcome.iou,
        rule_category=rule.category,
        model_category=model.category,
        note=outcome.note,
    )
