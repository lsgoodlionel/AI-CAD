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


def _three_story_plan():
    drawings = [
        _drawing("p1", "一层平面图", "A-101"),
        _drawing("p2", "二层平面图", "A-201"),
        _drawing("p3", "三层平面图", "A-301"),
    ]
    return drawings, normalize_story_table(drawings)


def _n_story_plan(n: int):
    drawings = [
        _drawing(f"p{i}", f"{i}层平面图", f"A-{100 * i + 1}") for i in range(1, n + 1)
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
def test_retaining_section_excluded_but_building_section_recovers():
    """围护/基坑剖面（标高非楼层）被前置剔除，不污染候选；同项目建筑剖面正常点亮。"""
    plan_drawings, normalization = _two_story_plan()
    retaining = _drawing("r1", "20舞台深坑围护体剖面图（一）", "W-501")
    building = _drawing("sec1", "1-1剖面图", "A-501")
    section_levels = {
        # 围护标高（基坑/挡土），若未剔除会挤占配准窗口 / 冒充楼层
        "r1": _marks(-24.8, -9.3, -5.5, 5.5, 16.2),
        # 真建筑楼层标高
        "sec1": _marks(0.0, 3.6, 7.2),
    }
    recovery = recover_section_z(
        [*plan_drawings, retaining, building], section_levels, normalization
    )
    assert "main" in recovery.matched_units
    assert recovery.z_overrides[("main", "F1")]["height_m"] == pytest.approx(3.6)


@pytest.mark.unit
def test_only_retaining_sections_yields_no_match():
    """仅有围护/基坑剖面（无建筑剖面）→ 不点亮 matched，回落估算（绝不虚高）。"""
    plan_drawings, normalization = _two_story_plan()
    retaining = _drawing("r1", "舞台深坑围护体剖面图", "W-501")
    recovery = recover_section_z(
        [*plan_drawings, retaining], {"r1": _marks(-24.8, -9.3, 5.5)}, normalization
    )
    assert recovery.matched_units == set()


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
    # 标题中性（不含围护/基坑关键词，不会被 _NON_FLOOR_SECTION_RE 前置剔除），
    # 但标高整体错位 → 到达零锚校验并被其拒绝，验证锚点兜底仍有效。
    section = _drawing("sec1", "A-A剖面图", "A-901")
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


# ── 阶段A：最近邻配准（放宽标高数≈楼层数强绑定）──────────────────────────


@pytest.mark.unit
def test_nonuniform_real_heights_recovered_beyond_old_surplus_tolerance():
    """真实非均匀层高（大堂 5.4m + 上部标准层 3.6m）+ 2 个额外标高（旧口径

    surplus 容差仅 +2，会直接拒绝）——阶段A 用滑窗最近邻配准挑出与楼层数
    等长、零锚校验通过、间距最均匀的连续子序列，不再受数量强绑定限制。
    """
    plan_drawings, normalization = _three_story_plan()
    section = _drawing("sec1", "1-1剖面图", "A-501")
    # F1 底 0.0（大堂层高 5.4）、F2 底 5.4（层高 3.6）、F3 底 9.0（层高 3.6）、
    # 屋面 12.6，外加机电夹层 16.0——标高数 6，楼层数 3，surplus=+3 超旧容差(+2)。
    section_levels = {"sec1": _marks(0.0, 5.4, 9.0, 12.6, 16.0, 19.0)}

    recovery = recover_section_z(
        [*plan_drawings, section], section_levels, normalization
    )

    assert "main" in recovery.matched_units
    assert recovery.z_overrides[("main", "F1")]["height_m"] == pytest.approx(5.4)
    assert recovery.z_overrides[("main", "F1")]["elevation_bottom_m"] == pytest.approx(0.0)
    assert recovery.z_overrides[("main", "F2")]["height_m"] == pytest.approx(3.6)
    assert recovery.z_overrides[("main", "F2")]["elevation_bottom_m"] == pytest.approx(5.4)
    assert recovery.z_overrides[("main", "F3")]["height_m"] == pytest.approx(3.6)
    assert recovery.z_overrides[("main", "F3")]["elevation_bottom_m"] == pytest.approx(9.0)
    # 层高非均匀（5.4 与 3.6 并存）——验证不再一律回落默认 4.5m 均匀层高
    heights = {
        recovery.z_overrides[("main", key)]["height_m"] for key in ("F1", "F2", "F3")
    }
    assert heights == {5.4, 3.6}


@pytest.mark.unit
def test_partial_coverage_at_threshold_boundary_matches():
    """10 层楼、剖面仅给出底部 7 层标高（覆盖率恰为 70%）——达门槛，判定 matched。

    未覆盖的 F8~F10 留给 `_resolve_story_height` 默认兜底（estimated=True），
    不影响已覆盖楼层的实测层高写入。
    """
    plan_drawings, normalization = _n_story_plan(10)
    section = _drawing("sec1", "1-1剖面图", "A-501")
    marks = [round(i * 3.6, 3) for i in range(7)]  # F1~F7 底标高，层高均 3.6
    recovery = recover_section_z(
        [*plan_drawings, section], {"sec1": _marks(*marks)}, normalization
    )

    assert "main" in recovery.matched_units
    for order in range(1, 8):
        key = f"F{order}"
        assert recovery.z_overrides[("main", key)]["height_m"] == pytest.approx(3.6)
    # 未覆盖楼层不写 override（回落默认层高，绝不虚高）
    assert ("main", "F8") not in recovery.z_overrides
    assert ("main", "F9") not in recovery.z_overrides
    assert ("main", "F10") not in recovery.z_overrides


@pytest.mark.unit
def test_coverage_below_threshold_falls_back_to_default():
    """10 层楼、剖面仅给出 6 层标高（覆盖率 60% < 70% 门槛）——不判定 matched。"""
    plan_drawings, normalization = _n_story_plan(10)
    section = _drawing("sec1", "1-1剖面图", "A-501")
    marks = [round(i * 3.6, 3) for i in range(6)]  # 覆盖率 6/10 = 0.6
    recovery = recover_section_z(
        [*plan_drawings, section], {"sec1": _marks(*marks)}, normalization
    )

    assert "main" not in recovery.matched_units
    assert recovery.z_overrides == {}
    issue = next(
        issue for issue in recovery.issues if issue.issue_type == "z_story_count_mismatch"
    )
    assert issue.payload["best_coverage"] == pytest.approx(0.6)
    assert issue.payload["min_coverage_required"] == pytest.approx(0.7)


@pytest.mark.unit
def test_noisy_close_marks_filtered_before_matching():
    """剖面标高含女儿墙噪声（12.6 屋面 + 13.0 女儿墙，间距 0.4m < 2.8m 噪声阈）：

    过滤后不影响主楼面序列匹配（对齐 `filter_main_sequence` 同口径）。
    """
    plan_drawings, normalization = _two_story_plan()
    section = _drawing("sec1", "1-1剖面图", "A-501")
    section_levels = {"sec1": _marks(0.0, 3.6, 7.2, 7.6)}  # 7.6 为女儿墙噪声

    recovery = recover_section_z(
        [*plan_drawings, section], section_levels, normalization
    )

    assert "main" in recovery.matched_units
    assert recovery.z_overrides[("main", "F1")]["height_m"] == pytest.approx(3.6)
    assert recovery.z_overrides[("main", "F2")]["height_m"] == pytest.approx(3.6)


@pytest.mark.unit
def test_no_zero_anchor_falls_back_to_spacing_consistency_gate():
    """楼层表无 ±0.000 层（纯地下单体）：无锚可校，仅靠覆盖率+间距一致性把关。

    2 层地下楼层表（B1/B2，无 ±0.000），剖面标高间距均匀 → 应判定 matched；
    是对「无锚放行」路径的显式覆盖（原测试套件对此路径无覆盖）。
    """
    drawings = [
        _drawing("p1", "地下一层平面图", "A-B101"),
        _drawing("p2", "地下二层平面图", "A-B201"),
    ]
    normalization = normalize_story_table(drawings)
    section = _drawing("sec1", "1-1剖面图", "A-501")
    # B2 底 -8.4、B1 底 -4.2（升序），层高均 4.2
    recovery = recover_section_z(
        [*drawings, section], {"sec1": _marks(-8.4, -4.2)}, normalization
    )
    assert "main" in recovery.matched_units
