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
