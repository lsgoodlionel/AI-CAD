"""构件拓扑图装配（B-15）：汇 B-12~B-14 关系成图 + LOD 证据 + 持久化。

节点=构件，边=从属/支承。为 QTO 提供扣减依据与几何一致性检查：
拓扑闭合（梁支承、板托承、洞口归属）驱动 stable_component_boundaries / geometry_consistent gate。

查询用轻量邻接表（不引 networkx，KISS）；查询深度天然 1 跳，无深度爆炸风险。
含 migration 021 model_topology_relations 的读写（Repository Pattern）。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from core.model3d.topology_rules import (
    BeamSupport,
    HostRel,
    SlabSupport,
    resolve_beam_support,
    resolve_opening_host,
    resolve_slab_support,
)

# 孤立率上限：低于此值视为拓扑闭合（可信几何一致）
_ISOLATED_RATIO_MAX = 0.5


@dataclass(frozen=True)
class TopologyGraph:
    nodes: dict[str, str] = field(default_factory=dict)   # id -> type
    host_rels: tuple[HostRel, ...] = ()
    beam_supports: tuple[BeamSupport, ...] = ()
    slab_supports: tuple[SlabSupport, ...] = ()

    def columns_under(self, beam_id: str) -> list[str]:
        return [rel.column_id for rel in self.beam_supports if rel.beam_id == beam_id]

    def beams_under(self, slab_id: str) -> list[str]:
        for rel in self.slab_supports:
            if rel.slab_id == slab_id:
                return list(rel.beam_ids)
        return []

    def slabs_on(self, beam_id: str) -> list[str]:
        return [rel.slab_id for rel in self.slab_supports if beam_id in rel.beam_ids]

    def wall_of(self, opening_id: str) -> str | None:
        for rel in self.host_rels:
            if rel.opening_id == opening_id:
                return rel.wall_id
        return None

    def _connected_ids(self) -> set[str]:
        connected: set[str] = set()
        for rel in self.host_rels:
            if not rel.orphan and rel.wall_id:
                connected.update((rel.opening_id, rel.wall_id))
        for rel in self.beam_supports:
            connected.update((rel.beam_id, rel.column_id))
        for rel in self.slab_supports:
            if rel.beam_ids:
                connected.add(rel.slab_id)
                connected.update(rel.beam_ids)
        return connected

    def isolated_nodes(self) -> list[str]:
        connected = self._connected_ids()
        return sorted(node for node in self.nodes if node not in connected)

    def isolated_ratio(self) -> float:
        if not self.nodes:
            return 0.0
        return round(len(self.isolated_nodes()) / len(self.nodes), 4)

    def stats(self) -> dict[str, Any]:
        edge_count = (
            sum(1 for r in self.host_rels if not r.orphan)
            + len(self.beam_supports)
            + sum(len(r.beam_ids) for r in self.slab_supports)
        )
        return {
            "node_count": len(self.nodes),
            "edge_count": edge_count,
            "isolated_count": len(self.isolated_nodes()),
            "isolated_ratio": self.isolated_ratio(),
            "orphan_openings": sum(1 for r in self.host_rels if r.orphan),
        }

    def lod_evidence(self) -> dict[str, bool]:
        """拓扑闭合驱动 LOD gate：需有支承关系且孤立率达标。"""
        has_support = bool(self.beam_supports or self.slab_supports)
        has_topology = has_support or any(not r.orphan for r in self.host_rels)
        closed = bool(self.nodes) and self.isolated_ratio() <= _ISOLATED_RATIO_MAX
        return {
            "stable_component_boundaries": has_topology and closed,
            "geometry_consistent": has_support and closed,
        }


def build_topology_graph(
    walls: list[dict],
    columns: list[dict],
    beams: list[dict],
    slabs: list[dict],
    openings: list[dict] | None = None,
) -> TopologyGraph:
    """装配拓扑图：为各构件赋稳定 id（type_index），跑三条规则成图。"""
    openings = openings or []
    id_walls = _with_ids(walls, "wall")
    id_columns = _with_ids(columns, "column")
    id_beams = _with_ids(beams, "beam")
    id_slabs = _with_ids(slabs, "slab")
    id_openings = _with_ids(openings, "opening")

    nodes: dict[str, str] = {}
    for elements, node_type in (
        (id_walls, "wall"), (id_columns, "column"), (id_beams, "beam"),
        (id_slabs, "slab"), (id_openings, "opening"),
    ):
        for element in elements:
            nodes[element["id"]] = node_type

    return TopologyGraph(
        nodes=nodes,
        host_rels=tuple(resolve_opening_host(id_openings, id_walls)),
        beam_supports=tuple(resolve_beam_support(id_beams, id_columns)),
        slab_supports=tuple(resolve_slab_support(id_slabs, id_beams)),
    )


def _with_ids(elements: list[dict], prefix: str) -> list[dict]:
    """赋稳定 id（保留已有 id），返回浅拷贝，不改动入参。"""
    result: list[dict] = []
    for index, element in enumerate(elements or []):
        item = dict(element)
        item.setdefault("id", f"{prefix}_{index}")
        result.append(item)
    return result


# ── 持久化仓储 ─────────────────────────────────────────────────

_INSERT_SQL = """
INSERT INTO model_topology_relations (
    project_id, scope_key, relation_type,
    source_id, source_type, target_id, target_type, end_label, confidence, evidence_ref
)
VALUES (
    :project_id, :scope_key, :relation_type,
    :source_id, :source_type, :target_id, :target_type, :end_label, :confidence,
    CAST(:evidence_ref AS jsonb)
)
"""

_DELETE_SQL = "DELETE FROM model_topology_relations WHERE project_id = :project_id AND scope_key = :scope_key"

_SELECT_SQL = """
SELECT relation_type, source_id, source_type, target_id, target_type,
       end_label, confidence, evidence_ref
FROM model_topology_relations
WHERE project_id = :project_id AND scope_key = :scope_key
"""


async def save_topology(db, project_id: str, scope_key: str, graph: TopologyGraph) -> int:
    """覆盖式落库该 scope 的拓扑关系（先删后插），返回写入条数。"""
    await db.execute(_DELETE_SQL, {"project_id": project_id, "scope_key": scope_key})
    written = 0
    for params in _relation_params(project_id, scope_key, graph):
        await db.execute(_INSERT_SQL, params)
        written += 1
    return written


async def fetch_topology_relations(db, project_id: str, scope_key: str) -> list[dict[str, Any]]:
    rows = await db.fetch_all(_SELECT_SQL, {"project_id": project_id, "scope_key": scope_key})
    result: list[dict[str, Any]] = []
    for row in rows or []:
        record = dict(row)
        record["evidence_ref"] = _parse_evidence(record.get("evidence_ref"))
        result.append(record)
    return result


def _relation_params(project_id: str, scope_key: str, graph: TopologyGraph) -> list[dict[str, Any]]:
    params: list[dict[str, Any]] = []
    for rel in graph.host_rels:
        if rel.orphan or not rel.wall_id:
            continue
        params.append(_row(project_id, scope_key, "host", rel.opening_id, "opening",
                           rel.wall_id, "wall", None, rel.confidence))
    for rel in graph.beam_supports:
        params.append(_row(project_id, scope_key, "beam_support", rel.beam_id, "beam",
                           rel.column_id, "column", rel.end, rel.confidence))
    for rel in graph.slab_supports:
        for beam_id in rel.beam_ids:
            params.append(_row(project_id, scope_key, "slab_support", rel.slab_id, "slab",
                               beam_id, "beam", None, rel.confidence))
    return params


def _row(project_id, scope_key, relation_type, source_id, source_type,
         target_id, target_type, end_label, confidence) -> dict[str, Any]:
    return {
        "project_id": project_id,
        "scope_key": scope_key,
        "relation_type": relation_type,
        "source_id": source_id,
        "source_type": source_type,
        "target_id": target_id,
        "target_type": target_type,
        "end_label": end_label,
        "confidence": round(float(confidence), 4),
        "evidence_ref": json.dumps({}, ensure_ascii=False),
    }


def _parse_evidence(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return {}
    return dict(value) if isinstance(value, dict) else {}
