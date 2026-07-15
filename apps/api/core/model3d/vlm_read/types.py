"""VLM 读图（远程 qwen3.5-vision）共享契约。

铁律：VLM 只产出语义候选 + 置信度（判专业 / 读标高 / 识构件），绝不输出
计数 / 坐标 / 尺寸 / QTO——那些属于确定性几何管线（topology_rules /
model_qto 等）。候选需人工或规则复核后才可采信，任何解析歧义或低置信
一律降级为空/低分，绝不替 VLM 编造结果。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DisciplineCandidate:
    """专业判定候选（建筑/结构/给排水/暖通/电气/道路/景观……）。"""

    value: str
    confidence: float
    evidence: str = ""  # 触发该判定的原文片段，供人工复核溯源

    def to_dict(self) -> dict:
        return {
            "value": self.value,
            "confidence": round(self.confidence, 4),
            "evidence": self.evidence,
        }


@dataclass(frozen=True)
class ElevationCandidate:
    """标高候选（米）。仅供参考的语义线索——权威标高以人工录入/规则
    （section_z_recovery 等确定性几何管线）为准，VLM 结果不得覆盖。
    """

    value_m: float
    confidence: float
    evidence: str = ""

    def to_dict(self) -> dict:
        return {
            "value_m": self.value_m,
            "confidence": round(self.confidence, 4),
            "evidence": self.evidence,
        }


@dataclass(frozen=True)
class ComponentCandidate:
    """构件类别候选（梁/板/柱/基础等）。不含计数、坐标、尺寸。"""

    label: str
    confidence: float
    evidence: str = ""

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "confidence": round(self.confidence, 4),
            "evidence": self.evidence,
        }


@dataclass(frozen=True)
class VlmReadResult:
    """一次远程 VLM 读图调用的结构化产物。"""

    discipline: DisciplineCandidate | None = None
    elevations: tuple[ElevationCandidate, ...] = ()
    components: tuple[ComponentCandidate, ...] = ()
    raw_text: str = ""
    backend: str = "none"  # "qwen3.5-vision" | "none"
    model: str = ""
    warnings: tuple[str, ...] = ()

    @property
    def available(self) -> bool:
        """VLM 是否真正调用成功（backend 非 none）。"""
        return self.backend not in ("", "none")

    def filter_confidence(self, min_conf: float) -> "VlmReadResult":
        """按置信度过滤（人工复核纪律：低置信候选不进自动管线）。"""
        discipline = (
            self.discipline
            if (self.discipline is not None and self.discipline.confidence >= min_conf)
            else None
        )
        elevations = tuple(e for e in self.elevations if e.confidence >= min_conf)
        components = tuple(c for c in self.components if c.confidence >= min_conf)
        return VlmReadResult(
            discipline=discipline,
            elevations=elevations,
            components=components,
            raw_text=self.raw_text,
            backend=self.backend,
            model=self.model,
            warnings=self.warnings,
        )

    def to_dict(self) -> dict:
        return {
            "backend": self.backend,
            "available": self.available,
            "model": self.model,
            "discipline": self.discipline.to_dict() if self.discipline else None,
            "elevations": [e.to_dict() for e in self.elevations],
            "components": [c.to_dict() for c in self.components],
            "warnings": list(self.warnings),
        }
