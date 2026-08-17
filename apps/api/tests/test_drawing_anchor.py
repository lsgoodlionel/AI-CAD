"""交叉点定位单测(图↔图对齐 / 图↔工程坐标系)。"""
import math

import pytest

from services.drawing_anchor import (
    MIN_PAIRS, RESIDUAL_WARN_M, align_drawings, apply_similarity,
    match_intersections, similarity_from_pairs, solve_world_transform,
)


def _pt(label_x, label_y, x, y, **world):
    return {"label_x": label_x, "label_y": label_y, "x_norm": x, "y_norm": y, **world}


# ── 相似变换求解 ────────────────────────────────────────────────

def test_similarity_recovers_pure_translation():
    src = [(0.0, 0.0), (1.0, 0.0)]
    dst = [(5.0, 3.0), (6.0, 3.0)]
    t = similarity_from_pairs(src, dst)
    assert abs(t["scale"] - 1.0) < 1e-9
    assert abs(t["rotation_deg"]) < 1e-6 or abs(t["rotation_deg"] - 360) < 1e-6
    assert abs(t["tx"] - 5.0) < 1e-9 and abs(t["ty"] - 3.0) < 1e-9


def test_similarity_recovers_scale_and_rotation():
    """图纸是等比绘制的:缩放 + 旋转 + 平移足以描述图↔世界的关系。"""
    src = [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)]
    s, deg = 2.5, 90.0
    rad = math.radians(deg)
    dst = [(s * (math.cos(rad) * x - math.sin(rad) * y) + 10,
            s * (math.sin(rad) * x + math.cos(rad) * y) - 4) for x, y in src]
    t = similarity_from_pairs(src, dst)
    assert abs(t["scale"] - s) < 1e-9
    assert abs(t["rotation_deg"] - deg) < 1e-6
    assert t["rmse"] < 1e-9


def test_similarity_two_points_are_enough():
    """需求明确:一张图上有至少两个交叉点即可定位。"""
    assert MIN_PAIRS == 2
    t = similarity_from_pairs([(0, 0), (2, 0)], [(1, 1), (5, 1)])
    assert t is not None and abs(t["scale"] - 2.0) < 1e-9


def test_similarity_rejects_too_few_or_degenerate_points():
    assert similarity_from_pairs([(0, 0)], [(1, 1)]) is None
    # 两个源点重合 → 定不出方向
    assert similarity_from_pairs([(1, 1), (1, 1)], [(0, 0), (5, 5)]) is None


def test_similarity_reports_rmse_for_inconsistent_pairs():
    """点配错时残差要暴露出来,而不是硬解出一个看似合理的变换。"""
    src = [(0, 0), (1, 0), (2, 0)]
    dst = [(0, 0), (1, 0), (2, 1)]          # 第三点偏了
    t = similarity_from_pairs(src, dst)
    assert t["rmse"] > 0.1


def test_apply_similarity_round_trips_through_solved_transform():
    src = [(0.1, 0.2), (0.8, 0.5)]
    dst = [(3.0, 4.0), (10.0, 7.0)]
    t = similarity_from_pairs(src, dst)
    for p, q in zip(src, dst):
        got = apply_similarity(p, t)
        assert abs(got[0] - q[0]) < 1e-6 and abs(got[1] - q[1]) < 1e-6


# ── 交叉点按轴号配对 ────────────────────────────────────────────

def test_match_intersections_pairs_by_axis_labels():
    a = [_pt("1", "A", 0.1, 0.2), _pt("5", "C", 0.8, 0.7), _pt("9", "Z", 0.5, 0.5)]
    b = [_pt("5", "C", 0.3, 0.4), _pt("1", "A", 0.0, 0.1)]
    src, dst = match_intersections(a, b)
    assert len(src) == 2                    # 9-Z 在 b 里没有,不参与
    assert src[0] == (0.1, 0.2) and dst[0] == (0.0, 0.1)


def test_match_intersections_ignores_partial_label_overlap():
    """只有一个轴号相同不算同一个交叉点。"""
    a = [_pt("1", "A", 0.1, 0.2)]
    b = [_pt("1", "B", 0.1, 0.2)]
    src, _dst = match_intersections(a, b)
    assert src == []


def test_align_drawings_end_to_end():
    a = [_pt("1", "A", 0.0, 0.0), _pt("5", "C", 1.0, 0.0)]
    b = [_pt("1", "A", 0.2, 0.3), _pt("5", "C", 0.7, 0.3)]
    t = align_drawings(a, b)
    assert abs(t["scale"] - 0.5) < 1e-9
    assert abs(t["tx"] - 0.2) < 1e-9


def test_align_drawings_returns_none_without_enough_shared_points():
    assert align_drawings([_pt("1", "A", 0, 0)], [_pt("1", "A", 1, 1)]) is None


# ── 图 → 工程坐标系 ─────────────────────────────────────────────

def test_solve_world_transform_from_two_coordinated_points():
    pts = [
        _pt("1", "A", 0.0, 0.0, world_x=0.0, world_y=0.0, world_z=0.0),
        _pt("5", "A", 1.0, 0.0, world_x=40.0, world_y=0.0, world_z=0.0),
    ]
    t = solve_world_transform(pts)
    assert abs(t["scale"] - 40.0) < 1e-9
    assert t["z"] == 0.0
    assert t["suspect"] is False


def test_solve_world_transform_ignores_points_without_coordinates():
    pts = [
        _pt("1", "A", 0.0, 0.0, world_x=0.0, world_y=0.0),
        _pt("5", "A", 1.0, 0.0, world_x=40.0, world_y=0.0),
        _pt("9", "A", 2.0, 0.0),                     # 没填坐标,跳过
    ]
    t = solve_world_transform(pts)
    assert t["pairs"] == 2


def test_solve_world_transform_needs_two_coordinated_points():
    assert solve_world_transform(
        [_pt("1", "A", 0.0, 0.0, world_x=0.0, world_y=0.0)]) is None
    assert solve_world_transform([]) is None


def test_solve_world_transform_flags_suspect_when_residual_large():
    """残差大 = 点配错或轴号重名,必须标出来而不是当好结果用。"""
    pts = [
        _pt("1", "A", 0.0, 0.0, world_x=0.0, world_y=0.0),
        _pt("5", "A", 1.0, 0.0, world_x=40.0, world_y=0.0),
        _pt("9", "A", 2.0, 0.0, world_x=60.0, world_y=30.0),   # 明显不共线
    ]
    t = solve_world_transform(pts)
    assert t["rmse_m"] > RESIDUAL_WARN_M
    assert t["suspect"] is True


def test_solve_world_transform_averages_z():
    pts = [
        _pt("1", "A", 0.0, 0.0, world_x=0.0, world_y=0.0, world_z=3.0),
        _pt("5", "A", 1.0, 0.0, world_x=40.0, world_y=0.0, world_z=5.0),
    ]
    assert solve_world_transform(pts)["z"] == 4.0


# ── 反射(工程坐标 X=北 / Y=东 是左手系)──────────────────────────────

def _reflected_pairs(scale=0.14, rot_deg=70.0, tx=-6100.0, ty=-100.0):
    """构造一组**需要反射**的对应点:先镜像 y,再旋转缩放平移。"""
    import math
    rad = math.radians(rot_deg)
    src, dst = [], []
    for i in range(6):
        x, y = 100.0 + i * 90.0, 200.0 + (i % 3) * 140.0
        mx, my = x, -y                                  # 反射
        src.append((x, y))
        dst.append((scale * (math.cos(rad) * mx - math.sin(rad) * my) + tx,
                    scale * (math.sin(rad) * mx + math.cos(rad) * my) + ty))
    return src, dst


def test_similarity_handles_a_reflected_mapping():
    """**中国测量坐标 X=北 / Y=东 相对于数学系 (东,北) 是左手系**,
    图纸米坐标(y 向上)→ 工程坐标因此需要一次反射。

    不支持反射时实测残差 105m —— 图直接被判 suspect 跳过,永远摆不上。
    """
    src, dst = _reflected_pairs()
    got = similarity_from_pairs(src, dst)
    assert got is not None
    assert got["rmse"] < 1e-6
    assert got["reflect"] is True


def test_non_reflected_mapping_still_reports_no_reflection():
    """普通(不需反射的)数据不能被误判成反射 —— 否则会整体镜像。"""
    import math
    rad = math.radians(30.0)
    src = [(0.0, 0.0), (100.0, 0.0), (0.0, 100.0), (60.0, 80.0)]
    dst = [(2 * (math.cos(rad) * x - math.sin(rad) * y) + 5.0,
            2 * (math.sin(rad) * x + math.cos(rad) * y) - 3.0) for x, y in src]
    got = similarity_from_pairs(src, dst)
    assert got["reflect"] is False
    assert got["rmse"] < 1e-6


def test_apply_similarity_honours_reflection():
    src, dst = _reflected_pairs()
    t = similarity_from_pairs(src, dst)
    for p, q in zip(src, dst):
        got = apply_similarity(p, t)
        assert got[0] == pytest.approx(q[0], abs=1e-6)
        assert got[1] == pytest.approx(q[1], abs=1e-6)


def test_transform_without_reflect_key_is_treated_as_unreflected():
    """老数据/老调用没有 reflect 字段时按不反射处理,保持向后兼容。"""
    legacy = {"scale": 2.0, "rotation_deg": 0.0, "tx": 1.0, "ty": 2.0}
    assert apply_similarity((3.0, 4.0), legacy) == pytest.approx((7.0, 10.0))
