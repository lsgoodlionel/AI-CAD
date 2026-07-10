"""构件截面表测试（B-07）。

从剖面/详图标注文本抽真实截面（梁高×宽/板厚/墙厚/柱截面/管径），
缺证据回落默认并标 estimated；应用到楼层构件覆盖硬编码默认。
"""
import json
from unittest.mock import AsyncMock

import pytest

from services.model_component_sections import (
    DEFAULT_BEAM_DEPTH_M,
    DEFAULT_SLAB_THICKNESS_M,
    Section,
    apply_component_sections,
    build_component_sections,
    fetch_component_sections,
    upsert_component_sections,
)


# ── 标注解析 ────────────────────────────────────────────────────

@pytest.mark.unit
def test_beam_section_from_width_height_annotation():
    sections = build_component_sections(["KL1 300×600", "框架梁 300×700"])
    beam = sections["beam"]
    assert beam.w_m == pytest.approx(0.3)
    assert beam.h_m == pytest.approx(0.65, abs=0.06)  # 600/700 中位
    assert beam.source == "section"
    assert beam.estimated is False


@pytest.mark.unit
def test_column_section_requires_column_keyword():
    sections = build_component_sections(["框架柱 KZ1 500×500"])
    column = sections["column"]
    assert column.w_m == pytest.approx(0.5)
    assert column.h_m == pytest.approx(0.5)
    assert column.estimated is False
    # 柱截面不应污染梁
    assert sections["beam"].estimated is True


@pytest.mark.unit
def test_slab_thickness_annotation():
    sections = build_component_sections(["板厚120", "现浇板 板厚150"])
    slab = sections["slab"]
    assert slab.thickness_m == pytest.approx(0.135, abs=0.02)
    assert slab.estimated is False


@pytest.mark.unit
def test_wall_thickness_not_confused_with_slab():
    sections = build_component_sections(["墙厚200"])
    assert sections["wall"].thickness_m == pytest.approx(0.2)
    assert sections["wall"].estimated is False
    assert sections["slab"].estimated is True  # 未被墙厚污染


@pytest.mark.unit
def test_pipe_diameter_annotation():
    sections = build_component_sections(["DN100 给水管", "φ150"])
    pipe = sections["pipe"]
    assert pipe.diameter_m == pytest.approx(0.125, abs=0.03)
    assert pipe.estimated is False


@pytest.mark.unit
def test_out_of_range_dimensions_ignored():
    sections = build_component_sections(["梁 5×9999999"])
    assert sections["beam"].estimated is True  # 超范围不采信


# ── 默认回落 ────────────────────────────────────────────────────

@pytest.mark.unit
def test_no_annotation_falls_back_to_defaults_estimated():
    sections = build_component_sections([])
    assert sections["beam"].h_m == pytest.approx(DEFAULT_BEAM_DEPTH_M)
    assert sections["beam"].source == "default"
    assert sections["beam"].estimated is True
    assert sections["slab"].thickness_m == pytest.approx(DEFAULT_SLAB_THICKNESS_M)
    assert sections["slab"].estimated is True


@pytest.mark.unit
def test_all_component_types_present():
    sections = build_component_sections([])
    assert set(sections) == {"beam", "column", "slab", "wall", "pipe"}


# ── 应用到楼层构件 ─────────────────────────────────────────────

@pytest.mark.unit
def test_apply_measured_sections_overrides_element_defaults():
    sections = build_component_sections(["梁 300×500", "板厚150"])
    floors = [
        {
            "elements": {
                "beams": [{"path": [], "width": 0.3, "depth": 0.6}],
                "slabs": [{"outline": [], "thickness": 0.12}],
                "pipes": [],
            }
        }
    ]
    apply_component_sections(floors, sections)

    beam = floors[0]["elements"]["beams"][0]
    slab = floors[0]["elements"]["slabs"][0]
    assert beam["depth"] == pytest.approx(0.5)
    assert beam["z_source"] == "measured"
    assert slab["thickness"] == pytest.approx(0.15)
    assert slab["z_source"] == "measured"


@pytest.mark.unit
def test_apply_default_sections_is_noop():
    """全默认（estimated）时不改动构件字段——无回归。"""
    sections = build_component_sections([])
    floors = [{"elements": {"beams": [{"depth": 0.6}], "slabs": [], "pipes": []}}]
    apply_component_sections(floors, sections)
    beam = floors[0]["elements"]["beams"][0]
    assert beam["depth"] == pytest.approx(0.6)
    assert "z_source" not in beam


# ── 持久化仓储 ─────────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.asyncio
async def test_upsert_executes_per_section():
    db = AsyncMock()
    sections = build_component_sections(["梁 300×600"])
    written = await upsert_component_sections(db, "p1", "main", sections)
    assert written == len(sections)
    _sql, params = db.execute.await_args_list[0].args
    assert params["project_id"] == "p1"
    assert params["scope_key"] == "main"
    assert "evidence_ref" in params
    json.loads(params["evidence_ref"])  # 可反序列化


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fetch_parses_rows_to_sections():
    db = AsyncMock()
    db.fetch_all = AsyncMock(
        return_value=[
            {
                "component_type": "beam",
                "h_m": 0.6,
                "w_m": 0.3,
                "thickness_m": None,
                "diameter_m": None,
                "source": "section",
                "confidence": 0.85,
                "estimated": False,
                "evidence_ref": '{"samples": 2}',
            }
        ]
    )
    sections = await fetch_component_sections(db, "p1", "main")
    assert isinstance(sections["beam"], Section)
    assert sections["beam"].h_m == pytest.approx(0.6)
    assert sections["beam"].evidence == {"samples": 2}
