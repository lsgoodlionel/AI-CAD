"""轴线几何单测(斜向轴线 / 交叉点 / 过点生成 / 平移)。"""
import math

from services.axis_geometry import (
    axis_offset, intersect, line_angle_deg, line_through_point, move_to,
    orientation, translate,
)


def _ln(x1, y1, x2, y2):
    return {"x1_norm": x1, "y1_norm": y1, "x2_norm": x2, "y2_norm": y2}


# ── 角度与朝向 ──────────────────────────────────────────────────

def test_line_angle_deg_basic_directions():
    assert line_angle_deg(_ln(0.5, 0, 0.5, 1)) == 90.0        # 竖线
    assert line_angle_deg(_ln(0, 0.5, 1, 0.5)) == 0.0         # 横线
    assert abs(line_angle_deg(_ln(0, 0, 1, 1)) - 45.0) < 1e-6


def test_line_angle_deg_is_direction_agnostic():
    """同一条线,两端点顺序反过来角度应一致(轴线无方向性)。"""
    assert line_angle_deg(_ln(0, 0, 1, 1)) == line_angle_deg(_ln(1, 1, 0, 0))


def test_line_angle_deg_handles_degenerate_point():
    assert line_angle_deg(_ln(0.5, 0.5, 0.5, 0.5)) == 0.0


def test_orientation_three_way_including_skew():
    assert orientation(_ln(0.5, 0, 0.5, 1)) == "x"
    assert orientation(_ln(0, 0.5, 1, 0.5)) == "y"
    # 斜向轴线是合法轴线,不该被判为无效
    assert orientation(_ln(0, 0, 1, 1)) == "skew"
    assert orientation(_ln(0, 0, 1, 0.5)) == "skew"


def test_orientation_tolerates_small_drawing_jitter():
    """人手描/OCR 都有抖动,1° 内仍算轴对齐。"""
    assert orientation(_ln(0.5, 0.0, 0.51, 1.0)) == "x"
    assert orientation(_ln(0.0, 0.5, 1.0, 0.51)) == "y"


# ── 过点生成轴线(选点定轴)──────────────────────────────────────

def test_line_through_point_generates_vertical_and_horizontal():
    v = line_through_point(0.4, 0.6, 90)
    assert orientation(v) == "x"
    assert abs((v["x1_norm"] + v["x2_norm"]) / 2 - 0.4) < 1e-6

    h = line_through_point(0.4, 0.6, 0)
    assert orientation(h) == "y"
    assert abs((h["y1_norm"] + h["y2_norm"]) / 2 - 0.6) < 1e-6


def test_line_through_point_spans_whole_page():
    """生成的线要贯穿整图,否则交点可能落在线段之外。"""
    v = line_through_point(0.5, 0.5, 90)
    assert v["y1_norm"] < 0 and v["y2_norm"] > 1


def test_line_through_point_supports_skew():
    s = line_through_point(0.3, 0.3, 30)
    # 端点存库前四舍五入到 6 位,角度精度随之在 1e-4 量级(远优于图纸精度)
    assert abs(line_angle_deg(s) - 30.0) < 1e-4


def test_generated_pair_intersects_exactly_at_the_picked_point():
    """选点定轴的核心保证:生成的竖/横线交点就是所选的点。"""
    x, y = 0.37, 0.62
    got = intersect(line_through_point(x, y, 90), line_through_point(x, y, 0))
    assert got is not None
    assert abs(got[0] - x) < 1e-6 and abs(got[1] - y) < 1e-6


# ── 交点 ────────────────────────────────────────────────────────

def test_intersect_of_vertical_and_horizontal():
    got = intersect(_ln(0.3, 0, 0.3, 1), _ln(0, 0.7, 1, 0.7))
    assert got == (0.3, 0.7)


def test_intersect_extends_beyond_segment_ends():
    """轴线按无限长直线求交——两条短线段也能定出交点。"""
    got = intersect(_ln(0.3, 0.0, 0.3, 0.1), _ln(0.0, 0.7, 0.1, 0.7))
    assert got == (0.3, 0.7)


def test_intersect_returns_none_for_parallel_lines():
    assert intersect(_ln(0.2, 0, 0.2, 1), _ln(0.6, 0, 0.6, 1)) is None
    assert intersect(_ln(0, 0, 1, 1), _ln(0.1, 0, 1.1, 1)) is None


def test_intersect_works_for_skew_pair():
    got = intersect(_ln(0, 0, 1, 1), _ln(0, 1, 1, 0))
    assert got is not None
    assert abs(got[0] - 0.5) < 1e-6 and abs(got[1] - 0.5) < 1e-6


# ── 平移(拖动微调)───────────────────────────────────────────────

def test_translate_shifts_both_endpoints_without_mutating():
    src = _ln(0.2, 0.3, 0.2, 0.9)
    got = translate(src, 0.1, -0.05)
    assert got["x1_norm"] == 0.3 and got["y1_norm"] == 0.25
    assert src["x1_norm"] == 0.2            # 原对象不变


def test_translate_keeps_angle():
    src = _ln(0, 0, 1, 1)
    assert line_angle_deg(translate(src, 0.3, -0.2)) == line_angle_deg(src)


def test_move_to_places_line_through_target_point():
    """拖动用「拖到哪」而非「拖了多远」,避免累积误差。"""
    src = _ln(0.2, 0.0, 0.2, 1.0)
    got = move_to(src, 0.55, 0.5)
    assert abs((got["x1_norm"] + got["x2_norm"]) / 2 - 0.55) < 1e-6
    assert line_angle_deg(got) == line_angle_deg(src)


def test_move_to_preserves_skew_angle():
    src = _ln(0.0, 0.0, 0.4, 0.4)
    got = move_to(src, 0.8, 0.1)
    assert abs(line_angle_deg(got) - 45.0) < 1e-6


# ── 法线式偏移(同向轴线排序,斜向也适用)────────────────────────

def test_axis_offset_degenerates_to_coordinate_for_aligned_axes():
    """偏移带符号(法向为 (-sinθ,cosθ)),仅在同方向内可比。"""
    assert abs(abs(axis_offset(_ln(0.3, 0, 0.3, 1))) - 0.3) < 1e-6   # 竖线 → |x|
    assert abs(axis_offset(_ln(0, 0.7, 1, 0.7)) - 0.7) < 1e-6        # 横线 → y


def test_axis_offset_normal_is_truly_perpendicular():
    """曾用 (sinθ,cosθ) 当法向——与方向点积是 sin2θ,斜向完全失效。
    真法向下,同一条斜线上不同点的偏移必须相同。"""
    a = _ln(0.0, 0.0, 1.0, 1.0)          # 45° 线
    b = _ln(0.5, 0.5, 1.5, 1.5)          # 同一条线上的另一段
    assert abs(axis_offset(a) - axis_offset(b)) < 1e-6


def test_axis_offset_orders_parallel_skew_axes():
    a = _ln(0.0, 0.0, 1.0, 1.0)
    b = _ln(0.2, 0.0, 1.2, 1.0)      # 与 a 平行、右移
    assert axis_offset(a) != axis_offset(b)
    assert abs(abs(axis_offset(a) - axis_offset(b)) - 0.2 * math.cos(math.pi / 4)) < 1e-3
