"""构件拓扑图装配测试（B-15）。

汇 B-12~B-14 关系成图，支持「梁支承哪些板/落在哪些柱」查询、孤立率上报、
LOD 证据（stable_component_boundaries/geometry_consistent）由拓扑驱动；含持久化。
"""
import json
from unittest.mock import AsyncMock

import pytest

from services.model_topology import (
    TopologyGraph,
    build_topology_graph,
    fetch_topology_relations,
    save_topology,
)


def _column(cx, cy, half=0.25):
    return {
        "outline": [
            [cx - half, cy - half], [cx + half, cy - half],
            [cx + half, cy + half], [cx - half, cy + half],
        ],
    }


def _connected_scene():
    columns = [_column(0, 0), _column(6, 0), _column(6, 6), _column(0, 6)]
    beams = [
        {"path": [[0, 0], [6, 0]], "width": 0.3, "depth": 0.6},
        {"path": [[6, 0], [6, 6]], "width": 0.3, "depth": 0.6},
        {"path": [[6, 6], [0, 6]], "width": 0.3, "depth": 0.6},
        {"path": [[0, 6], [0, 0]], "width": 0.3, "depth": 0.6},
    ]
    slabs = [{"outline": [[0, 0], [6, 0], [6, 6], [0, 6]]}]
    walls = [{"path": [[0, 0], [6, 0]], "width": 0.2}]
    openings = [{"center": [3, 0]}]
    return walls, columns, beams, slabs, openings


# ── 图装配与查询 ───────────────────────────────────────────────

@pytest.mark.unit
def test_build_graph_nodes_and_queries():
    graph = build_topology_graph(*_connected_scene())
    assert isinstance(graph, TopologyGraph)

    # 板托承于全部 4 根边梁
    assert len(graph.beams_under("slab_0")) == 4
    # 梁两端落柱
    assert len(graph.columns_under("beam_0")) == 2
    # 洞口归属墙
    assert graph.wall_of("opening_0") == "wall_0"


@pytest.mark.unit
def test_connected_scene_low_isolation_and_lod_evidence():
    graph = build_topology_graph(*_connected_scene())
    assert graph.isolated_ratio() < 0.5
    evidence = graph.lod_evidence()
    assert evidence["stable_component_boundaries"] is True
    assert evidence["geometry_consistent"] is True


@pytest.mark.unit
def test_isolated_components_reported():
    # 一根悬空梁、无柱无板 → 全孤立
    graph = build_topology_graph([], [], [{"path": [[0, 0], [6, 0]], "width": 0.3}], [], [])
    assert graph.isolated_ratio() == pytest.approx(1.0)
    assert graph.lod_evidence()["geometry_consistent"] is False


@pytest.mark.unit
def test_empty_scene_lod_evidence_false():
    graph = build_topology_graph([], [], [], [], [])
    evidence = graph.lod_evidence()
    assert evidence["stable_component_boundaries"] is False
    assert evidence["geometry_consistent"] is False


@pytest.mark.unit
def test_stats_reports_counts():
    graph = build_topology_graph(*_connected_scene())
    stats = graph.stats()
    assert stats["node_count"] == 11  # 1墙+4柱+4梁+1板+1洞
    assert stats["isolated_count"] >= 0
    assert "orphan_openings" in stats


# ── 持久化 ──────────────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.asyncio
async def test_save_topology_writes_relations():
    db = AsyncMock()
    graph = build_topology_graph(*_connected_scene())
    written = await save_topology(db, "p1", "main", graph)
    assert written > 0
    # 至少一次 execute 带 relation_type
    _sql, params = db.execute.await_args_list[-1].args
    assert params["project_id"] == "p1"
    assert params["scope_key"] == "main"
    assert params["relation_type"] in ("host", "beam_support", "slab_support")
    json.loads(params["evidence_ref"])


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fetch_topology_relations_parses_rows():
    db = AsyncMock()
    db.fetch_all = AsyncMock(
        return_value=[
            {
                "relation_type": "beam_support",
                "source_id": "beam_0",
                "source_type": "beam",
                "target_id": "column_0",
                "target_type": "column",
                "end_label": "start",
                "confidence": 0.9,
                "evidence_ref": "{}",
            }
        ]
    )
    rows = await fetch_topology_relations(db, "p1", "main")
    assert rows[0]["relation_type"] == "beam_support"
    assert rows[0]["evidence_ref"] == {}
