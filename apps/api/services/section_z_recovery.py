"""剖面 z 恢复编排（B-05 核心；P2 阶段A 升级为最近邻配准，点亮 cross_view_match gate）。

把每张剖面图抽出的标高序列（B-02 SectionLevels）按单体归组，对齐到平面归一化楼层表，
产出 z_overrides（供 B-04 回灌层高）与 matched_units（点亮 cross_view_match gate 的依据）。

P2 阶段A 最近邻配准（替代 MVP 的「标高数≈楼层数」强绑定，详见
`docs/MODEL_P2_PLAN.md` §2 阶段A）：真实竣工图的剖面标高数几乎不可能恰好等于
楼层数（含女儿墙/设备夹层/基坑围护等噪声标高），旧口径一律回落默认层高，从未
真正匹配过。新口径：

1. 标高先经 `section_level_extractor.filter_main_sequence` 去女儿墙/设备夹层噪声
   （间距 < 2.8m 的相邻标高视为噪声）。
2. 数量较多的一侧（marks 或 stories）滑窗，寻找与另一侧等长、零锚校验通过
   （若楼层表存在 ±0.000 层）、标高间距最均匀（变异系数最小）的连续对齐——
   允许剖面标高数 > 楼层数（从中取"主楼面标高子集"），也允许 < 楼层数（部分覆盖）。
3. 覆盖率（= 命中楼层数 / 楼层总数）≥ 70% 且间距变异系数达标才判定 matched，
   否则回落默认层高（绝不虚高 LOD 原则不变）。
4. 每单体（`detect_building_unit`）独立配准，各单体各自的剖面各自验证。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from core.model3d.section_level_extractor import LevelMark, SectionLevels, filter_main_sequence
from core.model3d.vlm_read.types import ElevationCandidate
from services.model_story import (
    ModelQualityIssue,
    StoryNormalizationResult,
    detect_building_unit,
)

# 零锚校验：楼层表中视为 ±0.000 层的容差 / 配对标高允许偏差（米）
_ANCHOR_STORY_TOL_M = 0.3
_ANCHOR_MARK_TOL_M = 0.5

# 置信门槛（P2 阶段A §2 第3点）：覆盖率下限
_MIN_MATCH_COVERAGE = 0.7
# 窗口内相邻标高差变异系数（标准差/均值）上限：过高说明间距忽大忽小，
# 大概率不是同一套真实楼层序列（配对残差门槛，绝不虚高兜底）
_MAX_SPACING_CV = 0.6

# ── VLM 第二标高源（后续工作 item1）──────────────────────────────
# VLM 候选标高置信度硬顶：低于矢量/OCR 文本基线置信度（见 section_level_extractor
# ._BASE_TEXT_ONLY=0.6），确保矢量/OCR 确定性源永远优先，VLM 仅补空、绝不越权
# 盖过更可信的源（即便 VLM 自报置信度更高，也要在本管线内被压低）。
_VLM_CONFIDENCE_CAP = 0.5
# 主源（矢量/OCR）标高数低于此才视为「不足」，允许 VLM 候选介入补充；与
# section_level_extractor._fit_calibration 的最小拟合点数(2)同口径——不足 2 点
# 连基本线性标定/两点配准都做不到。
_MIN_PRIMARY_MARKS = 2
# VLM 标高与已有标高视为同一实体标高的容差（米）：避免同一层高被计入两次
_VLM_DEDUPE_TOL_M = 0.05


@dataclass(frozen=True)
class SectionZRecovery:
    z_overrides: dict[tuple[str, str], dict] = field(default_factory=dict)
    matched_units: set[str] = field(default_factory=set)
    issues: list[ModelQualityIssue] = field(default_factory=list)


@dataclass(frozen=True)
class _Alignment:
    """一次候选配准：stories[story_start:story_start+window_size] 与
    marks[mark_start:mark_start+window_size] 逐位对应。"""
    story_start: int
    mark_start: int
    window_size: int
    spacing_cv: float


@dataclass(frozen=True)
class _SectionEvaluation:
    """单张剖面对某单体楼层表的配准结果（诊断 + 决策共用，避免重复计算）。"""
    marks: list
    alignment: _Alignment | None
    coverage: float  # alignment 为 None 时为 0.0


def recover_section_z(
    drawings: list[dict],
    section_levels_by_drawing: dict[str, SectionLevels],
    normalization: StoryNormalizationResult,
    vlm_elevations_by_drawing: dict[str, tuple[ElevationCandidate, ...]] | None = None,
) -> SectionZRecovery:
    """剖面标高 → 楼层层高覆盖 + 匹配单体集。纯函数，无 IO。

    每单体在全部候选剖面中，取通过置信门槛（覆盖率 ≥70% + 间距变异系数达标）
    且覆盖率最高、间距最均匀者。无一通过 → 回落默认层高 + 发 ModelQualityIssue。

    ``vlm_elevations_by_drawing``（可选）：VLM 读图产出的标高候选，作为矢量/OCR
    之外的**第二标高源**——仅当某剖面主源标高数不足（< `_MIN_PRIMARY_MARKS`）时
    才补入，且置信度硬顶到 `_VLM_CONFIDENCE_CAP`（矢量/OCR 优先，VLM 仅补空，
    绝不越权覆盖）。合并后仍走本函数既有的覆盖率/零锚/间距一致性门禁——VLM
    候选凑不齐达标序列，一样回落默认层高（绝不虚高）。为空/None → 原样按矢量/OCR
    单源恢复，零差异回归。
    """
    merged_levels = _merge_vlm_elevations(section_levels_by_drawing, vlm_elevations_by_drawing)
    sections_by_unit = _sections_per_unit(drawings, merged_levels)

    z_overrides: dict[tuple[str, str], dict] = {}
    matched_units: set[str] = set()
    issues: list[ModelQualityIssue] = []

    for unit_key, sections in sections_by_unit.items():
        stories = _ordered_stories(normalization, unit_key)
        if not stories:
            continue

        evaluations = _evaluate_sections(stories, sections)
        picked = _pick_best_section(evaluations)
        if picked is None:
            issues.append(_no_match_issue(unit_key, stories, evaluations))
            continue

        marks, alignment = picked
        stories_window = stories[
            alignment.story_start: alignment.story_start + alignment.window_size
        ]
        marks_window = marks[
            alignment.mark_start: alignment.mark_start + alignment.window_size
        ]
        _assign_overrides(z_overrides, unit_key, stories_window, marks_window)
        matched_units.add(unit_key)

    return SectionZRecovery(
        z_overrides=z_overrides, matched_units=matched_units, issues=issues
    )


# 围护/基坑/支护/挡土/地质类剖面：其标高是基坑围护结构（挡土、支护、深坑）的
# 竖向标高，**不对应建筑楼层标高**。若纳入楼层 z 恢复，等于拿挡土/基坑标高冒充
# 楼层——既违反「绝不虚高」，又用无关标高污染候选集、把真剖面挤出配准窗口。
# 实测（上海大歌剧院竣工图）：24 张 section 中 20 张属此类，全被默认归到 main 单体，
# 淹没了仅有的建筑结构剖面。故在归组阶段前置剔除。
_NON_FLOOR_SECTION_RE = re.compile(r"围护|基坑|深坑|支护|挡土|地质")


def _is_non_floor_section(drawing: dict) -> bool:
    """标题/图号命中围护·基坑类关键词 → 非楼层剖面，不参与楼层 z 恢复。"""
    text = f"{drawing.get('title') or ''}{drawing.get('drawing_no') or ''}"
    return bool(_NON_FLOOR_SECTION_RE.search(text))


def _sections_per_unit(
    drawings: list[dict],
    section_levels_by_drawing: dict[str, SectionLevels],
) -> dict[str, list[SectionLevels]]:
    """按单体归组全部有标高的**建筑楼层**剖面（候选集，选择延迟到主循环按配准质量裁决）。

    前置剔除围护/基坑/支护类剖面（其标高非楼层标高，见 `_NON_FLOOR_SECTION_RE`）。
    """
    grouped: dict[str, list[SectionLevels]] = {}
    for drawing in drawings:
        if _is_non_floor_section(drawing):
            continue
        drawing_id = str(drawing.get("id") or "")
        section = section_levels_by_drawing.get(drawing_id)
        if section is None or not section.marks:
            continue
        unit_key = detect_building_unit(drawing).unit_key
        grouped.setdefault(unit_key, []).append(section)
    return grouped


def _merge_vlm_elevations(
    section_levels_by_drawing: dict[str, SectionLevels],
    vlm_elevations_by_drawing: dict[str, tuple[ElevationCandidate, ...]] | None,
) -> dict[str, SectionLevels]:
    """矢量/OCR 主源标高不足的剖面，用 VLM 候选标高补上（第二标高源）。

    仅对 ``vlm_elevations_by_drawing`` 中列出、且主源 marks 数 < `_MIN_PRIMARY_MARKS`
    的剖面生效；主源已足够时 VLM 候选让位（确定性优先，绝不越权覆盖）。
    为空/None → 原样返回同一 dict（零差异回归）。
    """
    if not vlm_elevations_by_drawing:
        return section_levels_by_drawing
    merged = dict(section_levels_by_drawing)
    for drawing_id, candidates in vlm_elevations_by_drawing.items():
        if not candidates:
            continue
        primary = merged.get(drawing_id)
        if primary is not None and len(primary.marks) >= _MIN_PRIMARY_MARKS:
            continue  # 主源已足够，VLM 候选让位
        vlm_marks = _vlm_marks_from_candidates(candidates)
        if not vlm_marks:
            continue
        merged[drawing_id] = _combine_with_vlm(primary, vlm_marks)
    return merged


def _vlm_marks_from_candidates(
    candidates: tuple[ElevationCandidate, ...],
) -> list[LevelMark]:
    """VLM ElevationCandidate → LevelMark，置信度硬顶到 `_VLM_CONFIDENCE_CAP`。"""
    marks: list[LevelMark] = []
    for candidate in candidates:
        value = round(float(candidate.value_m), 3)
        marks.append(
            LevelMark(
                elevation_m=value,
                label=candidate.evidence or f"{value:+.3f}",
                confidence=round(min(float(candidate.confidence), _VLM_CONFIDENCE_CAP), 4),
                source_ref={"vlm": True, "evidence": candidate.evidence},
            )
        )
    return marks


def _combine_with_vlm(
    primary: SectionLevels | None, vlm_marks: list[LevelMark]
) -> SectionLevels:
    """主源标高（若有）+ 去重后的 VLM 标高，合并为一个 SectionLevels。

    去重：VLM 标高与已有标高相差 < `_VLM_DEDUPE_TOL_M` 视为同一实体标高，
    保留主源侧（避免同一层高被计入两次、也避免 VLM 置信度覆盖主源置信度）。
    """
    primary_marks = list(primary.marks) if primary is not None else []
    combined = list(primary_marks)
    for vlm_mark in vlm_marks:
        if any(
            abs(vlm_mark.elevation_m - existing.elevation_m) < _VLM_DEDUPE_TOL_M
            for existing in combined
        ):
            continue
        combined.append(vlm_mark)
    combined_sorted = tuple(sorted(combined, key=lambda mark: mark.elevation_m))
    fit = {
        **(primary.fit if primary is not None else {}),
        "vlm_supplement": True,
        "vlm_mark_count": len(vlm_marks),
    }
    return SectionLevels(marks=combined_sorted, reason=None, fit=fit)


def _ordered_stories(normalization: StoryNormalizationResult, unit_key: str) -> list:
    levels = normalization.stories_by_building.get(unit_key) or []
    return sorted(levels, key=lambda level: level.story_order)


# ── 最近邻配准 ────────────────────────────────────────────────────


def _evaluate_sections(stories: list, sections: list[SectionLevels]) -> list[_SectionEvaluation]:
    """对单体全部候选剖面逐一配准评估（标高先经噪声过滤，与抽取器同口径）。"""
    evaluations: list[_SectionEvaluation] = []
    for section in sections:
        marks = filter_main_sequence(list(section.marks))
        if not marks:
            continue
        alignment = _best_alignment(stories, marks)
        coverage = round(alignment.window_size / len(stories), 6) if alignment else 0.0
        evaluations.append(_SectionEvaluation(marks=marks, alignment=alignment, coverage=coverage))
    return evaluations


def _pick_best_section(
    evaluations: list[_SectionEvaluation],
) -> tuple[list, _Alignment] | None:
    """在通过置信门槛的候选里，取覆盖率最高、间距最均匀者。全不通过 → None。"""
    best: tuple[list, _Alignment] | None = None
    best_quality: tuple[float, float] | None = None
    for evaluation in evaluations:
        alignment = evaluation.alignment
        if alignment is None:
            continue
        if evaluation.coverage < _MIN_MATCH_COVERAGE or alignment.spacing_cv > _MAX_SPACING_CV:
            continue
        quality = (evaluation.coverage, -alignment.spacing_cv)
        if best_quality is None or quality > best_quality:
            best_quality = quality
            best = (evaluation.marks, alignment)
    return best


def _no_match_issue(
    unit_key: str, stories: list, evaluations: list[_SectionEvaluation]
) -> ModelQualityIssue:
    """诊断：区分「无候选通过零锚校验」与「覆盖率/间距一致性不足」，复用既有 issue_type 语义。"""
    if not any(evaluation.alignment is not None for evaluation in evaluations):
        return ModelQualityIssue(
            issue_type="z_anchor_mismatch",
            severity="warning",
            message="剖面标高与 ±0.000 楼层锚不符（疑为基坑/围护剖面），未采用（回落默认层高）",
            building_unit_key=unit_key,
            payload={"story_count": len(stories)},
        )
    best_coverage = max((evaluation.coverage for evaluation in evaluations), default=0.0)
    return ModelQualityIssue(
        issue_type="z_story_count_mismatch",
        severity="warning",
        message="剖面标高覆盖楼层数/间距一致性未达置信门槛，未采用剖面标高（回落默认层高）",
        building_unit_key=unit_key,
        payload={
            "story_count": len(stories),
            "best_coverage": round(best_coverage, 3),
            "min_coverage_required": _MIN_MATCH_COVERAGE,
            "mark_counts": sorted(len(evaluation.marks) for evaluation in evaluations),
        },
    )


def _zero_story_index(stories: list) -> int | None:
    for index, story in enumerate(stories):
        if abs(story.elevation_m) <= _ANCHOR_STORY_TOL_M:
            return index
    return None


def _best_alignment(stories: list, marks: list) -> _Alignment | None:
    """滑窗最近邻配准：数量较多一侧滑窗，寻找零锚校验通过、间距最均匀的对齐。

    marks 数 ≥ 楼层数：楼层全覆盖，marks 侧滑窗挑"主楼面标高子集"（允许含女儿墙/
    基坑等噪声标高的剖面里，选出真正对应楼层的连续段）。
    marks 数 < 楼层数：标高全用，stories 侧滑窗挑"这批标高对应哪几层"（阶段A
    允许部分覆盖，由调用方按覆盖率门槛裁决是否采用）。
    楼层表存在 ±0.000 层时，候选窗口必须包含该层且对应标高 ≈0——否则无从校验
    偏移是否正确，直接排除（绝不虚高）。
    """
    story_count = len(stories)
    mark_count = len(marks)
    if story_count == 0 or mark_count == 0:
        return None

    window_size = min(story_count, mark_count)
    zero_index = _zero_story_index(stories)
    candidates: list[_Alignment] = []

    if mark_count >= story_count:
        for mark_start in range(mark_count - window_size + 1):
            if not _anchor_check(marks, mark_start, zero_index):
                continue
            window = marks[mark_start: mark_start + window_size]
            candidates.append(_Alignment(0, mark_start, window_size, _spacing_cv(window)))
    else:
        for story_start in range(story_count - window_size + 1):
            if zero_index is not None and not (
                story_start <= zero_index < story_start + window_size
            ):
                continue  # 候选窗口须含零锚层才可校验，否则跳过（绝不虚高）
            offset = (zero_index - story_start) if zero_index is not None else None
            if not _anchor_check(marks, 0, offset):
                continue
            candidates.append(_Alignment(story_start, 0, window_size, _spacing_cv(marks)))

    if not candidates:
        return None
    return min(candidates, key=lambda candidate: candidate.spacing_cv)


def _anchor_check(marks: list, mark_start: int, offset: int | None) -> bool:
    if offset is None:
        return True  # 楼层表无 ±0.000 层，无锚可校，放行（覆盖率/间距一致性仍把关）
    index = mark_start + offset
    if index < 0 or index >= len(marks):
        return False
    return abs(marks[index].elevation_m) <= _ANCHOR_MARK_TOL_M


def _spacing_cv(window: list) -> float:
    """窗口内相邻标高差的变异系数（标准差/均值）；样本不足（<2 个间距）时记 0，不惩罚。"""
    if len(window) < 3:
        return 0.0
    gaps = [window[i + 1].elevation_m - window[i].elevation_m for i in range(len(window) - 1)]
    mean_gap = sum(gaps) / len(gaps)
    if mean_gap <= 0:
        return float("inf")
    variance = sum((gap - mean_gap) ** 2 for gap in gaps) / len(gaps)
    return (variance ** 0.5) / mean_gap


def _assign_overrides(
    z_overrides: dict[tuple[str, str], dict],
    unit_key: str,
    stories_window: list,
    marks_window: list,
) -> None:
    """逐层配对：stories_window[i] 底标高 = marks_window[i]，层高 = 相邻标高差。

    顶层若无下一个标高（窗口内标高数恰等于窗口楼层数，两者始终相等）→ 沿用与
    上一标高的差值；单层窗口（window_size=1）无从推层高 → 0（回落
    `_resolve_story_height` 默认兜底）。
    """
    for index, story in enumerate(stories_window):
        bottom = marks_window[index].elevation_m
        if index + 1 < len(marks_window):
            height = round(marks_window[index + 1].elevation_m - bottom, 3)
        elif index > 0:
            height = round(bottom - marks_window[index - 1].elevation_m, 3)
        else:
            height = 0.0
        z_overrides[(unit_key, story.story_key)] = {
            "height_m": height,
            "elevation_bottom_m": round(bottom, 3),
            "source": "section",
            "confidence": round(float(marks_window[index].confidence), 4),
        }
