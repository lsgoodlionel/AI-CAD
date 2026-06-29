"""四引擎 AI 审图 — 公共数据结构与基类"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum


class IssueSeverity(str, Enum):
    CRITICAL = "critical"   # 强条违规，必须处理
    MAJOR    = "major"      # 重要问题，建议处理
    MINOR    = "minor"      # 一般问题
    INFO     = "info"       # 提示信息


@dataclass
class DrawingContext:
    """贯穿四引擎的图纸上下文，视觉引擎运行后填充 extracted_text。"""
    drawing_id:      str
    drawing_no:      str
    discipline:      str          # 'structure' | 'architecture' | 'mep' | 'decoration' | 'general'
    title:           str
    version:         str
    file_key:        str          # MinIO object key
    file_ext:        str          # 'pdf' | 'dwg' | 'dxf' | 'ifc'
    project_id:      str
    estimated_impact: float | None = None

    # 视觉引擎填充（其他引擎依赖）
    extracted_text:  str  = ""
    ocr_metadata:    dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "drawing_id": self.drawing_id,
            "drawing_no": self.drawing_no,
            "discipline": self.discipline,
            "title": self.title,
            "version": self.version,
            "file_key": self.file_key,
            "file_ext": self.file_ext,
            "project_id": self.project_id,
            "estimated_impact": self.estimated_impact or 0,
            "extracted_text": self.extracted_text,
        }


@dataclass
class AIIssue:
    """单条审查问题，对应 ai_review_issues 表一行。"""
    engine:         str            # 'rules' | 'kg' | 'rag' | 'ocr' | 'review'
    severity:       IssueSeverity
    description:    str
    category:       str = ""
    regulation_ref: str = ""       # 规范条文，如 "GB50010-2010 第8.3.1条"
    suggestion:     str = ""
    location_x:     float | None = None
    location_y:     float | None = None

    # ── 会审审查引擎（review）扩展字段（契约2，全部可选、向后兼容）──
    discipline_code:    str  = ""  # ZH/JG/WH/JZ/GPS/DQ/NT...
    discipline_name:    str  = ""  # 综合协调/结构/围护...
    location:           dict = field(default_factory=dict)
    # location = {drawings:[], levels:[], axes:[], nodes_or_systems:[], spaces:[]}
    concerns:           list = field(default_factory=list)   # [{label, reason}]
    issue_class:        list = field(default_factory=list)
    # issue_class ∈ 表达遗漏|图纸冲突|接口冲突|施工条件问题|验收风险
    interface_primary:  str  = ""
    interface_related:  list = field(default_factory=list)
    risk_level:         str  = ""  # 高|中|低
    object_level:       str  = ""  # 部位级|系统级|节点级
    standard_question:  str  = ""  # 可直接入会审问题单的闭环句子（= 问题包.主问题 + 补充问题）
    evidence_gap:       list = field(default_factory=list)

    # ── V2 扩展：对象识别 + 场景 + 问题包 + 文书化输出（契约2 V2，可选、向后兼容）──
    object_name:        str  = ""  # 识别到的对象名（如 梁、柱、板、墙、核心筒）
    object_basis:       str  = ""  # 显式命名 | 推定（依据…）
    scenario:           str  = ""  # 正常审图|图间冲突|施工落地|验收风险
    scenario_reason:    str  = ""  # 场景优先级触发原因
    question_pack:      dict = field(default_factory=dict)  # {主问题, 补充问题, 证据缺口}
    doc_minutes:        list = field(default_factory=list)  # 会审纪要口径条目
    doc_reply:          list = field(default_factory=list)  # 设计答复口径条目


class BaseEngine(ABC):
    """所有引擎的抽象基类。"""
    engine_name: str = "base"

    @abstractmethod
    async def analyze(self, ctx: DrawingContext, db) -> list[AIIssue]:
        """执行分析，返回问题列表。出错时返回空列表而非抛出异常。"""
