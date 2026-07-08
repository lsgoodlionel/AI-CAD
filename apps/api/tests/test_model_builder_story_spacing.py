import sys
import types
from unittest.mock import AsyncMock

import pytest

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

if "minio.error" not in sys.modules:
    error_module = types.ModuleType("minio.error")

    class S3Error(Exception):
        pass

    error_module.S3Error = S3Error
    sys.modules["minio.error"] = error_module

if "core.ai_review" not in sys.modules:
    package = types.ModuleType("core.ai_review")
    package.__path__ = []
    sys.modules["core.ai_review"] = package

if "core.ai_review.dwg_support" not in sys.modules:
    dwg_support = types.ModuleType("core.ai_review.dwg_support")

    def ensure_dxf(data: bytes, file_ext: str):
        return data, file_ext, None

    dwg_support.ensure_dxf = ensure_dxf
    sys.modules["core.ai_review.dwg_support"] = dwg_support

import services.model_builder as model_builder
import services.model_elements as model_elements
from services.model_builder import build_scene

PROJECT_ID = "22222222-2222-2222-2222-222222222222"


@pytest.fixture
def fake_db():
    class _FakeDB:
        def __init__(self):
            self.fetch_one = AsyncMock(return_value=None)
            self.fetch_all = AsyncMock(return_value=[])

    return _FakeDB()


def _drawing(drawing_id: str, drawing_no: str, title: str, *, file_key: str = "projects/p/d.pdf") -> dict:
    return {
        "id": drawing_id,
        "drawing_no": drawing_no,
        "title": title,
        "discipline": "architecture",
        "status": "ai_done",
        "current_stage": "technical_review",
        "file_key": file_key,
    }


def _arrange(fake_db, drawings: list[dict], issues: list[dict]) -> None:
    fake_db.fetch_one.side_effect = [{"id": PROJECT_ID, "name": "动态单体项目"}, None]
    fake_db.fetch_all.side_effect = [drawings, issues] if drawings else [drawings]


def _fake_render(project_id, drawing_id, file_key, file_ext) -> dict:
    return {"image_key": f"k/{drawing_id}.png", "width": 100, "height": 100, "parser": "pdf"}


async def _fake_build_floor_elements(executor, floor_drawings, file_getter):
    return {key: [] for key in model_elements.EMPTY_ELEMENTS}, 0, {"elevations": [], "registered": 0}


@pytest.mark.asyncio
async def test_build_scene_emits_quality_payload_and_dynamic_building_units(fake_db, monkeypatch):
    monkeypatch.setattr(model_builder, "_render_and_upload_sync", _fake_render)
    monkeypatch.setattr(model_elements, "build_floor_elements", _fake_build_floor_elements)
    monkeypatch.setattr(
        model_builder,
        "_load_annotation_overrides",
        AsyncMock(
            return_value={
                "south-1": {"elevation_m": 0.0},
                "south-2": {"elevation_m": 1.2},
            }
        ),
    )

    _arrange(
        fake_db,
        [
            _drawing("south-1", "A-S-101", "南区一层平面图"),
            _drawing("south-2", "A-S-201", "南区二层平面图"),
            _drawing("tower-1", "A-2-101", "首层平面图", file_key="projects/p/2#楼/A-2-101.pdf"),
            _drawing("detail-1", "A-D-001", "楼梯节点详图"),
        ],
        [],
    )

    scene, _ = await build_scene(fake_db, PROJECT_ID)

    assert "quality" in scene
    assert scene["quality"]["unclassified_drawings"][0]["drawing_id"] == "detail-1"
    assert any(issue["issue_type"] == "story_spacing_too_small" for issue in scene["quality"]["issues"])
    unit_keys = {item["unit_key"] for item in scene["quality"]["building_units"]}
    assert {"south", "building_2", "main"} <= unit_keys
    buildings = {item["key"]: item for item in scene["buildings"]}
    assert buildings["building_2"]["label"] == "2#楼"
    assert buildings["south"]["floors"][1]["elevation_m"] == pytest.approx(4.5)
