"""图种判别器（B-01）：判定单图为平面 / 剖面 / 立面 / 详图。

分级判别（廉价确定性优先，昂贵兜底后置，对齐 CROSS_VIEW_Z_RECOVERY_DESIGN §2.1）：
1. 图名关键词（零成本）——`drawing_filename_parser.match_view_type_keyword`，
   源优先级 title > drawing_no > filename > folder_path（沿用 drawing_semantics 的图纸口径）。
2. 几何特征佐证（可选）——传入 `DrawingGeometry` 时，双向长轴线网格判平面、
   密集水平标高线判剖/立面；与关键词一致则升置信，冲突则降置信并标 `needs_vlm`。
3. VLM 兜底（不在本模块实现）——当关键词与几何都判不出 / 冲突时，`evidence.needs_vlm=True`，
   由上游经 `ModelRouter` 的 `drawing_visual_analyzer` 引擎触发（VLM 只判类别，不读尺寸）。

产出 `ViewTypeResult{view_type, confidence, evidence, uncertain}`；证据可追溯来源。
低置信度显式标 `uncertain`，绝不把猜测伪装成确定判别。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from core.model3d.element_recognizer import extract_elevations
from services.drawing_filename_parser import (
    VIEW_TYPE_PLAN,
    VIEW_TYPE_UNKNOWN,
    match_view_type_keyword,
)

# 关键词来源优先级（与 drawing_semantics._iter_sources 同口径）
_SOURCE_KEYS: tuple[str, ...] = ("title", "drawing_no", "filename", "folder_path")

# 几何签名：平面 = 双向网格；section_or_elevation = 单向 + 密集标高线（剖/立面无法二分）
_GEOM_SIGNATURE_SECTION_OR_ELEVATION = "section_or_elevation"

# 置信度常量
_TITLE_CONF = 0.9              # 关键词命中于 title（最权威）
_OTHER_SOURCE_CONF = 0.8       # 关键词命中于其他源
_AGREE_BOOST = 0.07            # 几何与关键词一致的加成
_MAX_CONF = 0.97
_CONFLICT_CONF = 0.55          # 几何与关键词冲突
_GEOM_ONLY_PLAN_CONF = 0.6     # 仅几何判平面（无关键词）
_GEOM_ONLY_AMBIGUOUS_CONF = 0.4  # 几何像剖/立面但无法二分
_UNKNOWN_CONF = 0.2
_UNCERTAIN_THRESHOLD = 0.6     # confidence < 阈值 或 view_type=unknown → uncertain

# 几何签名判据（对齐 element_recognizer 的容差/占比语义，独立常量避免耦合私有量）
_LINE_STRAIGHT_TOL_PT = 2.0
_AXIS_MIN_RATIO = 0.5
_MAX_LINES_SCANNED = 4000
_MIN_GRID_LINES = 2            # 单方向 ≥2 条长线才算成网格
_MIN_ELEVATION_TEXTS = 3       # 标高文本密度阈值（剖/立面信号）


@dataclass(frozen=True)
class ViewTypeResult:
    """图种判别结果。

    view_type ∈ {plan, section, elevation, detail, unknown}
    uncertain：低置信或需 VLM 复核时为 True（供料层显式标注不确定性）。
    """
    view_type: str
    confidence: float
    evidence: dict = field(default_factory=dict)
    uncertain: bool = False


def classify_view_type(drawing: Mapping[str, Any]) -> ViewTypeResult:
    """判别单图图种。drawing 可含 title/drawing_no/filename/folder_path 与可选 geometry。"""
    hit, source = _first_keyword_hit(drawing)
    geom_signature = _geometry_signature(drawing.get("geometry"))

    if hit is not None:
        return _resolve_with_keyword(hit, source, geom_signature)
    return _resolve_without_keyword(geom_signature)


def _first_keyword_hit(drawing: Mapping[str, Any]):
    """按源优先级返回首个命中关键词的 (hit, source_key)；无命中返回 (None, None)。"""
    for key in _SOURCE_KEYS:
        value = drawing.get(key)
        if not isinstance(value, str):
            continue
        hit = match_view_type_keyword(value)
        if hit is not None:
            return hit, key
    return None, None


def _resolve_with_keyword(hit, source: str, geom_signature: str | None) -> ViewTypeResult:
    base_conf = _TITLE_CONF if source == "title" else _OTHER_SOURCE_CONF
    evidence = _make_evidence(
        keyword_source=source, keyword=hit.keyword, geometry_signature=geom_signature
    )

    confidence = base_conf
    if geom_signature is not None:
        if _geometry_agrees(hit.view_type, geom_signature):
            confidence = min(_MAX_CONF, base_conf + _AGREE_BOOST)
        elif _geometry_conflicts(hit.view_type, geom_signature):
            confidence = _CONFLICT_CONF
            evidence["conflict"] = True
            evidence["needs_vlm"] = True

    return ViewTypeResult(
        view_type=hit.view_type,
        confidence=confidence,
        evidence=evidence,
        uncertain=_is_uncertain(hit.view_type, confidence),
    )


def _resolve_without_keyword(geom_signature: str | None) -> ViewTypeResult:
    if geom_signature == VIEW_TYPE_PLAN:
        evidence = _make_evidence(geometry_signature=geom_signature)
        return ViewTypeResult(
            view_type=VIEW_TYPE_PLAN,
            confidence=_GEOM_ONLY_PLAN_CONF,
            evidence=evidence,
            uncertain=_is_uncertain(VIEW_TYPE_PLAN, _GEOM_ONLY_PLAN_CONF),
        )
    if geom_signature == _GEOM_SIGNATURE_SECTION_OR_ELEVATION:
        # 几何像剖/立面但无法区分二者：诚实停在 unknown，交 VLM 兜底。
        evidence = _make_evidence(geometry_signature=geom_signature, needs_vlm=True)
        return ViewTypeResult(
            view_type=VIEW_TYPE_UNKNOWN,
            confidence=_GEOM_ONLY_AMBIGUOUS_CONF,
            evidence=evidence,
            uncertain=True,
        )
    # 无关键词、无几何证据：unknown + 需 VLM。
    return ViewTypeResult(
        view_type=VIEW_TYPE_UNKNOWN,
        confidence=_UNKNOWN_CONF,
        evidence=_make_evidence(needs_vlm=True),
        uncertain=True,
    )


def _geometry_agrees(view_type: str, geom_signature: str) -> bool:
    if geom_signature == VIEW_TYPE_PLAN:
        return view_type == VIEW_TYPE_PLAN
    if geom_signature == _GEOM_SIGNATURE_SECTION_OR_ELEVATION:
        return view_type in ("section", "elevation")
    return False


def _geometry_conflicts(view_type: str, geom_signature: str) -> bool:
    if geom_signature == VIEW_TYPE_PLAN:
        return view_type in ("section", "elevation")
    if geom_signature == _GEOM_SIGNATURE_SECTION_OR_ELEVATION:
        return view_type == VIEW_TYPE_PLAN
    return False


def _is_uncertain(view_type: str, confidence: float) -> bool:
    return view_type == VIEW_TYPE_UNKNOWN or confidence < _UNCERTAIN_THRESHOLD


def _make_evidence(
    *,
    keyword_source: str | None = None,
    keyword: str | None = None,
    geometry_signature: str | None = None,
    needs_vlm: bool = False,
) -> dict:
    return {
        "keyword_source": keyword_source,
        "keyword": keyword,
        "geometry_signature": geometry_signature,
        "needs_vlm": needs_vlm,
        "conflict": False,
    }


def _geometry_signature(geom: Any) -> str | None:
    """从 DrawingGeometry 提取廉价图种签名：plan / section_or_elevation / None（不确定）。

    - 平面：横、竖两向各 ≥2 条长线（轴网双向）。
    - 剖/立面：密集水平标高文本（≥3）且非双向网格。
    duck-typed（geom 需具 lines/page_w/page_h/texts），缺字段即返回 None，绝不抛异常。
    """
    lines = getattr(geom, "lines", None)
    page_w = getattr(geom, "page_w", 0) or 0
    page_h = getattr(geom, "page_h", 0) or 0
    if not lines or page_w <= 0 or page_h <= 0:
        return None

    horizontal_long = 0
    vertical_long = 0
    for line in lines[:_MAX_LINES_SCANNED]:
        if len(line) < 4:
            continue
        x0, y0, x1, y1 = line[0], line[1], line[2], line[3]
        if abs(y0 - y1) <= _LINE_STRAIGHT_TOL_PT and abs(x1 - x0) >= _AXIS_MIN_RATIO * page_w:
            horizontal_long += 1
        elif abs(x0 - x1) <= _LINE_STRAIGHT_TOL_PT and abs(y1 - y0) >= _AXIS_MIN_RATIO * page_h:
            vertical_long += 1

    is_grid = horizontal_long >= _MIN_GRID_LINES and vertical_long >= _MIN_GRID_LINES
    elevation_text_count = _count_elevation_texts(getattr(geom, "texts", None))

    if elevation_text_count >= _MIN_ELEVATION_TEXTS and not is_grid:
        return _GEOM_SIGNATURE_SECTION_OR_ELEVATION
    if is_grid:
        return VIEW_TYPE_PLAN
    return None


def _count_elevation_texts(texts: Any) -> int:
    if not texts:
        return 0
    joined = "；".join(t[2] for t in texts if len(t) >= 3 and isinstance(t[2], str))
    return len(extract_elevations(joined))
