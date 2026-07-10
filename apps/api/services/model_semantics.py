from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from services.drawing_semantics import SemanticCandidate, extract_semantic_candidates

AUTO_CONFIRM_THRESHOLD = 0.88
MIN_INDEPENDENT_EVIDENCE = 2
CANONICAL_NODE_TYPES = {
    "building_unit",
    "sub_zone",
    "functional_space",
    "construction_zone",
}


class SemanticVersionConflict(Exception):
    def __init__(self, latest: Mapping[str, Any]):
        self.latest = dict(latest)
        super().__init__("Semantic node version conflict")


class SemanticHierarchyError(ValueError):
    pass


@dataclass(frozen=True)
class SemanticNode:
    id: str
    node_type: str
    canonical_name: str
    normalized_key: str
    status: str = "candidate"
    confidence: float = 0.5
    source: str = "automatic"
    parent_id: str | None = None
    version: int = 1
    conflicts: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class SemanticEvidence:
    node_id: str
    drawing_id: str | None
    source: str
    source_value: str
    confidence: float
    location: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SemanticOperation:
    operation_type: str
    node_id: str | None
    target_node_id: str | None = None
    before_state: dict[str, Any] = field(default_factory=dict)
    after_state: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SemanticGraph:
    nodes: list[SemanticNode]
    evidence: list[SemanticEvidence]
    conflicts: list[dict[str, Any]]
    unassigned_drawings: list[dict[str, Any]]
    operations: list[SemanticOperation] = field(default_factory=list)
    version: int = 1

    @property
    def active_nodes(self) -> list[SemanticNode]:
        return [node for node in self.nodes if node.status in {"candidate", "confirmed"}]

    def as_dict(self) -> dict[str, Any]:
        return {
            "nodes": [node.__dict__ for node in self.nodes],
            "evidence": [item.__dict__ for item in self.evidence],
            "conflicts": self.conflicts,
            "unassigned_drawings": self.unassigned_drawings,
            "version": self.version,
        }


@dataclass(frozen=True)
class SemanticAssignment:
    drawing_id: str
    building_unit: dict[str, Any] | None = None
    sub_zone: dict[str, Any] | None = None
    functional_space: dict[str, Any] | None = None
    construction_zone: dict[str, Any] | None = None


def resolve_candidates(
    candidates: list[SemanticCandidate],
    drawing_id: str | None = None,
) -> SemanticGraph:
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    type_by_key: dict[str, set[str]] = {}
    for candidate in candidates:
        if candidate.node_type not in CANONICAL_NODE_TYPES:
            continue
        key = (candidate.node_type, candidate.normalized_key)
        entry = grouped.setdefault(
            key,
            {
                "node_type": candidate.node_type,
                "canonical_name": candidate.label,
                "normalized_key": candidate.normalized_key,
                "confidence": 0.0,
                "sources": set(),
                "evidence": [],
            },
        )
        entry["confidence"] = max(float(entry["confidence"]), float(candidate.confidence))
        entry["sources"].add(candidate.source)
        entry["evidence"].append(candidate)
        type_by_key.setdefault(candidate.normalized_key, set()).add(candidate.node_type)

    conflicts = [
        {"normalized_key": key, "node_types": sorted(types), "reason": "type_conflict"}
        for key, types in sorted(type_by_key.items())
        if len(types) > 1
    ]
    conflicted_keys = {item["normalized_key"] for item in conflicts}

    nodes: list[SemanticNode] = []
    evidence: list[SemanticEvidence] = []
    for index, item in enumerate(grouped.values(), start=1):
        normalized_key = str(item["normalized_key"])
        node_conflicts = [conflict for conflict in conflicts if conflict["normalized_key"] == normalized_key]
        status = "candidate"
        if (
            float(item["confidence"]) >= AUTO_CONFIRM_THRESHOLD
            and len(item["sources"]) >= MIN_INDEPENDENT_EVIDENCE
            and normalized_key not in conflicted_keys
        ):
            status = "confirmed"
        node_id = f"{item['node_type']}:{normalized_key}:{index}"
        nodes.append(
            SemanticNode(
                id=node_id,
                node_type=str(item["node_type"]),
                canonical_name=str(item["canonical_name"]),
                normalized_key=normalized_key,
                status=status,
                confidence=round(float(item["confidence"]), 4),
                conflicts=node_conflicts,
            )
        )
        for candidate in item["evidence"]:
            evidence.append(
                SemanticEvidence(
                    node_id=node_id,
                    drawing_id=drawing_id,
                    source=candidate.source,
                    source_value=candidate.source_value,
                    confidence=round(candidate.confidence, 4),
                    location=dict(candidate.context or {}),
                )
            )

    return SemanticGraph(
        nodes=nodes,
        evidence=evidence,
        conflicts=conflicts,
        unassigned_drawings=[],
    )


def apply_operation_to_graph(
    graph: SemanticGraph,
    operation: Mapping[str, Any],
) -> SemanticGraph:
    operation_type = str(operation.get("operation_type") or "")
    target_ids = [str(item) for item in operation.get("target_ids") or []]
    nodes = [SemanticNode(**node.__dict__) for node in graph.nodes]
    by_id = {node.id: node for node in nodes}

    if operation_type == "merge":
        if len(target_ids) < 2:
            raise SemanticHierarchyError("merge requires at least two target nodes")
        kept = by_id.get(target_ids[0])
        if kept is None:
            raise SemanticHierarchyError("merge target not found")
        name = str(operation.get("name") or operation.get("canonical_name") or kept.canonical_name)
        replacement = SemanticNode(
            **{**kept.__dict__, "canonical_name": name, "source": "manual", "status": "confirmed", "version": kept.version + 1}
        )
        merged_nodes = []
        for node in nodes:
            if node.id == replacement.id:
                merged_nodes.append(replacement)
            elif node.id in target_ids[1:]:
                merged_nodes.append(SemanticNode(**{**node.__dict__, "status": "merged", "version": node.version + 1}))
            else:
                merged_nodes.append(node)
        return _with_operation(graph, merged_nodes, operation_type, kept.id, None, kept.__dict__, replacement.__dict__)

    if operation_type == "rename":
        node_id = target_ids[0] if target_ids else str(operation.get("node_id") or "")
        node = by_id.get(node_id)
        if node is None:
            raise SemanticHierarchyError("rename target not found")
        renamed = SemanticNode(
            **{
                **node.__dict__,
                "canonical_name": str(operation.get("canonical_name") or operation.get("name") or node.canonical_name),
                "source": "manual",
                "status": "confirmed",
                "version": node.version + 1,
            }
        )
        return _replace_node(graph, nodes, renamed, operation_type, node.__dict__, renamed.__dict__)

    if operation_type == "reparent":
        node_id = target_ids[0] if target_ids else str(operation.get("node_id") or "")
        parent_id = str(operation.get("parent_id") or operation.get("target_node_id") or "")
        if node_id == parent_id:
            raise SemanticHierarchyError("node cannot be its own parent")
        _assert_no_cycle(by_id, node_id, parent_id)
        node = by_id.get(node_id)
        if node is None:
            raise SemanticHierarchyError("reparent target not found")
        updated = SemanticNode(**{**node.__dict__, "parent_id": parent_id or None, "source": "manual", "version": node.version + 1})
        return _replace_node(graph, nodes, updated, operation_type, node.__dict__, updated.__dict__)

    if operation_type in {"confirm", "reject"}:
        node_id = target_ids[0] if target_ids else str(operation.get("node_id") or "")
        node = by_id.get(node_id)
        if node is None:
            raise SemanticHierarchyError("operation target not found")
        updated = SemanticNode(
            **{
                **node.__dict__,
                "status": "confirmed" if operation_type == "confirm" else "rejected",
                "source": "manual",
                "version": node.version + 1,
            }
        )
        return _replace_node(graph, nodes, updated, operation_type, node.__dict__, updated.__dict__)

    raise SemanticHierarchyError(f"unsupported semantic operation: {operation_type}")


def _replace_node(
    graph: SemanticGraph,
    nodes: list[SemanticNode],
    replacement: SemanticNode,
    operation_type: str,
    before: dict[str, Any],
    after: dict[str, Any],
) -> SemanticGraph:
    return _with_operation(
        graph,
        [replacement if node.id == replacement.id else node for node in nodes],
        operation_type,
        replacement.id,
        replacement.parent_id,
        before,
        after,
    )


def _with_operation(
    graph: SemanticGraph,
    nodes: list[SemanticNode],
    operation_type: str,
    node_id: str | None,
    target_node_id: str | None,
    before: dict[str, Any],
    after: dict[str, Any],
) -> SemanticGraph:
    return SemanticGraph(
        nodes=nodes,
        evidence=graph.evidence,
        conflicts=graph.conflicts,
        unassigned_drawings=graph.unassigned_drawings,
        operations=[
            *graph.operations,
            SemanticOperation(
                operation_type=operation_type,
                node_id=node_id,
                target_node_id=target_node_id,
                before_state=before,
                after_state=after,
            ),
        ],
        version=graph.version + 1,
    )


def _assert_no_cycle(by_id: dict[str, SemanticNode], node_id: str, parent_id: str) -> None:
    current = parent_id
    visited: set[str] = set()
    while current:
        if current == node_id:
            raise SemanticHierarchyError("semantic hierarchy cycle rejected")
        if current in visited:
            raise SemanticHierarchyError("semantic hierarchy cycle rejected")
        visited.add(current)
        parent = by_id.get(current)
        current = parent.parent_id if parent else None


async def build_semantic_graph(db, project_id: str, drawings: list[Mapping[str, Any]]) -> SemanticGraph:
    all_nodes: list[SemanticNode] = []
    all_evidence: list[SemanticEvidence] = []
    conflicts: list[dict[str, Any]] = []
    unassigned: list[dict[str, Any]] = []

    for drawing in drawings:
        drawing_id = str(drawing.get("id") or drawing.get("drawing_id") or "")
        graph = resolve_candidates(extract_semantic_candidates(drawing), drawing_id=drawing_id)
        if not graph.nodes:
            unassigned.append(
                {
                    "drawing_id": drawing_id,
                    "drawing_no": str(drawing.get("drawing_no") or ""),
                    "title": str(drawing.get("title") or ""),
                    "reason": "semantic_unassigned",
                }
            )
        all_nodes.extend(graph.nodes)
        all_evidence.extend(graph.evidence)
        conflicts.extend(graph.conflicts)

    persisted = await _load_persisted_graph(db, project_id)
    if persisted.nodes:
        return persisted
    return SemanticGraph(
        nodes=all_nodes,
        evidence=all_evidence,
        conflicts=conflicts,
        unassigned_drawings=unassigned,
    )


async def apply_semantic_operation(
    db,
    project_id: str,
    actor_id: str,
    operation: Mapping[str, Any],
    expected_version: int | None = None,
) -> dict[str, Any]:
    node_id = str((operation.get("target_ids") or [operation.get("node_id") or ""])[0])
    node = await db.fetch_one(
        "SELECT * FROM model_semantic_nodes WHERE id=$1 AND project_id=$2",
        node_id,
        project_id,
    )
    if node is None:
        raise SemanticHierarchyError("semantic node not found")
    latest = dict(node)
    if expected_version is not None and int(latest.get("version") or 0) != int(expected_version):
        raise SemanticVersionConflict(latest)

    graph = SemanticGraph(nodes=[SemanticNode(**_node_from_row(latest))], evidence=[], conflicts=[], unassigned_drawings=[])
    updated_graph = apply_operation_to_graph(graph, {**dict(operation), "target_ids": [node_id]})
    updated = updated_graph.nodes[0]
    await db.execute(
        """
        UPDATE model_semantic_nodes
        SET canonical_name=$1, parent_id=$2, status=$3, source='manual',
            version=$4, updated_at=now()
        WHERE id=$5 AND project_id=$6
        """,
        updated.canonical_name,
        updated.parent_id,
        updated.status,
        updated.version,
        updated.id,
        project_id,
    )
    await db.execute(
        """
        INSERT INTO model_semantic_operations (
            project_id, operation_type, node_id, target_node_id,
            before_state, after_state, expected_version, performed_by
        )
        VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb, $7, $8)
        """,
        project_id,
        updated_graph.operations[-1].operation_type,
        updated.id,
        updated.parent_id,
        latest,
        updated.__dict__,
        expected_version,
        actor_id,
    )
    return {"node": updated.__dict__, "operation": updated_graph.operations[-1].__dict__}


async def load_confirmed_assignments(db, project_id: str) -> dict[str, SemanticAssignment]:
    rows = await db.fetch_all(
        """
        SELECT a.drawing_id, n.node_type, n.id, n.normalized_key, n.canonical_name
        FROM model_semantic_assignments a
        JOIN model_semantic_nodes n ON n.id = a.node_id
        WHERE a.project_id=$1 AND a.status='confirmed' AND n.status='confirmed'
        """,
        project_id,
    )
    assignments: dict[str, dict[str, Any]] = {}
    for row in rows:
        item = dict(row)
        drawing_id = str(item["drawing_id"])
        assignments.setdefault(drawing_id, {"drawing_id": drawing_id})
        assignments[drawing_id][str(item["node_type"])] = {
            "id": str(item["id"]),
            "key": str(item["normalized_key"]),
            "name": str(item["canonical_name"]),
        }
    return {
        drawing_id: SemanticAssignment(**assignment)
        for drawing_id, assignment in assignments.items()
    }


async def _load_persisted_graph(db, project_id: str) -> SemanticGraph:
    rows = await db.fetch_all(
        """
        SELECT id, node_type, canonical_name, normalized_key, status,
               confidence, source, parent_id, version
        FROM model_semantic_nodes
        WHERE project_id=$1
        ORDER BY node_type, canonical_name
        """,
        project_id,
    )
    nodes = [SemanticNode(**_node_from_row(dict(row))) for row in rows]
    return SemanticGraph(nodes=nodes, evidence=[], conflicts=[], unassigned_drawings=[])


def _node_from_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row.get("id")),
        "node_type": str(row.get("node_type")),
        "canonical_name": str(row.get("canonical_name")),
        "normalized_key": str(row.get("normalized_key")),
        "status": str(row.get("status") or "candidate"),
        "confidence": float(row.get("confidence") or 0.5),
        "source": str(row.get("source") or "automatic"),
        "parent_id": str(row["parent_id"]) if row.get("parent_id") else None,
        "version": int(row.get("version") or 1),
    }
