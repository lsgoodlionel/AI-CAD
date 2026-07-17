"""A-19 · 集成测试：矢量图 → 构件识别 → 程序化 IFC → scene 端到端。

端到端串起 ``services.model_builder.build_scene``：在灰度开关
``model_ifc_enabled=True`` 且存在确定性构件时，验证 scene 升级出 ``model_ifc``
契约（``ifc_key`` / ``build_mode="ifc"`` / ``is_estimated``），并回填
``lod.supported_modes`` 含 ``ifc``。

取舍说明
────────
``build_scene`` 的输入经 ``_fetch_inputs`` 查 DB、构件经 ``_attach_floor_elements``
调 ``model_elements.build_floor_elements``。本测试用 FakeDB（fetch_one/fetch_all
side_effect）提供最小图纸输入，monkeypatch ``build_floor_elements`` 注入确定性柱构件，
monkeypatch 渲染（``_render_and_upload_sync``）避免真实 PDF/MinIO。

IFC 组装走**真实** ``ifcopenshell``（故 importorskip），仅 mock 掉对象存储上传
（``model_ifc_integration.upload_file``）与 Fragments 子进程转换
（``_convert_fragments_quiet``）——与 A-19 验收标准「mock MinIO/转换子进程」一致。
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

pytest.importorskip("ifcopenshell")

import services.model_builder as model_builder  # noqa: E402
from services import ifc_mapping, model_elements, model_ifc_integration  # noqa: E402
from services.model_builder import build_scene  # noqa: E402

PROJECT_ID = "22222222-2222-2222-2222-222222222222"
DRAWING_1 = "77777777-7777-7777-7777-777777777771"


# ── FakeDB 与最小输入 ─────────────────────────────────────────────


class _FakeDB:
    def __init__(self) -> None:
        self.execute = AsyncMock(return_value=None)
        self.fetch_one = AsyncMock(return_value=None)
        self.fetch_all = AsyncMock(return_value=[])
        self.fetch_val = AsyncMock(return_value=0)


@pytest.fixture
def fake_db() -> _FakeDB:
    return _FakeDB()


def _drawing() -> dict:
    return {
        "id": DRAWING_1,
        "drawing_no": "JG-3F-01",
        "title": "三层结构平面图",
        "discipline": "structure",
        "status": "ai_done",
        "current_stage": "technical_review",
        "file_key": "projects/p/d.pdf",
    }


def _arrange(fake_db: _FakeDB) -> None:
    """按 build_scene 查询顺序布置 FakeDB：项目 → 图纸 → (问题) → 跨图批次。"""
    fake_db.fetch_one.side_effect = [{"id": PROJECT_ID, "name": "测试项目"}, None]
    fake_db.fetch_all.side_effect = [[_drawing()], []]


def _fake_render(project_id, drawing_id, file_key, file_ext) -> dict:
    return {
        "image_key": f"projects/{project_id}/model_assets/{drawing_id}.png",
        "width": 1600,
        "height": 1131,
        "parser": file_ext,
    }


def _columns_elements():
    """注入确定性柱构件（带闭合方形轮廓，供程序化 IFC 拉伸）。"""
    async def _build(executor, floor_drawings, file_getter, *args, **kwargs):
        return (
            {
                "columns": [
                    {"outline": [[0, 0], [0.6, 0], [0.6, 0.6], [0, 0.6]], "src": DRAWING_1}
                ],
                "walls": [],
                "beams": [],
                "slabs": [],
                "pipes": [],
                "equipment": [],
            },
            0,
            {},
        )

    return _build


async def _empty_elements(executor, floor_drawings, file_getter, *args, **kwargs):
    """无构件（纯贴图路径）。"""
    return {k: [] for k in model_elements.EMPTY_ELEMENTS}, 0, {}


@pytest.fixture
def _mock_ifc_side_effects(monkeypatch):
    """mock 渲染 + MinIO 上传 + Fragments 转换（IFC 组装保持真实）。"""
    monkeypatch.setattr(model_builder, "_render_and_upload_sync", _fake_render)
    monkeypatch.setattr(model_ifc_integration, "upload_file", lambda *a, **k: a[1])
    monkeypatch.setattr(
        model_ifc_integration, "_convert_fragments_quiet",
        lambda ifc_bytes, project_id: f"projects/{project_id}/model_ifc/project.frag",
    )
    # VLM 灰度默认关闭（与现网一致）；显式确保不受其它测试串扰
    monkeypatch.setattr(model_builder.vlm_semantics.settings, "vlm_semantic_enabled", False)


# ── 端到端 ────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.asyncio
async def test_build_scene_emits_model_ifc_when_enabled(
    fake_db, monkeypatch, _mock_ifc_side_effects
):
    # Arrange：开关开启 + 确定性柱构件
    monkeypatch.setattr(model_ifc_integration.settings, "model_ifc_enabled", True)
    monkeypatch.setattr(model_elements, "build_floor_elements", _columns_elements())
    _arrange(fake_db)

    # Act
    scene, _assets = await build_scene(fake_db, PROJECT_ID)

    # Assert：scene 升级出合规 IFC 契约
    assert scene["stats"]["reconstruction"] != "texture"
    model_ifc = scene["model_ifc"]
    assert model_ifc["ifc_key"] == f"projects/{PROJECT_ID}/model_ifc/project.ifc"
    assert model_ifc["build_mode"] == "ifc"
    assert model_ifc["is_estimated"] is True
    assert model_ifc["frag_key"] == f"projects/{PROJECT_ID}/model_ifc/project.frag"
    assert "generated_at" in model_ifc
    # LOD 支持模式回填 ifc（置首）
    assert scene["lod"]["supported_modes"][0] == "ifc"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_build_scene_produces_real_ifc_bytes(
    fake_db, monkeypatch, _mock_ifc_side_effects
):
    # 捕获真实 IFC 字节（不 mock build_ifc_from_scene），验证组装链贯通
    captured: dict[str, bytes] = {}

    def _capture_upload(data, key, content_type):
        captured["bytes"] = data
        captured["key"] = key
        return key

    monkeypatch.setattr(model_ifc_integration.settings, "model_ifc_enabled", True)
    monkeypatch.setattr(model_elements, "build_floor_elements", _columns_elements())
    monkeypatch.setattr(model_ifc_integration, "upload_file", _capture_upload)
    _arrange(fake_db)

    scene, _assets = await build_scene(fake_db, PROJECT_ID)

    assert scene["model_ifc"]["ifc_key"] == captured["key"]
    # 真实 IFC4 STEP 文本头
    assert captured["bytes"].startswith(b"ISO-10303-21")
    import ifcopenshell

    reopened = ifcopenshell.file.from_string(captured["bytes"].decode("utf-8"))
    assert reopened.schema == "IFC4"
    assert len(reopened.by_type("IfcColumn")) >= 1
    # Phase A 估算标注：楼层挂 Pset_ModelProvenance
    assert reopened.by_type("IfcBuildingStorey")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_build_scene_no_model_ifc_when_disabled(
    fake_db, monkeypatch, _mock_ifc_side_effects
):
    # 开关关闭 → 行为回归现网：无 model_ifc，supported_modes 不含 ifc
    monkeypatch.setattr(model_ifc_integration.settings, "model_ifc_enabled", False)
    monkeypatch.setattr(model_elements, "build_floor_elements", _columns_elements())
    _arrange(fake_db)

    scene, _assets = await build_scene(fake_db, PROJECT_ID)

    assert "model_ifc" not in scene
    assert "ifc" not in scene["lod"]["supported_modes"]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_build_scene_no_model_ifc_for_texture_only(
    fake_db, monkeypatch, _mock_ifc_side_effects
):
    # 开关开启但无确定性构件（texture）→ 无可建模几何 → 不建 IFC
    monkeypatch.setattr(model_ifc_integration.settings, "model_ifc_enabled", True)
    monkeypatch.setattr(model_elements, "build_floor_elements", _empty_elements)
    _arrange(fake_db)

    scene, _assets = await build_scene(fake_db, PROJECT_ID)

    assert scene["stats"]["reconstruction"] == "texture"
    assert "model_ifc" not in scene
