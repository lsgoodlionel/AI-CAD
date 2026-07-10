"""z 恢复置信度与降级框架（B-11，贯穿 B-02~B-10）。

统一各来源的置信度模型、estimated 标记与降级 note 文案，并提供按优先级择优的决策函数。
来源优先级（对齐 CROSS_VIEW_Z_RECOVERY_DESIGN §5）：
    section 剖面锚定 > registered 多视图配准 > measured 截面标注 > elevation 立面 > estimated > default 兜底。

诚实性硬约束：measured 类来源 estimated=False、note 空；其余显式 estimated=True + 标准 note，
绝不把默认常量伪装成实测。置信度阈值与 model_lod gate 语义协调（不另立双套标准）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# 来源优先级（越大越可信）
SOURCE_PRIORITY: dict[str, int] = {
    "section": 5,
    "registered": 4,
    "measured": 4,
    "elevation": 3,
    "estimated": 1,
    "default": 0,
}

# 视为「实测」的来源（不标 estimated）
MEASURED_SOURCES: frozenset[str] = frozenset({"section", "registered", "measured", "elevation"})

# 各来源基线置信度
_BASE_CONFIDENCE: dict[str, float] = {
    "section": 0.9,
    "registered": 0.92,
    "measured": 0.85,
    "elevation": 0.6,
    "estimated": 0.5,
    "default": 0.25,
}


@dataclass(frozen=True)
class Provenance:
    """单个几何量的溯源：从哪来、实测还是估算、置信几何、证据链、降级说明。"""
    source: str
    confidence: float
    estimated: bool
    evidence_ref: dict = field(default_factory=dict)
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "confidence": self.confidence,
            "estimated": self.estimated,
            "evidence_ref": dict(self.evidence_ref),
            "note": self.note,
        }


def build_provenance(
    source: str,
    *,
    confidence: float | None = None,
    evidence_ref: dict | None = None,
    quantity_label: str = "",
    default_value: float | None = None,
    unit: str = "m",
) -> Provenance:
    """构造 Provenance：未知来源归 default；非实测来源自动生成降级 note。"""
    normalized = source if source in SOURCE_PRIORITY else "default"
    estimated = normalized not in MEASURED_SOURCES
    resolved_conf = confidence if confidence is not None else _BASE_CONFIDENCE[normalized]
    note = downgrade_note(quantity_label, default_value, unit) if estimated else ""
    return Provenance(
        source=normalized,
        confidence=round(float(resolved_conf), 4),
        estimated=estimated,
        evidence_ref=dict(evidence_ref or {}),
        note=note,
    )


def downgrade_note(quantity_label: str, default_value: float | None, unit: str) -> str:
    """统一降级文案：缺实测证据 → 回落默认（估算）。"""
    label = quantity_label or "该几何量"
    if default_value is not None:
        return f"{label}缺实测证据，回落默认 {default_value}{unit}（估算）"
    return f"{label}缺实测证据（估算）"


def choose_by_priority(candidates: list[Provenance]) -> Provenance | None:
    """择优：来源优先级优先，置信度次之。空 → None。"""
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda prov: (SOURCE_PRIORITY.get(prov.source, 0), prov.confidence),
    )


def is_measured(source: str) -> bool:
    return source in MEASURED_SOURCES
