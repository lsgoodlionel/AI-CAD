"""scene V2 组装测试（schema_version=2：单体分组 + 构件层 + 回退）"""
import json
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
D_SOUTH = "77777777-7777-7777-7777-777777777771"
D_NORTH = "77777777-7777-7777-7777-777777777772"
D_MAIN = "77777777-7777-7777-7777-777777777773"

V1_KEYS = {"project", "floors", "markers", "cross_links", "ifc_models", "stats", "generated_at"}
ELEMENT_KINDS = {"columns", "walls", "beams", "slabs", "pipes", "equipment"}


@pytest.fixture
def fake_db():
    class _FakeDB:
        def __init__(self):
            self.fetch_one = AsyncMock(return_value=None)
            self.fetch_all = AsyncMock(return_value=[])

    return _FakeDB()


def _drawing(did: str, no: str, title: str) -> dict:
    return {
        "id": did, "drawing_no": no, "title": title, "discipline": "structure",
        "status": "ai_done", "current_stage": "technical_review",
        "file_key": "projects/p/d.pdf",
    }


def _issue(did: str) -> dict:
    return {
        "drawing_id": did, "issue_id": f"issue-{did[-1]}", "severity": "major",
        "description": "标高冲突", "discipline_code": "JG",
        "location_json": json.dumps({"levels": ["1层"], "axes": []}),
    }


def _arrange(fake_db, drawings: list, issues: list):
    fake_db.fetch_one.side_effect = [{"id": PROJECT_ID, "name": "上海大歌剧院"}, None]
    fake_db.fetch_all.side_effect = [drawings, issues] if drawings else [drawings]


def _fake_render(project_id, drawing_id, file_key, file_ext) -> dict:
    return {"image_key": f"k/{drawing_id}.png", "width": 100, "height": 100, "parser": "pdf"}


def _fake_elements() -> dict:
    return {
        "columns": [{"outline": [[0, 0], [0.6, 0], [0.6, 0.6], [0, 0.6]], "src": D_SOUTH}],
        "walls": [{"path": [[0, 0], [6, 0]], "width": 0.2, "src": D_SOUTH}],
        "beams": [], "slabs": [], "pipes": [], "equipment": [],
    }


async def _fake_build_floor_elements(executor, floor_drawings, file_getter):
    return _fake_elements(), 0, {"elevations": [0.0, 4.5], "registered": 0}


@pytest.mark.asyncio
async def test_scene_v2_buildings_and_elements(fake_db, monkeypatch):
    # Arrange
    monkeypatch.setattr(model_builder, "_render_and_upload_sync", _fake_render)
    monkeypatch.setattr(
        model_elements, "build_floor_elements", _fake_build_floor_elements
    )
    _arrange(fake_db, [
        _drawing(D_SOUTH, "S-1", "南区（大、中歌剧厅）一层墙柱结构平面图"),
        _drawing(D_NORTH, "S-2", "北区（小歌剧厅）一层墙柱结构平面图"),
        _drawing(D_MAIN, "S-3", "一层通用节点图"),
    ], [_issue(D_SOUTH)])

    # Act
    scene, _assets = await build_scene(fake_db, PROJECT_ID)

    # Assert：V1 keys 全保留 + V2 新增
    assert V1_KEYS <= set(scene)
    assert scene["schema_version"] == 2
    keys = {b["key"] for b in scene["buildings"]}
    assert {"south", "north", "main"} <= keys
    south = next(b for b in scene["buildings"] if b["key"] == "south")
    assert south["label"] == "南区"
    floor = south["floors"][0]
    assert ELEMENT_KINDS <= set(floor["elements"])
    assert floor["element_stats"]["columns"] == 1
    # 拍平 floors 兼容层也带 elements
    assert scene["floors"][0]["element_stats"]["walls"] == 1
    # 统计
    assert scene["stats"]["reconstruction"] == "elements"
    assert scene["stats"]["elements_total"]["columns"] >= 1
    assert scene["stats"]["buildings"] == 3


@pytest.mark.asyncio
async def test_scene_v2_markers_carry_building_key(fake_db, monkeypatch):
    monkeypatch.setattr(model_builder, "_render_and_upload_sync", _fake_render)
    monkeypatch.setattr(
        model_elements, "build_floor_elements", _fake_build_floor_elements
    )
    _arrange(fake_db, [_drawing(D_SOUTH, "S-1", "南区一层墙柱平面")], [_issue(D_SOUTH)])

    scene, _ = await build_scene(fake_db, PROJECT_ID)

    assert scene["markers"][0]["building_key"] == "south"


@pytest.mark.asyncio
async def test_scene_v2_falls_back_to_texture_on_element_failure(fake_db, monkeypatch):
    """构件识别整体失败 → elements 全空、reconstruction=texture、不抛异常。"""
    monkeypatch.setattr(model_builder, "_render_and_upload_sync", _fake_render)

    async def _boom(executor, floor_drawings, file_getter):
        raise RuntimeError("model3d 不可用")

    monkeypatch.setattr(model_elements, "build_floor_elements", _boom)
    _arrange(fake_db, [_drawing(D_MAIN, "S-3", "一层平面")], [])

    scene, _ = await build_scene(fake_db, PROJECT_ID)

    assert scene["stats"]["reconstruction"] == "texture"
    assert all(not v for v in scene["floors"][0]["elements"].values())


# ── model_elements 单元 ──────────────────────────────────────

@pytest.mark.unit
def test_building_of_patterns():
    assert model_elements.building_of({"title": "南区（大、中歌剧厅）梁图"})[0] == "south"
    assert model_elements.building_of({"title": "B2栋结构图"})[0] == "building_b2"
    assert model_elements.building_of({"title": "3#楼平面"})[0] == "building_3"
    assert model_elements.building_of({"title": "通用说明"})[0] == "main"


@pytest.mark.unit
def test_pick_element_drawings_classification():
    picked = model_elements.pick_element_drawings([
        {"title": "一层墙柱结构平面图", "discipline": "structure"},
        {"title": "一层主梁配筋图", "discipline": "structure"},
        {"title": "一层给排水平面图", "discipline": "mep"},
        {"title": "目录", "discipline": "architecture"},
    ])
    assert len(picked["structure"]) == 1
    assert len(picked["beam"]) == 1
    assert len(picked["mep"]) == 1


@pytest.mark.unit
def test_group_buildings_splits_elements_by_src():
    """多单体共享楼层时，构件按 src 来源图纸切分到所属单体，不重复归组"""
    floors = [{
        "key": "F1", "label": "1层", "elevation": 1, "order": 1,
        "drawings": [
            {"drawing_id": D_SOUTH, "drawing_no": "S-1"},
            {"drawing_id": D_NORTH, "drawing_no": "S-2"},
        ],
        "elements": {
            "columns": [
                {"outline": [[0, 0]], "src": D_SOUTH},
                {"outline": [[1, 1]], "src": D_NORTH},
                {"outline": [[2, 2]], "src": D_NORTH},
            ],
            "walls": [], "beams": [], "slabs": [], "pipes": [], "equipment": [],
        },
        "element_stats": {"columns": 3, "walls": 0, "beams": 0, "slabs": 0, "pipes": 0, "equipment": 0},
    }]
    drawings = [
        {"id": D_SOUTH, "title": "南区一层墙柱平面", "drawing_no": "S-1"},
        {"id": D_NORTH, "title": "北区一层墙柱平面", "drawing_no": "S-2"},
    ]
    buildings = model_elements.group_buildings(floors, drawings, "项目")
    south = next(b for b in buildings if b["key"] == "south")
    north = next(b for b in buildings if b["key"] == "north")
    assert south["floors"][0]["element_stats"]["columns"] == 1
    assert north["floors"][0]["element_stats"]["columns"] == 2


@pytest.mark.unit
def test_reconstruction_mode_mixed():
    floors = [
        {"elements": {"columns": [1], "walls": [], "beams": [], "slabs": [], "pipes": [], "equipment": []}},
        {"elements": {k: [] for k in ELEMENT_KINDS}},
    ]
    assert model_elements.reconstruction_mode(floors) == "mixed"


# ── V3：跨图轴号配准 + 真实标高 ──────────────────────────────────

@pytest.mark.unit
def test_register_offset_by_shared_axis_labels():
    """两图共有轴号 → 位置差中位数作为平移量"""
    ref = {"x": [["1", 0.0], ["2", 8.4], ["3", 16.8]], "y": [["A", 0.0], ["B", 4.2]]}
    cur = {"x": [["2", 0.0], ["3", 8.4]], "y": [["B", 0.0]]}
    dx, dy = model_elements.register_offset(ref, cur)
    assert dx == pytest.approx(8.4)
    assert dy == pytest.approx(4.2)


@pytest.mark.unit
def test_register_offset_without_shared_labels_is_zero():
    ref = {"x": [["1", 0.0]], "y": []}
    cur = {"x": [["9", 0.0]], "y": []}
    assert model_elements.register_offset(ref, cur) == (0.0, 0.0)


@pytest.mark.unit
def test_shift_elements_moves_all_coordinates():
    elements = {
        "columns": [{"outline": [[0, 0], [1, 0]], "src": "d"}],
        "pipes": [{"path": [[2, 2], [5, 2]], "dia": 0.1, "system": "电气", "src": "d"}],
        "walls": [], "beams": [], "slabs": [], "equipment": [],
    }
    shifted = model_elements._shift_elements(elements, 8.4, -4.2)
    assert shifted["columns"][0]["outline"][0] == [8.4, -4.2]
    assert shifted["pipes"][0]["path"][1] == [13.4, -2.2]


@pytest.mark.unit
def test_apply_real_elevations_greedy_monotonic():
    from services.model_builder import _apply_real_elevations

    floors = [
        {"key": "B2", "order": -2, "_elevation_candidates": [-9.3, 0.0]},
        {"key": "B1", "order": -1, "_elevation_candidates": [-9.3, -4.5, 0.0]},
        {"key": "F1", "order": 1, "_elevation_candidates": [0.0, 23.7]},
        {"key": "F2", "order": 2, "_elevation_candidates": []},
        {"key": "UNZONED", "order": 0, "_elevation_candidates": [0.0]},
    ]
    _apply_real_elevations(floors)
    by_key = {f["key"]: f["elevation_m"] for f in floors}
    assert by_key["B2"] == pytest.approx(-9.3)
    assert by_key["B1"] == pytest.approx(-4.5)
    assert by_key["F1"] == pytest.approx(0.0)
    assert by_key["F2"] is None
    assert by_key["UNZONED"] is None


@pytest.mark.unit
def test_apply_real_elevations_sign_constraint():
    """地下层只取 ≤0.5 候选、地上层只取 ≥-0.5 候选（防 ±0.000 抢占错层）"""
    from services.model_builder import _apply_real_elevations

    floors = [
        {"key": "B2", "order": -2, "_elevation_candidates": [0.0, 9.3]},   # 无负值候选
        {"key": "F1", "order": 1, "_elevation_candidates": [-9.3, 0.0]},   # 负值应被滤掉
    ]
    _apply_real_elevations(floors)
    by_key = {f["key"]: f["elevation_m"] for f in floors}
    assert by_key["B2"] == pytest.approx(0.0)   # 地下层允许 ±0.000（顶板）
    assert by_key["F1"] is None or by_key["F1"] >= -0.5
