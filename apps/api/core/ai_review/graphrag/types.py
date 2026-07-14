"""GraphRAG 融合召回层 — 共享数据结构（D-18）。

统一「条文图谱结构召回(KG)」与「向量语义召回(RAG)」两路输出为可合并/去重/仲裁
的中间态，供 LLM 多步核查消费。设计克制（YAGNI）：字段仅覆盖 D-18 当前需要的
合并/去重/灰度/可观测性，不预留未验证的扩展位。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from core.ai_review.base import AIIssue, IssueSeverity

# 召回来源：kg=图谱结构召回 / rag=向量语义召回
RetrievalSource = Literal["kg", "rag"]

# 融合运行模式：
#   identity        — 灰度关闭，恒等回到「KG.analyze + RAG.analyze 并行拼接」（现状行为）
#   fusion          — 灰度开启，双路召回 → 合并去重 → LLM 多步核查全部成功
#   fusion_degraded — 灰度开启，但 LLM 核查不可用（如本地 ollama 未起/未配置模型路由），
#                     优雅降级为「合并去重候选直出」，不做核查，issue 描述显式标注降级
FusionMode = Literal["identity", "fusion", "fusion_degraded"]

# 义务等级（对齐规范知识库设计：MUST/SHOULD/MAY/MUST_NOT）
ObligationLevel = Literal["MUST", "SHOULD", "MAY", "MUST_NOT"]


@dataclass(frozen=True)
class FusionConfig:
    """灰度开关 + 融合仲裁参数（不可变，默认值 = 安全关闭状态）。"""

    # 灰度开关：False（默认）时 run_graphrag_fusion 恒等回到现并行行为，
    # 不引入任何新的合并/去重/LLM 调用 —— 与旧行为字节级一致，可安全默认关闭上线。
    enabled: bool = False

    # 去重相似度阈值（difflib SequenceMatcher.ratio，>= 视为同一候选），
    # regulation_ref 精确相等命中优先，未命中再退到文本相似度。
    dedup_similarity_threshold: float = 0.72

    # 合并候选数上限（防止病态输入喂给 LLM 过长 prompt）
    max_merged_candidates: int = 20

    # LLM 多步核查所走的 ModelRouter 引擎名（需在 engine_model_configs 种子，
    # 参见 docs/PHASE_D_GRAPHRAG.md「ollama 临时测试」一节）
    llm_engine_name: str = "graphrag_verifier"


@dataclass(frozen=True)
class RetrievalCandidate:
    """单路召回的原始候选（融合前，尚未去重）。"""

    source: RetrievalSource
    regulation_ref: str = ""
    snippet: str = ""
    severity_hint: IssueSeverity = IssueSeverity.INFO
    discipline: str = ""

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "regulation_ref": self.regulation_ref,
            "snippet": self.snippet,
            "severity_hint": self.severity_hint.value,
            "discipline": self.discipline,
        }


@dataclass(frozen=True)
class FusionCandidate:
    """合并去重后的候选（sources 长度 2 = 双路共识，可信度更高）。"""

    regulation_ref: str = ""
    snippet: str = ""
    sources: tuple[RetrievalSource, ...] = ()
    severity_hint: IssueSeverity = IssueSeverity.INFO
    discipline: str = ""

    @property
    def is_consensus(self) -> bool:
        return len(set(self.sources)) >= 2

    def to_dict(self) -> dict:
        return {
            "regulation_ref": self.regulation_ref,
            "snippet": self.snippet,
            "sources": list(self.sources),
            "is_consensus": self.is_consensus,
            "severity_hint": self.severity_hint.value,
            "discipline": self.discipline,
        }


@dataclass(frozen=True)
class GraphRAGFusionResult:
    """一次 run_graphrag_fusion 调用的完整可观测结果。"""

    issues: tuple[AIIssue, ...] = ()
    mode: FusionMode = "identity"
    kg_count: int = 0
    rag_count: int = 0
    merged_count: int = 0
    llm_verified_count: int = 0
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "kg_count": self.kg_count,
            "rag_count": self.rag_count,
            "merged_count": self.merged_count,
            "llm_verified_count": self.llm_verified_count,
            "issue_count": len(self.issues),
            "warnings": list(self.warnings),
        }
