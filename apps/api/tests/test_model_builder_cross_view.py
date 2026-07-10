"""cross_view_match gate 点亮测试（B-05）。

验证剖面 z 恢复的 matched_units 如何驱动 ModelScopeEvidence.has_cross_view_match，
以及无剖面时 _recover_section_z 为 no-op（gate 保持 False，无回归）。
"""
import sys
import types

import pytest

# 复用 story_spacing 测试的轻量桩，避免真实 minio / ai_review 依赖
if "pydantic_settings" not in sys.modules:
    module = types.ModuleType("pydantic_settings")

    class _BaseSettings:
        def __init__(self, **kwargs):
            for key, value in self.__class__.__dict__.items():
                if key.startswith("_") or callable(value):
                    continue
                setattr(self, key, value)
            for key, value in kwargs.items():
                setattr(self, key, value)

    module.BaseSettings = _BaseSettings
    sys.modules["pydantic_settings"] = module

if "minio" not in sys.modules:
    minio_module = types.ModuleType("minio")

    class _Minio:
        def __init__(self, *args, **kwargs):
            pass

    minio_module.Minio = _Minio
    sys.modules["minio"] = minio_module
    error_module = types.ModuleType("minio.error")

    class S3Error(Exception):
        pass

    error_module.S3Error = S3Error
    sys.modules["minio.error"] = error_module

import services.model_builder as model_builder  # noqa: E402
from services.section_z_recovery import SectionZRecovery  # noqa: E402


def _floor(key: str, order: int, units: list[str]) -> dict:
    return {
        "key": key,
        "order": order,
        "building_units": units,
        "drawings": [{"drawing_id": f"d-{key}"}],
    }


# ── gate 点亮逻辑（纯）─────────────────────────────────────────

@pytest.mark.unit
def test_matched_unit_lights_cross_view_gate():
    floors = [_floor("F1", 1, ["main"]), _floor("F2", 2, ["main"])]
    scope = model_builder._scope_evidence_for("scene", "总体", floors, [], {"main"})
    assert scope.has_cross_view_match is True


@pytest.mark.unit
def test_unmatched_unit_keeps_gate_false():
    floors = [_floor("F1", 1, ["south"])]
    scope = model_builder._scope_evidence_for("scene", "总体", floors, [], {"north"})
    assert scope.has_cross_view_match is False


@pytest.mark.unit
def test_no_matched_units_keeps_gate_false():
    floors = [_floor("F1", 1, ["main"])]
    scope = model_builder._scope_evidence_for("scene", "总体", floors, [], set())
    assert scope.has_cross_view_match is False


@pytest.mark.unit
def test_single_building_scope_matches_any_recovered_unit():
    """无单体细分的 scope（building_units 空）→ 有匹配即命中。"""
    floors = [{"key": "F1", "order": 1, "building_units": [], "drawings": [{"drawing_id": "d"}]}]
    scope = model_builder._scope_evidence_for("scene", "总体", floors, [], {"main"})
    assert scope.has_cross_view_match is True


# ── no-op（无剖面无回归）──────────────────────────────────────

@pytest.mark.unit
@pytest.mark.asyncio
async def test_recover_section_z_noop_without_section_drawings():
    drawings = [
        {"id": "1", "title": "一层平面图", "drawing_no": "A-101", "file_key": "a.pdf"},
        {"id": "2", "title": "二层平面图", "drawing_no": "A-201", "file_key": "b.pdf"},
    ]

    class _EmptyNorm:
        stories_by_building: dict = {}

    recovery = await model_builder._recover_section_z(drawings, _EmptyNorm())
    assert isinstance(recovery, SectionZRecovery)
    assert recovery.matched_units == set()
    assert recovery.z_overrides == {}


# ── B-15 拓扑证据驱动 gate ─────────────────────────────────────

def _connected_floor() -> dict:
    def _col(cx, cy, half=0.25):
        return {"outline": [[cx - half, cy - half], [cx + half, cy - half],
                            [cx + half, cy + half], [cx - half, cy + half]]}
    return {
        "elements": {
            "walls": [],
            "columns": [_col(0, 0), _col(6, 0), _col(6, 6), _col(0, 6)],
            "beams": [
                {"path": [[0, 0], [6, 0]], "width": 0.3, "depth": 0.6},
                {"path": [[6, 0], [6, 6]], "width": 0.3, "depth": 0.6},
                {"path": [[6, 6], [0, 6]], "width": 0.3, "depth": 0.6},
                {"path": [[0, 6], [0, 0]], "width": 0.3, "depth": 0.6},
            ],
            "slabs": [{"outline": [[0, 0], [6, 0], [6, 6], [0, 6]]}],
        }
    }


@pytest.mark.unit
def test_topology_evidence_lights_geometry_gates():
    evidence = model_builder._topology_lod_evidence([_connected_floor()])
    assert evidence["stable_component_boundaries"] is True
    assert evidence["geometry_consistent"] is True


@pytest.mark.unit
def test_collect_scope_lod_evidence_reflects_topology():
    evidence = model_builder._collect_scope_lod_evidence([_connected_floor()], [], [])
    assert evidence["geometry_consistent"] is True
    assert evidence["stable_component_boundaries"] is True


@pytest.mark.unit
def test_sparse_floor_topology_leaves_gates_off():
    sparse = {"elements": {"walls": [], "columns": [], "beams": [
        {"path": [[0, 0], [6, 0]], "width": 0.3}], "slabs": []}}
    evidence = model_builder._collect_scope_lod_evidence([sparse], [], [])
    assert evidence["geometry_consistent"] is False
