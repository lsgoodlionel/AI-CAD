"""按工程坐标摆放构件单测。"""
import pytest

from services.drawing_transform import DrawingTransform
from services.model_world_placement import (
    intersections_to_meter, place_element, place_elements, place_point,
    placements_for_project, solve_placement,
)

#: 页高 1000pt、比例 0.05 米/点、原点在 (0,0) —— 便于手算校验
TF = DrawingTransform(scale_m_pt=0.05, origin_x=0.0, origin_y=0.0,
                      page_h=1000.0, confidence=1.0)


def _pt(label_x, label_y, xn, yn, **world):
    return {"label_x": label_x, "label_y": label_y,
            "x_norm": xn, "y_norm": yn, **world}


# ── 坐标空间对齐(最容易错的一步)────────────────────────────────

def test_intersections_to_meter_matches_element_coordinate_space():
    """交叉点存的是归一化页面坐标,构件是本图米坐标——解算前必须换算到同一空间。"""
    pts = [_pt("1", "A", 0.2, 0.4, world_x=0.0, world_y=0.0)]
    got = intersections_to_meter(pts, TF)
    # x: 0.2×1000pt ×0.05 = 10m;y: (1000 - 0.4×1000)×0.05 = 30m(y 翻转)
    assert got[0]["x_norm"] == 10.0
    assert got[0]["y_norm"] == 30.0


def test_intersections_to_meter_skips_points_without_world_coords():
    pts = [_pt("1", "A", 0.2, 0.4), _pt("2", "A", 0.3, 0.4, world_x=1, world_y=2)]
    assert len(intersections_to_meter(pts, TF)) == 1


def test_intersections_to_meter_needs_page_height():
    bad = DrawingTransform(scale_m_pt=0.05, origin_x=0, origin_y=0,
                           page_h=0, confidence=1.0)
    assert intersections_to_meter(
        [_pt("1", "A", 0.2, 0.4, world_x=0, world_y=0)], bad) == []


# ── 求解与应用 ──────────────────────────────────────────────────

def _two_points():
    """本图米坐标 (10,30) 与 (30,30) 对应工程坐标 (100,200) 与 (120,200):
    纯平移 +90/+170,尺度 1。"""
    return [
        _pt("1", "A", 0.2, 0.4, world_x=100.0, world_y=200.0, world_z=0.0),
        _pt("5", "A", 0.6, 0.4, world_x=120.0, world_y=200.0, world_z=0.0),
    ]


def test_solve_placement_recovers_pure_translation():
    p = solve_placement(_two_points(), TF)
    assert abs(p["scale"] - 1.0) < 1e-9
    assert abs(p["tx"] - 90.0) < 1e-6 and abs(p["ty"] - 170.0) < 1e-6
    assert p["suspect"] is False


def test_solve_placement_returns_none_without_two_points():
    one = [_pt("1", "A", 0.2, 0.4, world_x=0.0, world_y=0.0)]
    assert solve_placement(one, TF) is None


def test_place_point_moves_into_world_frame():
    p = solve_placement(_two_points(), TF)
    assert place_point(10.0, 30.0, p) == pytest.approx((100.0, 200.0), abs=1e-6)


def test_place_element_maps_all_geometry_keys():
    p = solve_placement(_two_points(), TF)
    el = {"outline": [[10.0, 30.0], [30.0, 30.0]], "kind": "column"}
    got = place_element(el, p)
    assert got["outline"][0] == pytest.approx([100.0, 200.0], abs=1e-3)
    assert got["placed"] is True
    assert el["outline"][0] == [10.0, 30.0]        # 原对象不变


def test_place_element_keeps_extra_point_dimensions():
    """点可能带第三维(标高),搬运时不能丢。"""
    p = solve_placement(_two_points(), TF)
    got = place_element({"path": [[10.0, 30.0, 4.2]]}, p)
    assert got["path"][0][2] == 4.2


def test_place_element_ignores_non_geometry_fields():
    p = solve_placement(_two_points(), TF)
    got = place_element({"outline": [], "src": "d1", "shape": "circle"}, p)
    assert got["src"] == "d1" and got["shape"] == "circle"


def test_place_elements_returns_input_unchanged_without_placement():
    """没解出变换就**原样返回**——不猜一个位置,错位比不摆更糟。"""
    elements = {"columns": [{"outline": [[1.0, 2.0]]}]}
    assert place_elements(elements, None) is elements


# ── 全项目摆放变换 ──────────────────────────────────────────────

class _FakeDb:
    def __init__(self, rows):
        self.rows = rows

    async def fetch_all(self, sql, params):
        return self.rows


def _row(drawing_id, label_x, xn, **world):
    return {"drawing_id": drawing_id, "id": f"i-{drawing_id}-{label_x}",
            "label_x": label_x, "label_y": "A", "x_norm": xn, "y_norm": 0.4,
            "world_z": None, **world}


@pytest.mark.asyncio
async def test_placements_only_for_drawings_with_transform_and_points():
    db = _FakeDb([
        _row("d1", "1", 0.2, world_x=100.0, world_y=200.0),
        _row("d1", "5", 0.6, world_x=120.0, world_y=200.0),
        _row("d2", "1", 0.2, world_x=0.0, world_y=0.0),        # 只有一个点
        _row("d3", "1", 0.2, world_x=0.0, world_y=0.0),        # 没有 transform
        _row("d3", "5", 0.6, world_x=20.0, world_y=0.0),
    ])
    got = await placements_for_project(db, "p1", {"d1": TF, "d2": TF})
    assert set(got) == {"d1"}


@pytest.mark.asyncio
async def test_placements_skip_suspect_solutions():
    """残差过大 = 交叉点配错或轴号重名,宁可不摆也不摆错位置。"""
    db = _FakeDb([
        _row("d1", "1", 0.2, world_x=0.0, world_y=0.0),
        _row("d1", "5", 0.6, world_x=20.0, world_y=0.0),
        _row("d1", "9", 0.9, world_x=100.0, world_y=90.0),     # 明显不共线
    ])
    assert await placements_for_project(db, "p1", {"d1": TF}) == {}
