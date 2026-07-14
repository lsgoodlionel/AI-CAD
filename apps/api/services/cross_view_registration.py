"""三视图配准与 z 装配（B-09）。

以轴网锚点（B-08）把平面(xy)、剖面(z+一向)、立面(z+另一向)配准到统一坐标系：
- 水平：复用 model_elements.register_offset（共有轴号中位数平移，退化为一维）统一轴号坐标。
- 竖直：剖面 z 标定以 ±0.000 为绝对锚点，跨图直接可比，无需平移。

产出统一标高表 + 一致性评分 + 冲突显式记录（对齐 CROSS_VIEW_Z_RECOVERY_DESIGN §2.4）。
冲突时不静默取一，逐条记录；配准失败优雅降级到剖面单证据（不整链崩）。

D-10：OCR ``axis_anchors``（core/model3d/ocr/consume.py）接入本模块，作为几何轴网
（grid_anchor_extractor）识别不到轴号时的补充锚点源——只补几何缺失的标签，几何命中
永远优先，绝不覆盖；每个轴号最终归入 ``ZRegistration.axis_label_sources`` 标注
"geometry" / "ocr"，供上游判断该锚点是否需要人工复核。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Mapping

from core.model3d.elevation_opening_extractor import ElevationOpenings
from core.model3d.grid_anchor_extractor import GridAxis, GridSystem, to_axes_dict
from core.model3d.provenance import build_provenance
from core.model3d.section_level_extractor import SectionLevels
from services.model_elements import register_offset

# 跨视图标高互校容差（米）
_Z_TOLERANCE_M = 0.6
# 判定「多视图一致」强证据的一致性阈值
_CONSISTENCY_THRESHOLD = 0.9
# 单视图未互校时的中性评分
_UNVERIFIED_SCORE = 0.5
# OCR 轴号锚点补入配准的最低置信（几何配准对误读更敏感，门槛高于 OCR 默认 0.6）
_OCR_AXIS_MIN_CONFIDENCE = 0.75
# 国标建施图轴网命名通行约定：纯数字（含字母后缀如 "1a"）为竖向轴线编号，
# 其余（字母/中文）为横向轴线编号。无几何线信息时用此规则判定 OCR 锚点方向，
# 仅是补缺失标签的启发式——与几何轴号冲突时以几何为准，不参与仲裁。
_NUMERIC_AXIS_LABEL_RE = re.compile(r"^\d+[A-Za-z]?$")


@dataclass(frozen=True)
class SectionView:
    drawing_id: str
    grid: GridSystem
    levels: SectionLevels
    ocr_axis_anchors: tuple[dict, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ElevationView:
    drawing_id: str
    grid: GridSystem
    openings: ElevationOpenings
    ocr_axis_anchors: tuple[dict, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ZRegistration:
    levels: tuple[dict, ...] = ()          # [{elevation_m, source, confidence}]
    axis_map: dict[str, float] = field(default_factory=dict)   # label -> 平面帧坐标
    axis_label_sources: dict[str, str] = field(default_factory=dict)  # label -> geometry|ocr
    consistency_score: float = 0.0
    conflicts: tuple[dict, ...] = ()
    matched: bool = False                  # 达成多视图一致强证据


def register_views(
    plan_grid: GridSystem | None,
    sections: list[SectionView],
    elevations: list[ElevationView],
    *,
    plan_ocr_anchors: list[dict] | None = None,
) -> ZRegistration:
    """配准三视图，产出统一标高表 + 一致性 + 冲突。任何异常降级为空，绝不抛。

    ``plan_ocr_anchors``：平面图的 OCR 轴号锚点（``ocr.consume.axis_anchors`` 输出），
    剖面/立面的 OCR 锚点走各自 ``SectionView.ocr_axis_anchors`` / ``ElevationView.ocr_axis_anchors``。
    """
    valid_sections = [s for s in sections if s.levels.marks]
    axis_map, axis_label_sources = _build_axis_map(
        plan_grid, valid_sections, elevations, plan_ocr_anchors
    )
    levels = _unify_levels(valid_sections)

    consistency_score, conflicts = _cross_check(valid_sections, elevations)
    matched = (
        bool(valid_sections)
        and bool(elevations)
        and not conflicts
        and consistency_score >= _CONSISTENCY_THRESHOLD
    )
    return ZRegistration(
        levels=levels,
        axis_map=axis_map,
        axis_label_sources=axis_label_sources,
        consistency_score=round(consistency_score, 4),
        conflicts=tuple(conflicts),
        matched=matched,
    )


def _build_axis_map(
    plan_grid: GridSystem | None,
    sections: list[SectionView],
    elevations: list[ElevationView],
    plan_ocr_anchors: list[dict] | None = None,
) -> tuple[dict[str, float], dict[str, str]]:
    """统一各视图轴号到「参考帧」（优先平面）坐标系；返回 (axis_map, axis_label_sources)。

    每个视图先做「几何轴网 ∪ OCR 轴号锚点」合并（geometry 命中优先，OCR 只补缺失标签），
    再走既有的 register_offset 一维平移配准——OCR 补的标签因此享受与几何标签相同的
    坐标系归一化，而不是被硬塞一个未配准的原始像素坐标。
    """
    plan_entry = _merge_ocr_axes(plan_grid, plan_ocr_anchors) if plan_grid is not None else None
    view_entries = [
        _merge_ocr_axes(view.grid, view.ocr_axis_anchors)
        for view in (*sections, *elevations)
    ]

    frame_entry = plan_entry if plan_entry and _has_labels(plan_entry[0]) else None
    if frame_entry is None:
        frame_entry = next((entry for entry in view_entries if _has_labels(entry[0])), None)
    if frame_entry is None:
        return {}, {}

    frame, frame_sources = frame_entry
    axis_map: dict[str, float] = {}
    axis_label_sources: dict[str, str] = {}
    for axis in (*frame.axes_x, *frame.axes_y):
        if axis.label:
            axis_map.setdefault(axis.label, axis.coord)
            axis_label_sources.setdefault(axis.label, frame_sources.get(axis.label, "geometry"))

    frame_axes = to_axes_dict(frame)
    all_entries = ([plan_entry] if plan_entry else []) + view_entries
    for grid, sources in all_entries:
        if grid is frame or not _has_labels(grid):
            continue
        dx, _dy = register_offset(frame_axes, to_axes_dict(grid))
        for axis in (*grid.axes_x, *grid.axes_y):
            if axis.label and axis.label not in axis_map:
                axis_map[axis.label] = round(axis.coord + dx, 3)
                axis_label_sources[axis.label] = sources.get(axis.label, "geometry")
    return axis_map, axis_label_sources


def _merge_ocr_axes(
    grid: GridSystem,
    ocr_anchors: Iterable[Mapping] | None,
) -> tuple[GridSystem, dict[str, str]]:
    """几何轴网 ∪ OCR 轴号锚点：geometry 命中优先，OCR 只补几何未识别到的标签。

    低于 ``_OCR_AXIS_MIN_CONFIDENCE`` 的锚点不采信；同一标签多个 OCR 候选取置信最高者
    （确定性 tie-break，不做概率融合）。方向判定见模块顶部 ``_NUMERIC_AXIS_LABEL_RE`` 注释。
    """
    sources: dict[str, str] = {
        axis.label: "geometry" for axis in (*grid.axes_x, *grid.axes_y) if axis.label
    }
    if not ocr_anchors:
        return grid, sources

    by_label: dict[str, Mapping] = {}
    for anchor in ocr_anchors:
        label = str(anchor.get("label") or "").strip()
        confidence = float(anchor.get("confidence") or 0.0)
        if not label or label in sources or confidence < _OCR_AXIS_MIN_CONFIDENCE:
            continue
        best = by_label.get(label)
        if best is None or confidence > float(best.get("confidence") or 0.0):
            by_label[label] = anchor

    if not by_label:
        return grid, sources

    extra_x: list[GridAxis] = []
    extra_y: list[GridAxis] = []
    for label, anchor in by_label.items():
        center = anchor.get("center") or (0.0, 0.0)
        if _NUMERIC_AXIS_LABEL_RE.match(label):
            extra_x.append(GridAxis(label=label, coord=float(center[0])))
        else:
            extra_y.append(GridAxis(label=label, coord=float(center[1])))
        sources[label] = "ocr"

    axes_x = grid.axes_x + tuple(extra_x)
    axes_y = grid.axes_y + tuple(extra_y)
    total = len(axes_x) + len(axes_y)
    labeled = sum(1 for axis in (*axes_x, *axes_y) if axis.label)
    merged = GridSystem(
        axes_x=axes_x,
        axes_y=axes_y,
        confidence=round(labeled / total, 4) if total else 0.0,
        unlabeled=labeled == 0,
    )
    return merged, sources


def _unify_levels(sections: list[SectionView]) -> tuple[dict, ...]:
    """统一标高表：取标高最多的剖面为主，逐层带 provenance。"""
    if not sections:
        return ()
    best = max(sections, key=lambda view: len(view.levels.marks))
    entries: list[dict] = []
    for mark in best.levels.marks:
        prov = build_provenance(
            "section",
            confidence=mark.confidence,
            evidence_ref={"drawing_id": best.drawing_id, **(mark.source_ref or {})},
        )
        entries.append(
            {
                "elevation_m": mark.elevation_m,
                "source": prov.source,
                "confidence": prov.confidence,
                "estimated": prov.estimated,
            }
        )
    return tuple(entries)


def _cross_check(
    sections: list[SectionView],
    elevations: list[ElevationView],
) -> tuple[float, list[dict]]:
    """剖面↔立面 z 互校 + 剖面↔剖面一致性；产出评分与冲突清单。"""
    checks_passed = 0
    checks_total = 0
    conflicts: list[dict] = []

    section_range = _section_elevation_range(sections)

    # 剖面↔立面：洞口标高须落在剖面 z 区间内
    if section_range is not None:
        low, high = section_range
        for view in elevations:
            for opening in view.openings.openings:
                head = opening.head_h_m
                if head is None:
                    continue
                checks_total += 1
                if low - _Z_TOLERANCE_M <= head <= high + _Z_TOLERANCE_M:
                    checks_passed += 1
                else:
                    conflicts.append(
                        {
                            "kind": "section_elevation_z",
                            "elevation_id": view.drawing_id,
                            "opening_head_m": head,
                            "section_range": [low, high],
                        }
                    )

    # 剖面↔剖面：顶标高互校
    conflicts.extend(_check_sections_agreement(sections))
    section_pairs = max(len(sections) - 1, 0)
    checks_total += section_pairs
    checks_passed += section_pairs - sum(
        1 for c in conflicts if c["kind"] == "section_section_z"
    )

    if checks_total == 0:
        # 有剖面但无可互校项 → 中性未验证；完全无视图 → 0
        return (_UNVERIFIED_SCORE if (sections or elevations) else 0.0), conflicts
    return checks_passed / checks_total, conflicts


def _check_sections_agreement(sections: list[SectionView]) -> list[dict]:
    conflicts: list[dict] = []
    if len(sections) < 2:
        return conflicts
    reference = _top_elevation(sections[0])
    for view in sections[1:]:
        top = _top_elevation(view)
        if reference is None or top is None:
            continue
        if abs(top - reference) > _Z_TOLERANCE_M:
            conflicts.append(
                {
                    "kind": "section_section_z",
                    "section_a": sections[0].drawing_id,
                    "section_b": view.drawing_id,
                    "top_a_m": reference,
                    "top_b_m": top,
                }
            )
    return conflicts


def _section_elevation_range(sections: list[SectionView]) -> tuple[float, float] | None:
    elevations = [
        mark.elevation_m for view in sections for mark in view.levels.marks
    ]
    if not elevations:
        return None
    return min(elevations), max(elevations)


def _top_elevation(view: SectionView) -> float | None:
    marks = view.levels.marks
    return max(mark.elevation_m for mark in marks) if marks else None


def _has_labels(grid: GridSystem | None) -> bool:
    if grid is None:
        return False
    return any(axis.label for axis in (*grid.axes_x, *grid.axes_y))
