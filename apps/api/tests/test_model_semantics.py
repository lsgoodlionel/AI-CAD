from pathlib import Path

import pytest


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
