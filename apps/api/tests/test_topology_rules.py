"""构件拓扑规则测试（B-12/B-13/B-14，确定性纯几何）。

坐标均为米（平面 xy）。覆盖正常 / 悬挑 / 孤立 / 边界阈值。
"""
import pytest

from core.model3d.topology_rules import (
    resolve_beam_support,
    resolve_opening_host,
    resolve_slab_support,
)


def _wall(wid, x0, y0, x1, y1, width=0.2):
    return {"id": wid, "path": [[x0, y0], [x1, y1]], "width": width}


def _column(cid, cx, cy, half=0.25):
    return {
        "id": cid,
        "outline": [
            [cx - half, cy - half], [cx + half, cy - half],
            [cx + half, cy + half], [cx - half, cy + half],
        ],
    }


def _beam(bid, x0, y0, x1, y1, width=0.3):
    return {"id": bid, "path": [[x0, y0], [x1, y1]], "width": width, "depth": 0.6}


def _slab(sid, pts):
    return {"id": sid, "outline": pts}


# ── B-12 门窗-墙从属 ───────────────────────────────────────────

@pytest.mark.unit
def test_opening_on_wall_gets_host():
    walls = [_wall("w1", 0, 0, 5, 0)]
    openings = [{"id": "op1", "center": [2.5, 0.0]}]
    rels = resolve_opening_host(openings, walls)
    assert len(rels) == 1
    assert rels[0].opening_id == "op1"
    assert rels[0].wall_id == "w1"
    assert rels[0].orphan is False
    assert rels[0].confidence >= 0.9


@pytest.mark.unit
def test_opening_far_from_walls_is_orphan():
    walls = [_wall("w1", 0, 0, 5, 0)]
    openings = [{"id": "op1", "center": [2.5, 3.0]}]  # 悬空
    rels = resolve_opening_host(openings, walls)
    assert rels[0].orphan is True
    assert rels[0].wall_id is None


@pytest.mark.unit
def test_opening_at_wall_end_still_hosted():
    walls = [_wall("w1", 0, 0, 5, 0)]
    openings = [{"id": "op1", "center": [0.05, 0.0]}]  # 墙端
    rels = resolve_opening_host(openings, walls)
    assert rels[0].wall_id == "w1"


@pytest.mark.unit
def test_opening_from_outline_center():
    walls = [_wall("w1", 0, 0, 5, 0)]
    openings = [{"id": "op1", "outline": [[2, -0.1], [3, -0.1], [3, 0.1], [2, 0.1]]}]
    rels = resolve_opening_host(openings, walls)
    assert rels[0].wall_id == "w1"


@pytest.mark.unit
def test_opening_between_two_walls_picks_nearest():
    walls = [_wall("w1", 0, 0, 5, 0), _wall("w2", 0, 0.3, 5, 0.3)]
    openings = [{"id": "op1", "center": [2.5, 0.05]}]
    rels = resolve_opening_host(openings, walls)
    assert rels[0].wall_id == "w1"  # 更近


# ── B-13 梁-柱支承 ─────────────────────────────────────────────

@pytest.mark.unit
def test_beam_both_ends_on_columns():
    beams = [_beam("b1", 0, 0, 6, 0)]
    columns = [_column("c1", 0, 0), _column("c2", 6, 0)]
    rels = resolve_beam_support(beams, columns)
    ends = {(r.column_id, r.end) for r in rels}
    assert ("c1", "start") in ends
    assert ("c2", "end") in ends
    assert all(r.confidence >= 0.7 for r in rels)


@pytest.mark.unit
def test_cantilever_beam_only_one_end_supported():
    beams = [_beam("b1", 0, 0, 6, 0)]
    columns = [_column("c1", 0, 0)]  # 仅起点有柱
    rels = resolve_beam_support(beams, columns)
    assert len(rels) == 1
    assert rels[0].end == "start"


@pytest.mark.unit
def test_beam_floating_no_support():
    beams = [_beam("b1", 0, 0, 6, 0)]
    columns = [_column("c1", 20, 20)]
    rels = resolve_beam_support(beams, columns)
    assert rels == []


@pytest.mark.unit
def test_beam_end_within_near_threshold_of_column():
    """梁端略超柱截面但在近邻阈值内 → 仍支承。"""
    beams = [_beam("b1", -0.3, 0, 6, 0)]  # 起点在柱外 0.3m
    columns = [_column("c1", 0, 0, half=0.25)]
    rels = resolve_beam_support(beams, columns)
    assert any(r.column_id == "c1" and r.end == "start" for r in rels)


# ── B-14 板-梁托承 ─────────────────────────────────────────────

@pytest.mark.unit
def test_slab_supported_by_edge_beams():
    slab = _slab("s1", [[0, 0], [6, 0], [6, 6], [0, 6]])
    beams = [
        _beam("b1", 0, 0, 6, 0),   # 下边
        _beam("b2", 0, 6, 6, 6),   # 上边
        _beam("b3", 0, 0, 0, 6),   # 左边
    ]
    rels = resolve_slab_support([slab], beams)
    assert len(rels) == 1
    assert rels[0].slab_id == "s1"
    assert set(rels[0].beam_ids) == {"b1", "b2", "b3"}
    assert rels[0].confidence >= 0.7


@pytest.mark.unit
def test_cantilever_slab_no_beams_degraded():
    slab = _slab("s1", [[0, 0], [6, 0], [6, 6], [0, 6]])
    beams = [_beam("b1", 20, 20, 26, 20)]  # 无梁对齐板边
    rels = resolve_slab_support([slab], beams)
    assert list(rels[0].beam_ids) == []
    assert rels[0].confidence < 0.5


@pytest.mark.unit
def test_interior_beam_not_counted_as_edge_support():
    """板中部梁（不与板边对齐）不计入边托承。"""
    slab = _slab("s1", [[0, 0], [6, 0], [6, 6], [0, 6]])
    beams = [_beam("b1", 0, 3, 6, 3)]  # 板中横梁
    rels = resolve_slab_support([slab], beams)
    assert list(rels[0].beam_ids) == []
