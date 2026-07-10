"""三视图配准与 z 装配（B-09）。

以轴网锚点（B-08）把平面(xy)、剖面(z+一向)、立面(z+另一向)配准到统一坐标系：
- 水平：复用 model_elements.register_offset（共有轴号中位数平移，退化为一维）统一轴号坐标。
- 竖直：剖面 z 标定以 ±0.000 为绝对锚点，跨图直接可比，无需平移。

产出统一标高表 + 一致性评分 + 冲突显式记录（对齐 CROSS_VIEW_Z_RECOVERY_DESIGN §2.4）。
冲突时不静默取一，逐条记录；配准失败优雅降级到剖面单证据（不整链崩）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from core.model3d.elevation_opening_extractor import ElevationOpenings
from core.model3d.grid_anchor_extractor import GridSystem, to_axes_dict
from core.model3d.provenance import build_provenance
from core.model3d.section_level_extractor import SectionLevels
from services.model_elements import register_offset

# 跨视图标高互校容差（米）
_Z_TOLERANCE_M = 0.6
# 判定「多视图一致」强证据的一致性阈值
_CONSISTENCY_THRESHOLD = 0.9
# 单视图未互校时的中性评分
_UNVERIFIED_SCORE = 0.5


@dataclass(frozen=True)
class SectionView:
    drawing_id: str
    grid: GridSystem
    levels: SectionLevels


@dataclass(frozen=True)
class ElevationView:
    drawing_id: str
    grid: GridSystem
    openings: ElevationOpenings


@dataclass(frozen=True)
class ZRegistration:
    levels: tuple[dict, ...] = ()          # [{elevation_m, source, confidence}]
    axis_map: dict[str, float] = field(default_factory=dict)   # label -> 平面帧坐标
    consistency_score: float = 0.0
    conflicts: tuple[dict, ...] = ()
    matched: bool = False                  # 达成多视图一致强证据


def register_views(
    plan_grid: GridSystem | None,
    sections: list[SectionView],
    elevations: list[ElevationView],
) -> ZRegistration:
    """配准三视图，产出统一标高表 + 一致性 + 冲突。任何异常降级为空，绝不抛。"""
    valid_sections = [s for s in sections if s.levels.marks]
    axis_map = _build_axis_map(plan_grid, valid_sections, elevations)
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
        consistency_score=round(consistency_score, 4),
        conflicts=tuple(conflicts),
        matched=matched,
    )


def _build_axis_map(
    plan_grid: GridSystem | None,
    sections: list[SectionView],
    elevations: list[ElevationView],
) -> dict[str, float]:
    """统一各视图轴号到「参考帧」（优先平面）坐标系。"""
    frame = plan_grid if _has_labels(plan_grid) else None
    other_grids = [view.grid for view in (*sections, *elevations)]
    if frame is None:
        frame = next((grid for grid in other_grids if _has_labels(grid)), None)
    if frame is None:
        return {}

    axis_map: dict[str, float] = {}
    for axis in (*frame.axes_x, *frame.axes_y):
        if axis.label:
            axis_map.setdefault(axis.label, axis.coord)

    frame_axes = to_axes_dict(frame)
    for grid in other_grids:
        if grid is frame or not _has_labels(grid):
            continue
        dx, _dy = register_offset(frame_axes, to_axes_dict(grid))
        for axis in (*grid.axes_x, *grid.axes_y):
            if axis.label and axis.label not in axis_map:
                axis_map[axis.label] = round(axis.coord + dx, 3)
    return axis_map


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
