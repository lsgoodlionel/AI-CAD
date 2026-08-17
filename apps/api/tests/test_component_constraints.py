"""Phase H3 收敛精修单测 —— Z 恢复 / 落轴网 / 跨层连续 / 数量对齐。纯函数。"""
from services.component_constraints import (
    apply_floor_z,
    check_vertical_continuity,
    reconcile_with_bom,
    snap_to_grid,
)


# ── apply_floor_z ──────────────────────────────────────────────

def test_apply_z_only_from_real_source():
    insts = [{"type": "column"}, {"type": "wall"}]
    apply_floor_z(insts, -5.9, -1.4, "section")
    assert all(i["z_bottom_m"] == -5.9 and i["z_top_m"] == -1.4 and i["z_source"] == "section" for i in insts)


def test_apply_z_default_source_covers_value_but_marks_unreal():
    """默认套层高:**仍盖 Z**(否则整栋无竖向位置、模型塌到 0),但如实标 story_default,
    不计入竖向真实率。诚实原则:宁可低报真实率,不让默认套冒充真实标高。"""
    from services.component_constraints import is_real_z_source
    insts = [{"type": "column"}]
    apply_floor_z(insts, 3.0, 6.0, "story_default")
    assert insts[0]["z_bottom_m"] == 3.0            # 盖值:模型有竖向
    assert insts[0]["z_source"] == "story_default"  # 但来源如实
    assert is_real_z_source("story_default") is False
    assert is_real_z_source("section") is True
    assert is_real_z_source("manual") is True


def test_apply_z_none_elevation_leaves_null():
    insts = [{"type": "column"}]
    apply_floor_z(insts, None, None, "section")
    assert insts[0].get("z_bottom_m") is None


# ── snap_to_grid ───────────────────────────────────────────────

_AXES = {"x": [{"label": "C", "coord": 12.0}], "y": [{"label": "3", "coord": 10.0}]}


def test_snap_column_centroid_to_grid_intersection():
    # 质心 (11.5, 9.5) → 吸附到 (12,10),平移 (+0.5,+0.5)
    insts = [{"type": "column", "grid_ref": "C-3", "outline_m": [[11, 9], [12, 10]]}]
    snap_to_grid(insts, _AXES)
    assert insts[0]["snapped"] is True
    # 平移后质心应落在交点
    pts = insts[0]["outline_m"]
    cx = sum(p[0] for p in pts) / len(pts)
    cy = sum(p[1] for p in pts) / len(pts)
    assert (round(cx, 3), round(cy, 3)) == (12.0, 10.0)


def test_snap_ignores_non_point_types_and_missing_axis():
    insts = [
        {"type": "wall", "grid_ref": "C-3", "outline_m": [[0, 0], [1, 1]]},   # 非点状 → 不动
        {"type": "column", "grid_ref": "Z-9", "outline_m": [[0, 0], [1, 1]]},  # 轴号不在 axes → 不动
    ]
    snap_to_grid(insts, _AXES)
    assert all("snapped" not in i for i in insts)


def test_snap_no_axes_is_noop():
    insts = [{"type": "column", "grid_ref": "C-3", "outline_m": [[0, 0]]}]
    snap_to_grid(insts, None)
    assert "snapped" not in insts[0]


# ── check_vertical_continuity ──────────────────────────────────

def test_continuity_reports_missing_middle_floor():
    """柱 C-3 在 1、3 层有,2 层缺 → 报缺口。"""
    by_floor = [
        (1, [{"type": "column", "grid_ref": "C-3"}]),
        (2, [{"type": "column", "grid_ref": "A-1"}]),
        (3, [{"type": "column", "grid_ref": "C-3"}]),
    ]
    gaps = check_vertical_continuity(by_floor)
    c3 = [g for g in gaps if g["grid_ref"] == "C-3"]
    assert len(c3) == 1
    assert c3[0]["missing_orders"] == [2]


def test_continuity_no_gap_when_contiguous():
    by_floor = [
        (1, [{"type": "column", "grid_ref": "C-3"}]),
        (2, [{"type": "column", "grid_ref": "C-3"}]),
    ]
    assert check_vertical_continuity(by_floor) == []


# ── reconcile_with_bom ─────────────────────────────────────────

def test_reconcile_reports_diff_vs_bom():
    insts = [{"type": "column"}] * 45 + [{"type": "wall"}] * 10
    bom = {"column": 48, "wall": 10, "beam": 20}
    report = reconcile_with_bom(insts, bom)
    assert report["column"] == {"expected": 48, "actual": 45, "diff": 3}   # 漏 3
    assert report["wall"] == {"expected": 10, "actual": 10, "diff": 0}
    assert report["beam"] == {"expected": 20, "actual": 0, "diff": 20}


def test_reconcile_negative_diff_when_over_detected():
    insts = [{"type": "pile"}] * 55
    report = reconcile_with_bom(insts, {"pile": 48})
    assert report["pile"]["diff"] == -7   # 多识别 7(可能重复/误检)
