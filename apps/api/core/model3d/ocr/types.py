"""OCR 共享契约（渲染/识别/分类/消费各环节的公共缝）。

坐标沿用 fitz 页面点（point，左上原点，与 ``page.rect`` 一致）；下游需要
归一化或转 y-up 时自行按 ``page_size`` 换算。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable

# 轻量语义分类（确定性规则，非学习）——决定 token 喂给哪条下游管线
TokenKind = Literal[
    "elevation",   # 标高，如 ±0.000 / +3.600 / -1.500
    "axis",        # 轴号候选，如 1 / 12 / A / 1/A
    "dimension",   # 纯尺寸数字（mm），如 3600 / 150
    "level_name",  # 楼层名，如 一层/首层/屋面/地下一层/标高层
    "room_name",   # 房间/功能空间名（含 CJK）
    "note",        # 说明性中文文本
    "title",       # 标题/图名候选（长中文串）
    "other",       # 未归类
]


@dataclass(frozen=True)
class TextToken:
    """一个 OCR 文本 token（一行/一框）。"""
    text: str
    bbox: tuple[float, float, float, float]  # (x_min, y_min, x_max, y_max) 页面点，左上原点
    confidence: float                        # 0~1
    kind: TokenKind = "other"
    value: float | None = None               # elevation/dimension 解析出的数值（米或 mm），否则 None

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "bbox": list(self.bbox),
            "confidence": round(self.confidence, 4),
            "kind": self.kind,
            "value": self.value,
        }


@dataclass(frozen=True)
class OcrResult:
    """一张图纸（首页）的 OCR 输出。"""
    tokens: tuple[TextToken, ...] = ()
    backend: str = ""                        # paddleocr / mock / none
    dpi: int = 0
    page_size: tuple[float, float] = (0.0, 0.0)  # 页面宽高（点）
    warnings: tuple[str, ...] = ()

    @property
    def available(self) -> bool:
        """OCR 是否真正执行（backend 非 none）。"""
        return self.backend not in ("", "none")

    def of_kind(self, kind: TokenKind) -> tuple[TextToken, ...]:
        return tuple(t for t in self.tokens if t.kind == kind)

    @property
    def kind_counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for t in self.tokens:
            out[t.kind] = out.get(t.kind, 0) + 1
        return out

    def filter_confidence(self, min_conf: float) -> "OcrResult":
        """按置信度过滤（人工复核纪律：低置信不进自动管线）。"""
        kept = tuple(t for t in self.tokens if t.confidence >= min_conf)
        return OcrResult(
            tokens=kept, backend=self.backend, dpi=self.dpi,
            page_size=self.page_size, warnings=self.warnings,
        )

    def to_dict(self) -> dict:
        return {
            "backend": self.backend,
            "available": self.available,
            "dpi": self.dpi,
            "page_size": list(self.page_size),
            "kind_counts": self.kind_counts,
            "warnings": list(self.warnings),
            "tokens": [t.to_dict() for t in self.tokens],
        }


@runtime_checkable
class OcrBackend(Protocol):
    """OCR 后端协议：把渲染好的位图识别成原始文本框。

    返回 (text, bbox_pixels, confidence)；bbox 为像素坐标，由 service 统一按 dpi
    换算为页面点。分类（TokenKind）不在后端做，由 service 调 classify 统一处理。
    """

    name: str

    def is_available(self) -> bool:
        ...

    def recognize(self, image_rgb, warnings: list[str]) -> list[tuple[str, tuple[float, float, float, float], float]]:
        ...
