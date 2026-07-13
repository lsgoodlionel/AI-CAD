"""剖面 z 恢复编排（B-05 核心，MVP：仅剖面标高对齐平面楼层序）。

把每张剖面图抽出的标高序列（B-02 SectionLevels）按单体归组，对齐到平面归一化楼层表，
产出 z_overrides（供 B-04 回灌层高）与 matched_units（点亮 cross_view_match gate 的依据）。

判定「跨视图对齐成立」的 MVP 条件（对齐详设 §4.3）：
该单体存在剖面且标高数 ≥ 楼层数（升序单调），标高逐层配对到楼层底标高，层高取相邻标高差。
数不齐 / 无剖面 → 不匹配，发 ModelQualityIssue，回落默认（绝不虚高 LOD）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from core.model3d.section_level_extractor import SectionLevels
from services.model_story import (
    ModelQualityIssue,
    StoryNormalizationResult,
    detect_building_unit,
)

# 剖面标高数相对楼层数的容差（含屋顶/女儿墙标高）
_MARK_SURPLUS_TOLERANCE = 2
# 零锚校验：楼层表中视为 ±0.000 层的容差 / 配对标高允许偏差（米）
_ANCHOR_STORY_TOL_M = 0.3
_ANCHOR_MARK_TOL_M = 0.5


@dataclass(frozen=True)
class SectionZRecovery:
    z_overrides: dict[tuple[str, str], dict] = field(default_factory=dict)
    matched_units: set[str] = field(default_factory=set)
    issues: list[ModelQualityIssue] = field(default_factory=list)


def recover_section_z(
    drawings: list[dict],
    section_levels_by_drawing: dict[str, SectionLevels],
    normalization: StoryNormalizationResult,
) -> SectionZRecovery:
    """剖面标高 → 楼层层高覆盖 + 匹配单体集。纯函数，无 IO。

    选择策略：每单体在「与楼层数兼容（数量窗口）且通过零锚校验」的候选中，
    取标高最多、残差最小者。不再盲选标高最多的一张——大型项目常含基坑
    围护剖面（冠梁/支撑/坑底等施工标高，25+ 个），标高数量最多的恰恰不是
    楼层剖面；盲选会让整个单体错失兼容剖面或写入错误标高。
    """
    sections_by_unit = _sections_per_unit(drawings, section_levels_by_drawing)

    z_overrides: dict[tuple[str, str], dict] = {}
    matched_units: set[str] = set()
    issues: list[ModelQualityIssue] = []

    for unit_key, sections in sections_by_unit.items():
        stories = _ordered_stories(normalization, unit_key)
        if not stories:
            continue

        compatible = [
            section for section in sections
            if _marks_align_stories(list(section.marks), len(stories))
        ]
        if not compatible:
            issues.append(
                ModelQualityIssue(
                    issue_type="z_story_count_mismatch",
                    severity="warning",
                    message="无剖面的标高数与平面楼层数兼容，未采用剖面标高（回落默认层高）",
                    building_unit_key=unit_key,
                    payload={
                        "mark_counts": sorted(len(s.marks) for s in sections),
                        "story_count": len(stories),
                    },
                )
            )
            continue

        anchored = [
            section for section in compatible
            if _anchor_ok(stories, list(section.marks))
        ]
        if not anchored:
            issues.append(
                ModelQualityIssue(
                    issue_type="z_anchor_mismatch",
                    severity="warning",
                    message="剖面标高与 ±0.000 楼层锚不符（疑为基坑/围护剖面），未采用（回落默认层高）",
                    building_unit_key=unit_key,
                    payload={
                        "story_count": len(stories),
                        "candidate_first_marks": [
                            round(s.marks[0].elevation_m, 3) for s in compatible[:5]
                        ],
                    },
                )
            )
            continue

        best = max(
            anchored,
            key=lambda s: (len(s.marks), -float(s.fit.get("residual", 1.0))),
        )
        _assign_overrides(z_overrides, unit_key, stories, list(best.marks))
        matched_units.add(unit_key)

    return SectionZRecovery(
        z_overrides=z_overrides, matched_units=matched_units, issues=issues
    )


def _sections_per_unit(
    drawings: list[dict],
    section_levels_by_drawing: dict[str, SectionLevels],
) -> dict[str, list[SectionLevels]]:
    """按单体归组全部有标高的剖面（候选集，选择延迟到主循环按楼层数裁决）。"""
    grouped: dict[str, list[SectionLevels]] = {}
    for drawing in drawings:
        drawing_id = str(drawing.get("id") or "")
        section = section_levels_by_drawing.get(drawing_id)
        if section is None or not section.marks:
            continue
        unit_key = detect_building_unit(drawing).unit_key
        grouped.setdefault(unit_key, []).append(section)
    return grouped


def _anchor_ok(stories: list, marks: list) -> bool:
    """零锚校验：楼层表存在 ±0.000 层时，逐层配对后该层标高必须 ≈0。

    把「标高数量凑巧兼容但整体错位」的语义错配（典型：基坑围护剖面的
    冠梁/支撑/坑底标高）确定性拒之门外。楼层表无零标高层（如纯地下
    阶段）→ 无锚可校，放行（数量窗口仍在把关）。
    """
    for index, story in enumerate(stories):
        if abs(story.elevation_m) <= _ANCHOR_STORY_TOL_M:
            return (
                index < len(marks)
                and abs(marks[index].elevation_m) <= _ANCHOR_MARK_TOL_M
            )
    return True


def _ordered_stories(normalization: StoryNormalizationResult, unit_key: str) -> list:
    levels = normalization.stories_by_building.get(unit_key) or []
    return sorted(levels, key=lambda level: level.story_order)


def _marks_align_stories(marks: list, story_count: int) -> bool:
    """标高数须 ≥ 楼层数且不超过楼层数+容差，且升序单调。"""
    if story_count == 0 or len(marks) < story_count:
        return False
    if len(marks) > story_count + _MARK_SURPLUS_TOLERANCE:
        return False
    elevations = [mark.elevation_m for mark in marks]
    return all(b > a for a, b in zip(elevations, elevations[1:]))


def _assign_overrides(
    z_overrides: dict[tuple[str, str], dict],
    unit_key: str,
    stories: list,
    marks: list,
) -> None:
    """逐层配对：story[i] 底标高 = marks[i]，层高 = marks[i+1] − marks[i]。

    顶层若无上一标高（标高数恰等于楼层数）→ 沿用相邻标高差，避免越界。
    """
    for index, story in enumerate(stories):
        bottom = marks[index].elevation_m
        if index + 1 < len(marks):
            height = round(marks[index + 1].elevation_m - bottom, 3)
        elif index > 0:
            height = round(bottom - marks[index - 1].elevation_m, 3)
        else:
            height = 0.0  # 单标高单层：无从推层高，回落默认（_resolve_story_height 处理）
        z_overrides[(unit_key, story.story_key)] = {
            "height_m": height,
            "elevation_bottom_m": round(bottom, 3),
            "source": "section",
            "confidence": round(float(marks[index].confidence), 4),
        }
