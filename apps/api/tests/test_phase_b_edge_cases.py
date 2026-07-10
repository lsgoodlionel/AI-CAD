"""Phase B z 恢复/拓扑 降级与缺证据路径归集测试（B-21/B-22 补强）。

针对各核心算法的降级、冲突、缺证据、畸形输入分支，确保 100% 关键分支覆盖。
"""
import pytest

from core.model3d import grid_anchor_extractor
from core.model3d.elevation_opening_extractor import extract_elevation_openings
from core.model3d.grid_anchor_extractor import extract_grid_anchors
from core.model3d.section_level_extractor import extract_section_levels
from core.model3d.topology_rules import resolve_opening_host
from core.model3d.types import DrawingGeometry
from services.model_topology import build_topology_graph


# ── section_level_extractor 降级 ──────────────────────────────

@pytest.mark.unit
def test_section_wrong_sign_slope_lowers_confidence():
    """标高随页面 y 同增（斜率非负，异常标定）→ fit_quality 降级。"""
    geom = DrawingGeometry(
        page_w=500, page_h=800,
        lines=[(50, 300, 400, 300), (50, 500, 400, 500), (50, 700, 400, 700)],
        texts=[(410, 300, "±0.000"), (410, 500, "4.200"), (410, 700, "8.400")],
    )
    result = extract_section_levels(geom)
    assert result.fit["slope_m_per_pt"] > 0          # 错向
    assert max(m.confidence for m in result.marks) <= 0.4 * 0.9 + 1e-6


@pytest.mark.unit
def test_section_same_y_pt_conflict_degrades_fit():
    """两标高绑同一 y（图面同位多值冲突）→ var_x=0 分支，斜率 0。"""
    geom = DrawingGeometry(
        page_w=500, page_h=800,
        lines=[(50, 500, 400, 500)],
        texts=[(410, 500, "±0.000"), (411, 500, "4.200")],
    )
    result = extract_section_levels(geom)
    assert result.fit["slope_m_per_pt"] == pytest.approx(0.0)
    assert len(result.marks) == 2


@pytest.mark.unit
def test_section_skips_malformed_primitives():
    geom = DrawingGeometry(
        page_w=500, page_h=800,
        lines=[(1, 2, 3), (50, 500, 400, 500)],       # 首条畸形（<4）
        texts=[(1, 2), (410, 500, "4.200"), (10, 10, 12345)],  # 畸形/非串跳过
    )
    result = extract_section_levels(geom)
    assert [round(m.elevation_m, 3) for m in result.marks] == [4.2]


# ── elevation_opening_extractor 边界 ──────────────────────────

def _elev_with_levels(rects, page_w=600):
    lines = [(40, 760, page_w - 40, 760), (40, 560, page_w - 40, 560), (40, 360, page_w - 40, 360)]
    texts = [(page_w - 25, 760, "±0.000"), (page_w - 25, 560, "3.000"), (page_w - 25, 360, "6.000")]
    for x, label in ((100, "1"), (300, "2"), (500, "3")):
        lines.append((x, 40, x, 780))
        texts.append((x, 20, label))
    return DrawingGeometry(page_w=page_w, page_h=800, lines=lines, texts=texts, rects=rects)


@pytest.mark.unit
def test_opening_axis_ref_spans_two_axes():
    """洞口横跨两根轴 → axis_ref='1-2'。"""
    geom = _elev_with_levels([(90, 500, 250, 100, False)])  # x 90..340 覆盖轴1(100)/2(300)
    opening = extract_elevation_openings(geom).openings[0]
    assert opening.axis_ref == "1-2"


@pytest.mark.unit
def test_opening_skips_malformed_rect():
    geom = _elev_with_levels([(1, 2, 3), (150, 500, 100, 100, False)])  # 首条畸形
    result = extract_elevation_openings(geom)
    assert len(result.openings) == 1


@pytest.mark.unit
def test_opening_no_labeled_axes_empty_axis_ref():
    geom = DrawingGeometry(
        page_w=600, page_h=800,
        lines=[(40, 760, 560, 760), (40, 560, 560, 560), (40, 360, 560, 360)],
        texts=[(575, 760, "±0.000"), (575, 560, "3.000"), (575, 360, "6.000")],
        rects=[(150, 500, 100, 100, False)],
    )
    opening = extract_elevation_openings(geom).openings[0]
    assert opening.axis_ref == ""


# ── grid_anchor_extractor 异常降级 ────────────────────────────

@pytest.mark.unit
def test_grid_extractor_degrades_on_detect_failure(monkeypatch):
    def _boom(*_args, **_kwargs):
        raise RuntimeError("detect failed")

    monkeypatch.setattr(grid_anchor_extractor, "_detect_axes", _boom)
    grid = extract_grid_anchors(DrawingGeometry(page_w=600, page_h=800, lines=[(0, 0, 600, 0)]))
    assert grid.axes_x == ()
    assert grid.unlabeled is True


# ── topology 降级 / 查询 miss ─────────────────────────────────

@pytest.mark.unit
def test_opening_without_center_is_orphan():
    rels = resolve_opening_host([{"id": "op1"}], [{"id": "w1", "path": [[0, 0], [5, 0]]}])
    assert rels[0].orphan is True
    assert rels[0].wall_id is None


@pytest.mark.unit
def test_topology_query_misses_return_empty():
    graph = build_topology_graph([], [], [], [], [])
    assert graph.beams_under("slab_x") == []
    assert graph.slabs_on("beam_x") == []
    assert graph.wall_of("opening_x") is None
