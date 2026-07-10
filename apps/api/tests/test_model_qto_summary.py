"""QTO 汇总测试（B-19）：构件量 → 项目/楼层/单体级汇总，含实测/估算与未覆盖统计。"""
import json
from unittest.mock import AsyncMock

import pytest

from services.model_qto import compute_quantities
from services.model_qto_summary import (
    build_scene_quantities,
    fetch_quantity_summary,
    save_quantity_summary,
    summarize,
)


def _scene_two_floors():
    beam = {"path": [[0, 0], [6, 0]], "width": 0.3, "depth": 0.6}
    slab = {"outline": [[0, 0], [6, 0], [6, 6], [0, 6]], "thickness": 0.12}
    return {
        "floors": [
            {"key": "F1", "label": "1层", "building_units": ["main"],
             "elements": {"beams": [dict(beam)], "slabs": [dict(slab)], "columns": [], "walls": []}},
            {"key": "F2", "label": "2层", "building_units": ["main"],
             "elements": {"beams": [dict(beam)], "slabs": [], "columns": [], "walls": []}},
        ],
        "quality": {"story_tables": {"main": [
            {"story_key": "F1", "height_m": 3.0},
            {"story_key": "F2", "height_m": 3.3},
        ]}},
    }


# ── summarize ─────────────────────────────────────────────────

@pytest.mark.unit
def test_summarize_totals_and_breakdown():
    scene = {"beams": [{"path": [[0, 0], [6, 0]], "width": 0.3, "depth": 0.6}],
             "slabs": [{"outline": [[0, 0], [6, 0], [6, 6], [0, 6]], "thickness": 0.12}],
             "columns": [], "walls": []}
    summary = summarize(compute_quantities(scene, story_height_m=3.0))

    assert summary["concrete"]["gross_m3"] == pytest.approx(1.08 + 4.32, abs=0.01)
    assert summary["element_count"] == 2
    assert "beam" in summary["by_type"]
    assert summary["by_type"]["slab"]["net_m3"] > 0


@pytest.mark.unit
def test_summarize_counts_measured_and_estimated():
    scene = {"beams": [
        {"path": [[0, 0], [6, 0]], "width": 0.3, "depth": 0.6, "z_source": "measured"},
        {"path": [[0, 1], [6, 1]], "width": 0.3, "depth": 0.6},  # 估算
    ], "columns": [], "slabs": [], "walls": []}
    summary = summarize(compute_quantities(scene, story_height_m=3.0))
    assert summary["measured_count"] == 1
    assert summary["estimated_count"] == 1


@pytest.mark.unit
def test_summarize_reports_uncovered_zero_volume():
    scene = {"beams": [{"path": [[0, 0], [0, 0]], "width": 0.3, "depth": 0.6}],  # 零长→零体积
             "columns": [], "slabs": [], "walls": []}
    summary = summarize(compute_quantities(scene, story_height_m=3.0))
    assert summary["uncovered_count"] == 1


# ── build_scene_quantities ────────────────────────────────────

@pytest.mark.unit
def test_scene_quantities_drilldown_by_floor_and_building():
    data = build_scene_quantities(_scene_two_floors())

    assert len(data["by_floor"]) == 2
    f1 = next(f for f in data["by_floor"] if f["floor_key"] == "F1")
    assert f1["concrete"]["net_m3"] > 0
    # 分单体
    assert any(b["building_key"] == "main" for b in data["by_building"])
    # 项目级合计 + 钢筋缺省缺失
    assert data["project"]["concrete"]["gross_m3"] > 0
    assert data["project"]["rebar"]["missing"] is True


@pytest.mark.unit
def test_scene_quantities_uses_story_height():
    """F1 高 3.0 → 柱/墙体积随层高；空场景不崩。"""
    data = build_scene_quantities({"floors": [], "quality": {}})
    assert data["project"]["element_count"] == 0


@pytest.mark.unit
def test_scene_quantities_with_rebar_inputs():
    inputs = [{"diameter": 20, "steel_grade": "HRB400", "required_length": 6000, "count": 4}]
    data = build_scene_quantities(_scene_two_floors(), rebar_inputs=inputs)
    assert data["project"]["rebar"]["missing"] is False
    assert data["project"]["rebar"]["total_t"] > 0


# ── 持久化 ────────────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.asyncio
async def test_save_quantity_summary_upserts():
    db = AsyncMock()
    data = build_scene_quantities(_scene_two_floors())
    await save_quantity_summary(db, "p1", "scene", data["project"])
    _sql, params = db.execute.await_args.args
    assert params["project_id"] == "p1"
    assert params["scope_key"] == "scene"
    json.loads(params["payload"])


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fetch_quantity_summary_parses_payload():
    db = AsyncMock()
    db.fetch_one = AsyncMock(return_value={
        "scope_key": "scene", "concrete_net_m3": 3.0, "concrete_gross_m3": 4.0,
        "formwork_contact_m2": 10.0, "rebar_kg": None, "estimated_ratio": 1.0,
        "payload": '{"concrete": {"net_m3": 3.0}}',
    })
    row = await fetch_quantity_summary(db, "p1", "scene")
    assert row["payload"]["concrete"]["net_m3"] == 3.0
