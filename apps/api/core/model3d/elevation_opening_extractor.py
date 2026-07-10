"""立面洞口识别（B-06）：立面几何 → 门窗洞口矩形 + 尺寸/标高。

确定性方法（无 LLM，对齐 CROSS_VIEW_Z_RECOVERY_DESIGN §2.3）：
1. 复用 B-02 剖面标高标定（extract_section_levels 的 z 拟合）建立 y_pt → 标高(m) 映射；
   竖直比例尺 = |slope|（米/点），供洞口宽高换算。
2. 复用 element_recognizer._detect_axes 取横向轴号，供洞口轴跨 axis_ref。
3. 识别洞口：filled=False 的矩形（或门窗图层/块），尺寸落在洞口区间；整栋轮廓大矩形剔除。
4. 每洞口：下/上边 → sill/head 标高，x 范围 → 宽度与轴跨，图层/几何 → 门窗类型。

无 z 标定时仍按图层返回洞口几何但标 dimension_missing（尺寸 None），绝不臆造标高。
"""
from __future__ import annotations

from dataclasses import dataclass, field

# 复用轴网识别、图层门窗分类、剖面标高标定
from core.model3d.element_recognizer import _detect_axes
from core.model3d.layer_conventions import classify_by_layer
from core.model3d.section_level_extractor import extract_section_levels
from core.model3d.types import DrawingGeometry

# 洞口尺寸区间（米）：过滤装饰线脚（过小）与整栋轮廓（过大）
_OPENING_W_RANGE = (0.4, 4.0)
_OPENING_H_RANGE = (0.5, 3.5)
# 门几何启发：高瘦
_DOOR_MIN_H_M = 1.9
_DOOR_MAX_W_M = 1.6
# 去重：中心与尺寸容差（pt）
_DEDUP_TOL_PT = 6.0

_BASE_CONF_LAYER = 0.85
_BASE_CONF_GEOMETRY = 0.6
_NO_DIMENSION_PENALTY = 0.5


@dataclass(frozen=True)
class Opening:
    """单个门窗洞口识别结果。sill/head/width/height 缺 z 标定时为 None。"""
    kind: str                       # window | door | opening
    sill_h_m: float | None
    head_h_m: float | None
    width_m: float | None
    height_m: float | None
    axis_ref: str
    confidence: float
    evidence: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ElevationOpenings:
    openings: tuple[Opening, ...] = ()
    reason: str | None = None
    dimension_missing: bool = False  # 整图缺 z 标定（洞口尺寸不可解）


@dataclass(frozen=True)
class _Calibration:
    slope: float        # 标高(m) = slope · y_pt + intercept
    intercept: float
    scale: float        # 比例尺 |slope|（米/点）

    def elevation_at(self, y_pt: float) -> float:
        return self.slope * y_pt + self.intercept


def extract_elevation_openings(geom: DrawingGeometry) -> ElevationOpenings:
    """从立面几何抽取门窗洞口。任何异常/空输入 → 空 + reason，绝不抛。"""
    if not geom.rects:
        return ElevationOpenings(reason="no_rects")

    calibration = _build_calibration(geom)
    axis_x, _axis_y, _axis_idx = _detect_axes(geom.lines, geom.page_w, geom.page_h, geom.texts)

    raw: list[Opening] = []
    for index, rect in enumerate(geom.rects):
        opening = _classify_rect(rect, index, geom, calibration, axis_x)
        if opening is not None:
            raw.append(opening)

    openings = _dedupe(raw)
    if not openings:
        return ElevationOpenings(reason="no_openings", dimension_missing=calibration is None)
    return ElevationOpenings(
        openings=tuple(openings), reason=None, dimension_missing=calibration is None
    )


def _build_calibration(geom: DrawingGeometry) -> _Calibration | None:
    fit = extract_section_levels(geom).fit
    slope = float(fit.get("slope_m_per_pt", 0.0))
    if fit.get("tie_point_count", 0) < 2 or slope >= 0:
        return None  # 页面 y 向下、标高向上 → 斜率必负；否则标定无效
    return _Calibration(slope=slope, intercept=float(fit.get("intercept_m", 0.0)), scale=abs(slope))


def _classify_rect(
    rect, index: int, geom: DrawingGeometry, calibration: _Calibration | None, axis_x: list
) -> Opening | None:
    if len(rect) < 5:
        return None
    x, y, w, h, filled = rect[0], rect[1], rect[2], rect[3], rect[4]
    layer_kind = classify_by_layer(_at(geom.rect_layers, index), _at(geom.rect_blocks, index))
    is_opening_layer = layer_kind in ("door", "window")

    if calibration is None:
        # 无标定：仅凭门窗图层识别，几何尺寸不可解
        if not is_opening_layer:
            return None
        return _build_opening(
            x, y, w, h, kind=(layer_kind or "opening"),
            calibration=None, axis_x=axis_x, is_opening_layer=True,
        )

    width_m = w * calibration.scale
    height_m = h * calibration.scale
    if not is_opening_layer and not _is_opening_size(width_m, height_m):
        return None
    if not filled and not is_opening_layer and not _is_opening_size(width_m, height_m):
        return None
    kind = layer_kind if is_opening_layer else _geometry_kind(width_m, height_m)
    return _build_opening(
        x, y, w, h, kind=kind, calibration=calibration, axis_x=axis_x,
        is_opening_layer=is_opening_layer,
    )


def _build_opening(
    x: float, y: float, w: float, h: float, *,
    kind: str, calibration: _Calibration | None, axis_x: list, is_opening_layer: bool,
) -> Opening:
    dimension_missing = calibration is None
    if calibration is None:
        sill = head = width_m = height_m = None
    else:
        e_low = calibration.elevation_at(y)
        e_high = calibration.elevation_at(y + h)
        sill = round(min(e_low, e_high), 3)
        head = round(max(e_low, e_high), 3)
        width_m = round(w * calibration.scale, 3)
        height_m = round(head - sill, 3)

    base = _BASE_CONF_LAYER if is_opening_layer else _BASE_CONF_GEOMETRY
    confidence = round(base * (1.0 if calibration else _NO_DIMENSION_PENALTY), 4)
    return Opening(
        kind=kind,
        sill_h_m=sill,
        head_h_m=head,
        width_m=width_m,
        height_m=height_m,
        axis_ref=_axis_ref(x, w, axis_x),
        confidence=confidence,
        evidence={
            "x_pt": x, "y_pt": y, "w_pt": w, "h_pt": h,
            "dimension_missing": dimension_missing,
        },
    )


def _is_opening_size(width_m: float, height_m: float) -> bool:
    return (
        _OPENING_W_RANGE[0] <= width_m <= _OPENING_W_RANGE[1]
        and _OPENING_H_RANGE[0] <= height_m <= _OPENING_H_RANGE[1]
    )


def _geometry_kind(width_m: float, height_m: float) -> str:
    if height_m >= _DOOR_MIN_H_M and width_m <= _DOOR_MAX_W_M:
        return "door"
    return "window"


def _axis_ref(x: float, w: float, axis_x: list) -> str:
    """洞口 x 跨内的轴号：命中区间两端 → 'L1-L2'；仅最近一根 → 'L'。"""
    labeled = [(label, pos) for label, pos in axis_x if label]
    if not labeled:
        return ""
    inside = sorted(
        (label for label, pos in labeled if x - _DEDUP_TOL_PT <= pos <= x + w + _DEDUP_TOL_PT),
    )
    if len(inside) >= 2:
        return f"{inside[0]}-{inside[-1]}"
    if inside:
        return inside[0]
    nearest = min(labeled, key=lambda item: abs(item[1] - (x + w / 2)))
    return nearest[0]


def _dedupe(openings: list[Opening]) -> list[Opening]:
    """去除近乎重合的洞口（双线框），保留成排不同位置的独立洞口。"""
    result: list[Opening] = []
    for opening in openings:
        if any(_same_bbox(opening, kept) for kept in result):
            continue
        result.append(opening)
    return result


def _same_bbox(a: Opening, b: Opening) -> bool:
    ea, eb = a.evidence, b.evidence
    return (
        abs(ea["x_pt"] - eb["x_pt"]) <= _DEDUP_TOL_PT
        and abs(ea["y_pt"] - eb["y_pt"]) <= _DEDUP_TOL_PT
        and abs(ea["w_pt"] - eb["w_pt"]) <= _DEDUP_TOL_PT
        and abs(ea["h_pt"] - eb["h_pt"]) <= _DEDUP_TOL_PT
    )


def _at(values: list, index: int) -> str:
    return values[index] if index < len(values) else ""
