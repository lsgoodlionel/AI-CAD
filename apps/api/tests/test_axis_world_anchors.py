"""识别成果 → 世界锚点记录 单测(M-I5 接线)。

坐标标注给出的是「页面点 ↔ 工程坐标」;而 `axis_intersections` 的身份是
**轴号对**(label_x × label_y,见 migration 039)——因为轴号对才是跨图对齐的
天然锚点。所以要把每个锚点落到它所在的两条轴线上。

**为什么必须排除外点**:RANSAC 标出的粗错(实测 1 处:OCR 把 -156.750 读成
-1.000)一旦写进锚点表,整张图会被摆到错误的位置。**错的世界坐标比缺一个锚点
危险得多**,宁可少写。
"""
import pytest

from services.axis_world_anchors import (
    AXIS_MATCH_TOLERANCE_PT, anchor_records, nearest_labelled_axis,
)

PAGE_H = 2384.0


def _axis(angle, offset, label, kind):
    return {"angle_deg": angle, "offset_pt": offset,
            "label": label, "label_kind": kind}


#: 一个最小轴网:竖向 1-1(x=1000)、横向 1-A(y=1600)
AXES = [
    _axis(90.0, -1000.0, "1-1", "numeric"),
    _axis(90.0, -1200.0, "1-2", "numeric"),
    _axis(0.0, 1600.0, "1-A", "alpha"),
    _axis(0.0, 1500.0, "1-B", "alpha"),
]


def _anchor(px, py, wx=-6100.0, wy=-100.0, **kw):
    return {"page": (px, py), "world": (wx, wy), **kw}


# ── 轴线匹配 ──────────────────────────────────────────────────

def test_finds_the_numeric_axis_through_a_point():
    got = nearest_labelled_axis((1000.0, 1600.0), AXES, "numeric")
    assert got["label"] == "1-1"


def test_finds_the_alpha_axis_through_a_point():
    got = nearest_labelled_axis((1000.0, 1600.0), AXES, "alpha")
    assert got["label"] == "1-A"


def test_picks_the_nearer_of_two_parallel_axes():
    assert nearest_labelled_axis((1198.0, 1600.0), AXES, "numeric")["label"] == "1-2"


def test_returns_none_when_no_axis_is_close_enough():
    """点不在任何轴线上就不该硬配 —— 配错轴号等于给了错的锚点身份。"""
    assert nearest_labelled_axis((1500.0, 1600.0), AXES, "numeric") is None


def test_tolerance_is_tight_relative_to_axis_spacing():
    """实测最小轴距约 26pt,容差必须远小于它。"""
    assert AXIS_MATCH_TOLERANCE_PT <= 5.0


def test_nearest_on_empty_axes():
    assert nearest_labelled_axis((0.0, 0.0), [], "numeric") is None


def test_ignores_axes_of_the_other_kind():
    only_alpha = [a for a in AXES if a["label_kind"] == "alpha"]
    assert nearest_labelled_axis((1000.0, 1600.0), only_alpha, "numeric") is None


# ── 锚点记录 ──────────────────────────────────────────────────

def test_builds_an_intersection_record():
    got = anchor_records([_anchor(1000.0, 1600.0)], AXES, page_h=PAGE_H)
    assert len(got) == 1
    r = got[0]
    assert (r["label_x"], r["label_y"]) == ("1-1", "1-A")
    assert r["x_norm"] == pytest.approx(1000.0 / PAGE_H)
    assert r["y_norm"] == pytest.approx(1600.0 / PAGE_H)
    assert (r["world_x"], r["world_y"]) == (-6100.0, -100.0)


def test_normalisation_divides_both_axes_by_page_height():
    """归一化是**同除页高**(见 intersections_to_meter),不是各除各的。"""
    r = anchor_records([_anchor(1000.0, 1600.0)], AXES, page_h=PAGE_H)[0]
    assert r["x_norm"] * PAGE_H == pytest.approx(1000.0)
    assert r["y_norm"] * PAGE_H == pytest.approx(1600.0)


def test_records_provenance_as_automatic():
    """自动锚点必须可与人工标定区分 —— 人审时要知道这条是谁写的。"""
    r = anchor_records([_anchor(1000.0, 1600.0)], AXES, page_h=PAGE_H)[0]
    assert "coord_annotation" in r["note"]


def test_skips_anchors_flagged_as_outliers():
    """RANSAC 判定的粗错绝不能写进锚点 —— 错的世界坐标比缺锚点危险得多。"""
    got = anchor_records([_anchor(1000.0, 1600.0, outlier=True)], AXES,
                         page_h=PAGE_H)
    assert got == []


def test_keeps_anchors_repaired_by_sign_flip():
    """符号被修复过的锚点是**可用**的,只是要记下来源。"""
    got = anchor_records([_anchor(1000.0, 1600.0, repaired="y_sign")], AXES,
                         page_h=PAGE_H)
    assert len(got) == 1 and "y_sign" in got[0]["note"]


def test_skips_anchors_not_on_two_axes():
    """只落在一条轴线上的点没有轴号对身份,不能入表。"""
    got = anchor_records([_anchor(1000.0, 900.0)], AXES, page_h=PAGE_H)
    assert got == []


def test_dedupes_by_label_pair():
    """同一轴号对只留一条 —— 表的唯一键就是 (drawing_id, label_x, label_y)。"""
    got = anchor_records([_anchor(1000.0, 1600.0), _anchor(1000.2, 1600.3)],
                         AXES, page_h=PAGE_H)
    assert len(got) == 1


def test_records_on_empty_input():
    assert anchor_records([], AXES, page_h=PAGE_H) == []


def test_zero_page_height_yields_nothing():
    assert anchor_records([_anchor(1000.0, 1600.0)], AXES, page_h=0.0) == []


def test_does_not_mutate_input():
    anchors = [_anchor(1000.0, 1600.0)]
    before = [dict(a) for a in anchors]
    anchor_records(anchors, AXES, page_h=PAGE_H)
    assert [dict(a) for a in anchors] == before


def test_rotated_axes_are_matched_too():
    """旋转分区的轴线也要能配上 —— 分区 3 的锚点不能因此丢掉。"""
    import math

    angle_n, angle_a = 132.0, 42.0
    rad_n, rad_a = math.radians(angle_n), math.radians(angle_a)
    # 取两条轴线的交点:法向偏移分别为 500 与 -300
    off_n, off_a = 500.0, -300.0
    # 解 [-sin,cos] 的二元一次方程
    a1, b1 = -math.sin(rad_n), math.cos(rad_n)
    a2, b2 = -math.sin(rad_a), math.cos(rad_a)
    det = a1 * b2 - a2 * b1
    px = (off_n * b2 - off_a * b1) / det
    py = (a1 * off_a - a2 * off_n) / det
    axes = [_axis(angle_n, off_n, "3-5", "numeric"),
            _axis(angle_a, off_a, "3-C", "alpha")]
    got = anchor_records([_anchor(px, py)], axes, page_h=PAGE_H)
    assert len(got) == 1
    assert (got[0]["label_x"], got[0]["label_y"]) == ("3-5", "3-C")


# ── 由识别结果构造 drawing_transform ────────────────────────────────

def test_transform_uses_the_measured_scale_not_a_guessed_one():
    """老 `transform_from_geometry` 从图面文字读比例尺,在描边字形图上读不到
    ——实测 A-01-02A 根本没有 drawing_transform,placements 因此直接跳过它。
    """
    from services.axis_world_anchors import transform_from_axes

    got = transform_from_axes(AXES, page_h=PAGE_H, scale_m_pt=0.142757)
    assert got.scale_m_pt == pytest.approx(0.142757)
    assert got.page_h == PAGE_H


def test_transform_origin_is_the_grid_lower_left():
    """原点取数字向最小 x 与字母向最小翻转 y,与 pt_to_meter 同口径。"""
    from services.axis_world_anchors import transform_from_axes

    got = transform_from_axes(AXES, page_h=PAGE_H, scale_m_pt=0.14)
    assert got.origin_x == pytest.approx(1000.0)          # -(-1000)
    assert got.origin_y == pytest.approx(PAGE_H - 1600.0)


def test_transform_needs_both_directions():
    """只有一个方向的轴线定不出原点 —— 返回 None,不落无效变换。"""
    from services.axis_world_anchors import transform_from_axes

    only_numeric = [a for a in AXES if a["label_kind"] == "numeric"]
    assert transform_from_axes(only_numeric, page_h=PAGE_H, scale_m_pt=0.14) is None


def test_transform_rejects_non_positive_scale():
    from services.axis_world_anchors import transform_from_axes

    assert transform_from_axes(AXES, page_h=PAGE_H, scale_m_pt=0.0) is None
    assert transform_from_axes(AXES, page_h=0.0, scale_m_pt=0.14) is None
