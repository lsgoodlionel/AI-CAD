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
    engine:         str            # 'rules' | 'kg' | 'rag' | 'ocr'
    severity:       IssueSeverity
    description:    str
    category:       str = ""
    regulation_ref: str = ""       # 规范条文，如 "GB50010-2010 第8.3.1条"
    suggestion:     str = ""
    location_x:     float | None = None
    location_y:     float | None = None


class BaseEngine(ABC):
    """所有引擎的抽象基类。"""
    engine_name: str = "base"

    @abstractmethod
    async def analyze(self, ctx: DrawingContext, db) -> list[AIIssue]:
        """执行分析，返回问题列表。出错时返回空列表而非抛出异常。"""
