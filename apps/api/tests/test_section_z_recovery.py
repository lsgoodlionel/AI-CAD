"""剖面 z 恢复编排器测试（B-05 核心）。

把剖面标高序列对齐平面楼层序，产出 z_overrides + 匹配单体集（点亮 cross_view_match gate 的依据）。
纯函数：输入已抽取的 SectionLevels + 归一化楼层表，无 IO。
"""
import pytest

from core.model3d.section_level_extractor import LevelMark, SectionLevels
from services.model_story import normalize_story_table
from services.section_z_recovery import recover_section_z


def _drawing(drawing_id: str, title: str, drawing_no: str) -> dict:
    return {
        "id": drawing_id,
        "title": title,
        "drawing_no": drawing_no,
        "discipline": "architecture",
        "file_key": "",
        "ocr_text": "",
    }


def _marks(*elevations: float) -> SectionLevels:
    return SectionLevels(
        marks=tuple(
            LevelMark(elevation_m=e, label=f"{e:+.3f}", confidence=0.9, source_ref={})
            for e in elevations
        ),
        reason=None,
        fit={"slope_m_per_pt": -0.02, "residual": 0.0, "tie_point_count": len(elevations)},
    )


def _two_story_plan():
    drawings = [
        _drawing("p1", "一层平面图", "A-101"),
        _drawing("p2", "二层平面图", "A-201"),
    ]
    return drawings, normalize_story_table(drawings)


# ── 匹配成功 ────────────────────────────────────────────────────

@pytest.mark.unit
def test_section_marks_matching_plan_stories_produce_overrides():
    plan_drawings, normalization = _two_story_plan()
    section = _drawing("sec1", "1-1剖面图", "A-501")
    # 剖面标高：F1 底 0.0、F2 底 3.6、屋顶 7.2 → F1 层高3.6 / F2 层高3.6
    section_levels = {"sec1": _marks(0.0, 3.6, 7.2)}

    recovery = recover_section_z(
        [*plan_drawings, section], section_levels, normalization
    )

    assert "main" in recovery.matched_units
    assert recovery.z_overrides[("main", "F1")]["height_m"] == pytest.approx(3.6)
    assert recovery.z_overrides[("main", "F1")]["elevation_bottom_m"] == pytest.approx(0.0)
    assert recovery.z_overrides[("main", "F1")]["source"] == "section"
    assert recovery.z_overrides[("main", "F2")]["elevation_bottom_m"] == pytest.approx(3.6)
    assert recovery.z_overrides[("main", "F2")]["height_m"] == pytest.approx(3.6)


@pytest.mark.unit
def test_recovered_overrides_feed_normalize_story_table():
    """端到端：恢复的 overrides 回灌 normalize → 实测层高、非估算。"""
    plan_drawings, normalization = _two_story_plan()
    section = _drawing("sec1", "A-A剖面图", "A-502")
    recovery = recover_section_z(
        [*plan_drawings, section], {"sec1": _marks(0.0, 3.3, 6.6)}, normalization
    )

    result = normalize_story_table(plan_drawings, z_overrides=recovery.z_overrides)
    level = result.stories_by_building["main"][0]
    assert level.height_m == pytest.approx(3.3)
    assert level.height_estimated is False
    assert level.height_source == "section"


# ── 降级 / 边界 ─────────────────────────────────────────────────

@pytest.mark.unit
def test_no_section_drawings_yields_empty_recovery():
    plan_drawings, normalization = _two_story_plan()
    recovery = recover_section_z(plan_drawings, {}, normalization)
    assert recovery.matched_units == set()
    assert recovery.z_overrides == {}


@pytest.mark.unit
def test_mark_count_below_story_count_not_matched_and_issue():
    plan_drawings, normalization = _two_story_plan()
    section = _drawing("sec1", "1-1剖面图", "A-501")
    # 仅 1 个标高，少于 2 层 → 无法对齐
    recovery = recover_section_z(
        [*plan_drawings, section], {"sec1": _marks(0.0)}, normalization
    )
    assert "main" not in recovery.matched_units
    assert recovery.z_overrides == {}
    assert any(issue.issue_type == "z_story_count_mismatch" for issue in recovery.issues)


@pytest.mark.unit
def test_pit_section_rejected_by_zero_anchor():
    """基坑围护剖面：标高数量凑巧兼容但整体错位 → 零锚校验拒绝 + issue。

    2 层楼（F1 底 ±0.000），剖面标高 (-30, -25, -20)——数量 3 落在窗口
    [2, 4] 内，旧口径会误采写坏整楼标高；零锚要求 F1 配对标高 ≈0。
    """
    plan_drawings, normalization = _two_story_plan()
    section = _drawing("sec1", "围护体剖面图", "A-901")
    recovery = recover_section_z(
        [*plan_drawings, section], {"sec1": _marks(-30.0, -25.0, -20.0)}, normalization
    )
    assert "main" not in recovery.matched_units
    assert recovery.z_overrides == {}
    assert any(issue.issue_type == "z_anchor_mismatch" for issue in recovery.issues)


@pytest.mark.unit
def test_compatible_section_wins_over_mark_richest():
    """多候选：标高最多的一张（25 个，基坑剖面）超窗口，兼容的楼层剖面胜出。

    旧口径「盲选标高最多再判」会让整单体错失兼容剖面；新口径把选择延迟到
    数量窗口 + 零锚校验之后。
    """
    plan_drawings, normalization = _two_story_plan()
    pit = _drawing("pit", "基坑围护剖面", "A-902")
    real = _drawing("real", "1-1剖面图", "A-501")
    section_levels = {
        "pit": _marks(*[-32.0 + i * 1.5 for i in range(25)]),  # 25 个施工标高
        "real": _marks(0.0, 3.6, 7.2),
    }
    recovery = recover_section_z(
        [*plan_drawings, pit, real], section_levels, normalization
    )
    assert "main" in recovery.matched_units
    assert recovery.z_overrides[("main", "F1")]["elevation_bottom_m"] == pytest.approx(0.0)
    assert recovery.z_overrides[("main", "F1")]["height_m"] == pytest.approx(3.6)


@pytest.mark.unit
def test_empty_section_marks_ignored():
    plan_drawings, normalization = _two_story_plan()
    section = _drawing("sec1", "剖面图", "A-501")
    recovery = recover_section_z(
        [*plan_drawings, section],
        {"sec1": SectionLevels(marks=(), reason="no_elevation_text")},
        normalization,
    )
    assert recovery.matched_units == set()
    assert recovery.z_overrides == {}
