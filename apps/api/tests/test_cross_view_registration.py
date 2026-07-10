"""三视图配准与 z 装配测试（B-09）。

以轴网锚点对齐平面/剖面/立面，合成统一标高表 + 一致性评分 + 冲突显式记录。
配准失败须优雅降级到剖面单证据，不整链崩。
"""
import pytest

from core.model3d.elevation_opening_extractor import ElevationOpenings, Opening
from core.model3d.grid_anchor_extractor import GridAxis, GridSystem
from core.model3d.section_level_extractor import LevelMark, SectionLevels
from services.cross_view_registration import (
    ElevationView,
    SectionView,
    ZRegistration,
    register_views,
)


def _grid(x_labels: list[tuple[str, float]]) -> GridSystem:
    axes_x = tuple(GridAxis(label=label, coord=coord) for label, coord in x_labels)
    return GridSystem(axes_x=axes_x, axes_y=(), confidence=1.0, unlabeled=False)


def _section(drawing_id: str, elevations: list[float], x_labels=None) -> SectionView:
    marks = tuple(
        LevelMark(elevation_m=e, label=f"{e:+.3f}", confidence=0.9, source_ref={})
        for e in elevations
    )
    levels = SectionLevels(
        marks=marks,
        reason=None,
        fit={"slope_m_per_pt": -0.02, "residual": 0.0, "tie_point_count": len(elevations)},
    )
    return SectionView(
        drawing_id=drawing_id,
        grid=_grid(x_labels or [("1", 100.0), ("2", 300.0)]),
        levels=levels,
    )


def _elevation(drawing_id: str, head_elevs: list[float], x_labels=None) -> ElevationView:
    openings = tuple(
        Opening(
            kind="window",
            sill_h_m=head - 1.5,
            head_h_m=head,
            width_m=1.2,
            height_m=1.5,
            axis_ref="1-2",
            confidence=0.85,
            evidence={},
        )
        for head in head_elevs
    )
    return ElevationView(
        drawing_id=drawing_id,
        grid=_grid(x_labels or [("1", 120.0), ("2", 320.0)]),
        openings=ElevationOpenings(openings=openings),
    )


# ── 三视图齐备：高一致性 ───────────────────────────────────────

@pytest.mark.unit
def test_full_three_view_registration_high_consistency():
    plan = _grid([("1", 100.0), ("2", 300.0), ("3", 500.0)])
    sections = [_section("sec1", [0.0, 3.0, 6.0])]
    elevations = [_elevation("elev1", [2.4, 5.4])]  # 洞口顶均落在 [0,6] 内

    reg = register_views(plan, sections, elevations)

    assert isinstance(reg, ZRegistration)
    assert reg.consistency_score >= 0.9
    assert reg.matched is True
    assert reg.conflicts == ()
    # 标高表来自剖面
    assert [lvl["elevation_m"] for lvl in reg.levels] == [0.0, 3.0, 6.0]
    assert reg.levels[0]["source"] == "section"


@pytest.mark.unit
def test_axis_map_unifies_labels_in_plan_frame():
    plan = _grid([("1", 100.0), ("2", 300.0), ("3", 500.0)])
    sections = [_section("sec1", [0.0, 3.0], x_labels=[("1", 110.0), ("2", 310.0)])]
    reg = register_views(plan, sections, [])
    # 轴号统一到平面坐标系
    assert reg.axis_map["1"] == pytest.approx(100.0, abs=1.0)
    assert reg.axis_map["3"] == pytest.approx(500.0)


# ── 冲突显式记录 ───────────────────────────────────────────────

@pytest.mark.unit
def test_elevation_opening_out_of_section_range_records_conflict():
    plan = _grid([("1", 100.0), ("2", 300.0)])
    sections = [_section("sec1", [0.0, 3.0, 6.0])]
    elevations = [_elevation("elev1", [20.0])]  # 洞口顶 20m 远超剖面 [0,6]

    reg = register_views(plan, sections, elevations)

    assert reg.conflicts != ()
    assert reg.conflicts[0]["kind"] == "section_elevation_z"
    assert reg.consistency_score < 0.9
    assert reg.matched is False  # 冲突未静默，不虚高一致性


@pytest.mark.unit
def test_two_sections_disagreement_recorded():
    plan = _grid([("1", 100.0)])
    sections = [_section("sec1", [0.0, 3.0, 6.0]), _section("sec2", [0.0, 3.0, 12.0])]
    reg = register_views(plan, sections, [])
    assert any(c["kind"] == "section_section_z" for c in reg.conflicts)


# ── 优雅降级 ────────────────────────────────────────────────────

@pytest.mark.unit
def test_section_only_degrades_to_single_evidence():
    """无立面/平面 → 仍产出剖面标高表，matched=False（非多视图强证据）。"""
    reg = register_views(None, [_section("sec1", [0.0, 3.0, 6.0])], [])
    assert [lvl["elevation_m"] for lvl in reg.levels] == [0.0, 3.0, 6.0]
    assert reg.matched is False
    assert reg.consistency_score == pytest.approx(0.5)  # 单视图未互校


@pytest.mark.unit
def test_no_views_returns_empty_registration():
    reg = register_views(None, [], [])
    assert reg.levels == ()
    assert reg.matched is False
    assert reg.consistency_score == pytest.approx(0.0)


@pytest.mark.unit
def test_empty_section_marks_ignored_gracefully():
    empty_section = SectionView(
        drawing_id="sec1",
        grid=_grid([("1", 100.0)]),
        levels=SectionLevels(marks=(), reason="no_elevation_text"),
    )
    reg = register_views(None, [empty_section], [])
    assert reg.levels == ()
    assert reg.matched is False
