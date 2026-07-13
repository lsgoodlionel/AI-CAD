"""OCR token → 下游消费者的结构化馈入（纯函数，可离线测）。

把分类后的 token 提炼成三条下游管线要的形态：
  - ``elevation_candidates``：标高（米）+ 位置 → 楼层/标高识别（补 section-z）
  - ``axis_anchors``：轴号 + 位置 → 跨图拼接配准锚点
  - ``space_labels``：房间/图名 → 语义树

统一执行「置信门槛」纪律：低于阈值的 token 不进自动管线（读错比缺失更糟）。
"""
from __future__ import annotations

from .types import OcrResult

# 默认人工复核门槛：标高/轴号影响几何配准，取较高置信
_DEFAULT_MIN_CONF = 0.6
# 进几何配准管线（section-z 标定）的更严门槛：读错标高比缺失更糟
_GEOMETRY_MIN_CONF = 0.8


def _center(bbox: tuple[float, float, float, float]) -> tuple[float, float]:
    return ((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)


def elevation_candidates(
    result: OcrResult, *, min_confidence: float = _DEFAULT_MIN_CONF
) -> list[dict]:
    """标高候选：[{value_m, center, bbox, confidence, text}]，按 value 升序、value 去重。

    喂给 section_z / model_story：作为「自动打底」的标高来源，人工再校正。
    """
    seen: set[float] = set()
    out: list[dict] = []
    for t in result.of_kind("elevation"):
        if t.confidence < min_confidence or t.value is None:
            continue
        if t.value in seen:
            continue
        seen.add(t.value)
        out.append({
            "value_m": t.value,
            "center": _center(t.bbox),
            "bbox": list(t.bbox),
            "confidence": t.confidence,
            "text": t.text,
        })
    out.sort(key=lambda d: d["value_m"])
    return out


def as_geometry_texts(
    result: OcrResult, *, min_confidence: float = _GEOMETRY_MIN_CONF
) -> list[tuple[float, float, str]]:
    """标高 token → ``DrawingGeometry.texts`` 同构条目 ``[(x, y, text), ...]``。

    供 section-z 兜底：CAD PDF 正文标高是矢量字形，``page.get_text`` 取不到；
    把 OCR 标高 token 合成几何文本后喂现有 ``extract_section_levels``，
    完整复用其标高线绑定 / 线性标定 / 置信度逻辑。

    坐标口径与 fitz ``get_text("words")`` 一致：(x_min, y_min) 文本框左上角、
    页面点、左上原点——extractor 的 ±10pt 绑线容差按同一语义工作。
    """
    out: list[tuple[float, float, str]] = []
    for t in result.of_kind("elevation"):
        if t.confidence < min_confidence:
            continue
        out.append((t.bbox[0], t.bbox[1], t.text))
    return out


def axis_anchors(
    result: OcrResult, *, min_confidence: float = _DEFAULT_MIN_CONF
) -> list[dict]:
    """轴号锚点：[{label, center, bbox, confidence}]，供跨图配准。"""
    out: list[dict] = []
    for t in result.of_kind("axis"):
        if t.confidence < min_confidence:
            continue
        out.append({
            "label": t.text,
            "center": _center(t.bbox),
            "bbox": list(t.bbox),
            "confidence": t.confidence,
        })
    return out


def space_labels(
    result: OcrResult, *, min_confidence: float = _DEFAULT_MIN_CONF
) -> list[dict]:
    """房间名 / 图名：[{text, kind, center, confidence}]，喂语义树。"""
    out: list[dict] = []
    for t in result.tokens:
        if t.kind not in ("room_name", "title", "level_name"):
            continue
        if t.confidence < min_confidence:
            continue
        out.append({
            "text": t.text,
            "kind": t.kind,
            "center": _center(t.bbox),
            "confidence": t.confidence,
        })
    return out
