from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from services.drawing_semantics import SemanticCandidate
from services.model_semantics import (
    SemanticGraph,
    SemanticHierarchyError,
    SemanticNode,
    SemanticVersionConflict,
    apply_operation_to_graph,
    apply_semantic_operation,
    build_semantic_graph,
    resolve_candidates,
)


MIGRATION = Path("migrations/016_model_semantic_graph.sql")


@pytest.mark.unit
def test_semantic_graph_migration_defines_required_tables_and_node_types():
    sql = MIGRATION.read_text()

    for table in (
        "model_semantic_nodes",
        "model_semantic_evidence",
        "model_semantic_assignments",
        "model_semantic_operations",
    ):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in sql

    for node_type in (
        "building_unit",
        "sub_zone",
        "functional_space",
        "construction_zone",
    ):
        assert node_type in sql


@pytest.mark.unit
def test_semantic_graph_migration_keeps_sources_and_statuses_auditable():
    sql = MIGRATION.read_text()

    for source in ("automatic", "manual", "legacy_inference"):
        assert source in sql
    for status in ("candidate", "confirmed", "rejected", "merged"):
        assert status in sql
    for operation in (
        "confirm",
        "reject",
        "rename",
        "merge",
        "split",
        "reparent",
        "assign",
        "unassign",
    ):
        assert operation in sql


@pytest.mark.unit
def test_semantic_graph_migration_preserves_legacy_units_as_candidates():
    sql = MIGRATION.read_text()

    assert "FROM model_building_units" in sql
    assert "'candidate'," in sql
    assert "'legacy_inference'" in sql
    assert "ON CONFLICT DO NOTHING" in sql
    assert "status IN ('candidate', 'confirmed')" in sql


def _candidate(
    node_type: str,
    label: str,
    source: str,
    confidence: float,
) -> SemanticCandidate:
    return SemanticCandidate(
        node_type=node_type,
        label=label,
        normalized_key=label.replace(" ", "").lower(),
        confidence=confidence,
        source=source,
        source_value=label,
        context={},
    )


def test_resolver_requires_independent_evidence_before_promoting_directional_unit():
    graph = resolve_candidates(
        [
            _candidate("building_unit", "南区", "filename", 0.62),
            _candidate("sub_zone", "南区", "ocr", 0.48),
        ]
    )

    assert graph.nodes
    assert all(node.status == "candidate" for node in graph.nodes)
    assert graph.conflicts[0]["reason"] == "type_conflict"


def test_resolver_confirms_only_high_confidence_independent_evidence():
    graph = resolve_candidates(
        [
            _candidate("building_unit", "3#楼", "title", 0.9),
            _candidate("building_unit", "3#楼", "drawing_no", 0.91),
        ]
    )

    assert len(graph.nodes) == 1
    assert graph.nodes[0].status == "confirmed"


def test_manual_merge_wins_and_is_audited():
    graph = SemanticGraph(
        nodes=[
            SemanticNode("a", "sub_zone", "A区", "a区"),
            SemanticNode("b", "sub_zone", "A 区", "a区"),
        ],
        evidence=[],
        conflicts=[],
        unassigned_drawings=[],
    )

    result = apply_operation_to_graph(
        graph,
        {"operation_type": "merge", "target_ids": ["a", "b"], "name": "A区"},
    )

    assert len(result.active_nodes) == 1
    assert result.active_nodes[0].status == "confirmed"
    assert result.operations[-1].operation_type == "merge"


def test_reparent_rejects_cycles():
    graph = SemanticGraph(
        nodes=[
            SemanticNode("parent", "building_unit", "A座", "a", parent_id="child"),
            SemanticNode("child", "sub_zone", "A区", "azone"),
        ],
        evidence=[],
        conflicts=[],
        unassigned_drawings=[],
    )

    with pytest.raises(SemanticHierarchyError):
        apply_operation_to_graph(
            graph,
            {"operation_type": "reparent", "target_ids": ["child"], "parent_id": "parent"},
        )


@pytest.mark.asyncio
async def test_apply_semantic_operation_returns_version_conflict():
    class _DB:
        def __init__(self):
            self.fetch_one = AsyncMock(
                return_value={
                    "id": "node-1",
                    "node_type": "building_unit",
                    "canonical_name": "A座",
                    "normalized_key": "a",
                    "status": "candidate",
                    "confidence": 0.5,
                    "source": "automatic",
                    "parent_id": None,
                    "version": 3,
                }
            )
            self.execute = AsyncMock()

    with pytest.raises(SemanticVersionConflict) as exc_info:
        await apply_semantic_operation(
            _DB(),
            "project-1",
            "user-1",
            {"operation_type": "rename", "target_ids": ["node-1"], "canonical_name": "新名称"},
            expected_version=2,
        )

    assert exc_info.value.latest["version"] == 3


@pytest.mark.asyncio
async def test_build_semantic_graph_keeps_unmatched_drawings_unassigned():
    class _DB:
        fetch_all = AsyncMock(return_value=[])

    graph = await build_semantic_graph(
        _DB(),
        "project-1",
        [{"id": "d1", "drawing_no": "M-001", "title": "总说明"}],
    )

    assert graph.nodes == []
    assert graph.unassigned_drawings[0]["drawing_id"] == "d1"
