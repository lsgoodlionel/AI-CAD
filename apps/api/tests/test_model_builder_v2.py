"""scene V2 组装测试（schema_version=2：单体分组 + 构件层 + 回退）"""
import json

import pytest

import services.model_builder as model_builder
import services.model_elements as model_elements
from services.model_builder import build_scene

PROJECT_ID = "22222222-2222-2222-2222-222222222222"
D_SOUTH = "77777777-7777-7777-7777-777777777771"
D_NORTH = "77777777-7777-7777-7777-777777777772"
D_MAIN = "77777777-7777-7777-7777-777777777773"

V1_KEYS = {"project", "floors", "markers", "cross_links", "ifc_models", "stats", "generated_at"}
ELEMENT_KINDS = {"columns", "walls", "beams", "slabs", "pipes", "equipment"}


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
    return _fake_elements(), 0


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
    assert model_elements.building_of({"title": "B2栋结构图"})[0] == "building_B2"
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
def test_reconstruction_mode_mixed():
    floors = [
        {"elements": {"columns": [1], "walls": [], "beams": [], "slabs": [], "pipes": [], "equipment": []}},
        {"elements": {k: [] for k in ELEMENT_KINDS}},
    ]
    assert model_elements.reconstruction_mode(floors) == "mixed"
