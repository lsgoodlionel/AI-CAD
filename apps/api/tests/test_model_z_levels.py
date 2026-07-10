"""跨视图 z 恢复标高表仓储测试（B-03）。

覆盖 upsert/fetch 读写函数、来源校验、evidence_ref 序列化/解析、
以及供 B-04 消费的 height_overrides 映射。使用 FakeDB（无真实 PG）。
"""
import json
from unittest.mock import AsyncMock

import pytest

from services.model_z_levels import (
    VALID_Z_SOURCES,
    ZLevelEntry,
    build_z_level_params,
    fetch_z_levels,
    to_height_overrides,
    upsert_z_levels,
)


def _entry(**overrides) -> ZLevelEntry:
    base = dict(
        scope_key="main",
        story_key="F2",
        story_order=2,
        elevation_bottom_m=4.2,
        story_height_m=4.2,
        source="section",
        confidence=0.9,
        evidence_ref={"section_drawing_id": "d1", "fit_residual": 0.0},
    )
    base.update(overrides)
    return ZLevelEntry(**base)


# ── 参数构造 / 校验 ─────────────────────────────────────────────

@pytest.mark.unit
def test_build_params_serializes_evidence_ref_to_json():
    params = build_z_level_params("p1", _entry())
    assert params["project_id"] == "p1"
    assert params["scope_key"] == "main"
    assert params["story_key"] == "F2"
    assert params["source"] == "section"
    assert json.loads(params["evidence_ref"]) == {
        "section_drawing_id": "d1",
        "fit_residual": 0.0,
    }


@pytest.mark.unit
def test_valid_sources_are_section_elevation_estimated():
    assert VALID_Z_SOURCES == {"section", "elevation", "estimated"}


@pytest.mark.unit
def test_build_params_rejects_unknown_source():
    with pytest.raises(ValueError, match="source"):
        build_z_level_params("p1", _entry(source="measured"))


# ── upsert ──────────────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.asyncio
async def test_upsert_executes_one_statement_per_entry():
    db = AsyncMock()
    entries = [_entry(story_key="F1", story_order=1), _entry(story_key="F2", story_order=2)]

    written = await upsert_z_levels(db, "p1", entries)

    assert written == 2
    assert db.execute.await_count == 2
    _sql, params = db.execute.await_args_list[0].args
    assert params["project_id"] == "p1"
    assert params["scope_key"] == "main"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_upsert_empty_is_noop():
    db = AsyncMock()
    written = await upsert_z_levels(db, "p1", [])
    assert written == 0
    db.execute.assert_not_awaited()


# ── fetch ───────────────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.asyncio
async def test_fetch_parses_evidence_ref_json_string():
    db = AsyncMock()
    db.fetch_all = AsyncMock(
        return_value=[
            {
                "scope_key": "main",
                "story_key": "F1",
                "story_order": 1,
                "elevation_bottom_m": 0.0,
                "story_height_m": 4.2,
                "source": "section",
                "confidence": 0.9,
                "evidence_ref": '{"section_drawing_id": "d1"}',
            }
        ]
    )

    rows = await fetch_z_levels(db, "p1")

    assert rows[0]["evidence_ref"] == {"section_drawing_id": "d1"}
    _sql, params = db.fetch_all.await_args.args
    assert params == {"project_id": "p1"}


# ── height_overrides 映射（供 B-04）─────────────────────────────

@pytest.mark.unit
def test_to_height_overrides_keys_by_scope_and_story():
    rows = [
        {
            "scope_key": "main",
            "story_key": "F2",
            "elevation_bottom_m": 4.2,
            "story_height_m": 4.2,
            "source": "section",
            "confidence": 0.9,
        }
    ]
    overrides = to_height_overrides(rows)
    assert ("main", "F2") in overrides
    entry = overrides[("main", "F2")]
    assert entry["height_m"] == pytest.approx(4.2)
    assert entry["elevation_bottom_m"] == pytest.approx(4.2)
    assert entry["source"] == "section"
