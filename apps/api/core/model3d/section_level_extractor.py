"""剖面标高线抽取（B-02）：剖面几何 → 有序「标高 → 绝对标高(m)」序列。

确定性方法（无 LLM，对齐 CROSS_VIEW_Z_RECOVERY_DESIGN §2.2）：
1. 解析标高文本（±0.000 / -3.600 / 23.700），复用 element_recognizer 的标高正则与范围。
2. 就近绑定水平标高线（文本 y 附近的近水平线）→ 建立「图面 y(pt) → 真实标高(m)」标定对。
3. 最小二乘拟合 z 标定线性映射，斜率须为负（页面 y 向下、标高向上）；残差供置信度。
4. 按标高去重升序，产出带 source_ref / confidence 的 LevelMark。

识别不到标高文本时返回空 marks + 明确 reason，绝不臆造。
"""
from __future__ import annotations

from dataclasses import dataclass, field

# 复用标高正则与合理范围（详设明确要求复用，保持与平面标高抽取一致口径）
from core.model3d.element_recognizer import _ELEVATION_RANGE, _ELEVATION_RE
from core.model3d.types import DrawingGeometry

# 近水平线容差 / 文本-线绑定 y 容差（pt）
_HORIZONTAL_TOL_PT = 2.0
_BIND_TOL_PT = 10.0

# 置信度基线
_BASE_BOUND = 0.9        # 绑定到标高线
_BASE_TEXT_ONLY = 0.6    # 仅文本、无邻近线
_UNVALIDATED_FIT_QUALITY = 0.6  # 单点无法拟合线性映射时的 fit_quality 上限
_WRONG_SIGN_FIT_QUALITY = 0.4   # 斜率符号错误（非负）时的 fit_quality

# 阶段A 鲁棒筛标高（P2 §2 阶段A 第2点）：小于此间距的相邻标高视为女儿墙/
# 设备夹层/施工阶段标高噪声，非独立楼层标高。与楼层归一化
# services.model_story.MIN_STORY_SPACING_M 同口径，保持"过近即噪声"判据一致。
_MIN_PLAUSIBLE_STORY_GAP_M = 2.8


@dataclass(frozen=True)
class LevelMark:
    """单条标高标定结果。"""
    elevation_m: float          # 真实标高（±0.000 基准，米）
    label: str                  # 原始标高文本（±0.000 / -3.600）
    confidence: float           # 0~1
    source_ref: dict = field(default_factory=dict)  # {y_pt, text, bound}


@dataclass(frozen=True)
class SectionLevels:
    """一张剖面图的标高抽取产物。"""
    marks: tuple[LevelMark, ...] = ()
    reason: str | None = None   # 空结果时的原因（no_elevation_text / no_valid_marks）
    fit: dict = field(default_factory=dict)  # {slope_m_per_pt, residual, tie_point_count}


@dataclass(frozen=True)
class _RawMark:
    elevation_m: float
    label: str
    y_pt: float
    bound: bool


def extract_section_levels(geom: DrawingGeometry) -> SectionLevels:
    """从剖面几何抽取标高序列。任何异常/空输入 → 空 marks + reason，绝不抛。"""
    horizontal_ys = _horizontal_line_ys(geom.lines)
    raw = _parse_elevation_texts(geom.texts, horizontal_ys)
    if not raw:
        return SectionLevels(reason="no_elevation_text")

    deduped = _dedupe_by_elevation(raw)
    fit = _fit_calibration(deduped)
    marks = tuple(
        LevelMark(
            elevation_m=round(mark.elevation_m, 3),
            label=mark.label,
            confidence=_confidence_for(mark, fit),
            source_ref={"y_pt": mark.y_pt, "text": mark.label, "bound": mark.bound},
        )
        for mark in sorted(deduped, key=lambda m: m.elevation_m)
    )
    marks = tuple(filter_main_sequence(list(marks)))
    return SectionLevels(marks=marks, reason=None, fit=fit)


def filter_main_sequence(marks: list[LevelMark]) -> list[LevelMark]:
    """RANSAC-lite 主楼面标高序筛选（P2 阶段A 鲁棒筛标高）。

    ``marks`` 须已按标高升序去重（`extract_section_levels` 内部调用；也可直接
    喂已构造好的 `LevelMark` 序列，供 `section_z_recovery` 对绕过抽取器构造的
    标高做同口径过滤）。

    迭代查找相邻间距 < `_MIN_PLAUSIBLE_STORY_GAP_M` 的一对，保留置信度更高者
    （同置信度保留靠前者，即更低标高的一个），直至相邻间距全部达标，或只剩
    ≤2 个标高（样本太少判不出"过近"，交由下游覆盖率/间距一致性门槛把关）。
    绝不抛异常、绝不臆造标高——只做剔除，不新增。
    """
    result = list(marks)
    while len(result) > 2:
        pair_index = _first_small_gap(result)
        if pair_index is None:
            break
        low_idx, high_idx = pair_index, pair_index + 1
        drop = low_idx if result[low_idx].confidence < result[high_idx].confidence else high_idx
        del result[drop]
    return result


def _first_small_gap(marks: list[LevelMark]) -> int | None:
    for index in range(len(marks) - 1):
        if marks[index + 1].elevation_m - marks[index].elevation_m < _MIN_PLAUSIBLE_STORY_GAP_M:
            return index
    return None


def _horizontal_line_ys(lines: list) -> list[float]:
    ys: list[float] = []
    for line in lines or []:
        if len(line) < 4:
            continue
        x0, y0, x1, y1 = line[0], line[1], line[2], line[3]
        if abs(y0 - y1) <= _HORIZONTAL_TOL_PT:
            ys.append((y0 + y1) / 2.0)
    return ys


def _parse_elevation_texts(texts: list, horizontal_ys: list[float]) -> list[_RawMark]:
    raw: list[_RawMark] = []
    for item in texts or []:
        if len(item) < 3:
            continue
        x, y, content = item[0], item[1], item[2]
        if not isinstance(content, str):
            continue
        parsed = _parse_elevation(content)
        if parsed is None:
            continue
        value, label = parsed
        y_pt, bound = _bind_to_line(float(y), horizontal_ys)
        raw.append(_RawMark(elevation_m=value, label=label, y_pt=y_pt, bound=bound))
    return raw


def _parse_elevation(content: str) -> tuple[float, str] | None:
    """解析首个合理范围内标高，返回 (值, 原始标注文本)。"""
    match = _ELEVATION_RE.search(content)
    if match is None:
        return None
    sign, number = match.group(1), match.group(2)
    value = float(number)
    if sign == "-":
        value = -value
    if not (_ELEVATION_RANGE[0] <= value <= _ELEVATION_RANGE[1]):
        return None
    return round(value, 3), match.group(0)


def _bind_to_line(text_y: float, horizontal_ys: list[float]) -> tuple[float, bool]:
    """就近绑定水平标高线：命中 → (线 y, True)；否则 → (文本 y, False)。"""
    if not horizontal_ys:
        return text_y, False
    nearest = min(horizontal_ys, key=lambda ly: abs(ly - text_y))
    if abs(nearest - text_y) <= _BIND_TOL_PT:
        return nearest, True
    return text_y, False


def _dedupe_by_elevation(raw: list[_RawMark]) -> list[_RawMark]:
    """按标高值去重，优先保留绑定到标高线者。"""
    best: dict[float, _RawMark] = {}
    for mark in raw:
        key = round(mark.elevation_m, 3)
        current = best.get(key)
        if current is None or (mark.bound and not current.bound):
            best[key] = mark
    return list(best.values())


def _fit_calibration(marks: list[_RawMark]) -> dict:
    """最小二乘拟合 elevation_m = slope·y_pt + intercept，返回斜率/残差/点数。"""
    count = len(marks)
    if count < 2:
        intercept = marks[0].elevation_m if marks else 0.0
        return {
            "slope_m_per_pt": 0.0,
            "intercept_m": round(intercept, 3),
            "residual": 0.0,
            "tie_point_count": count,
        }

    xs = [m.y_pt for m in marks]
    ys = [m.elevation_m for m in marks]
    mean_x = sum(xs) / count
    mean_y = sum(ys) / count
    var_x = sum((x - mean_x) ** 2 for x in xs)
    if var_x == 0:
        return {
            "slope_m_per_pt": 0.0,
            "intercept_m": round(mean_y, 3),
            "residual": 0.0,
            "tie_point_count": count,
        }

    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / var_x
    intercept = mean_y - slope * mean_x
    residual = (
        sum((y - (slope * x + intercept)) ** 2 for x, y in zip(xs, ys)) / count
    ) ** 0.5
    return {
        "slope_m_per_pt": round(slope, 6),
        "intercept_m": round(intercept, 6),
        "residual": round(residual, 6),
        "tie_point_count": count,
    }


def _confidence_for(mark: _RawMark, fit: dict) -> float:
    base = _BASE_BOUND if mark.bound else _BASE_TEXT_ONLY
    return round(base * _fit_quality(fit), 4)


def _fit_quality(fit: dict) -> float:
    """拟合质量 [0,1]：残差越小越高；单点/错向斜率显式降级。"""
    if fit.get("tie_point_count", 0) < 2:
        return _UNVALIDATED_FIT_QUALITY
    slope = fit.get("slope_m_per_pt", 0.0)
    if slope >= 0:  # 页面 y 向下、标高向上 → 斜率必负；非负说明标定异常
        return _WRONG_SIGN_FIT_QUALITY
    residual = fit.get("residual", 0.0)
    # 以 1m 为归一尺度惩罚残差（标高层高量级 m），残差 0 → 质量 1
    return max(0.0, min(1.0, 1.0 - residual))
