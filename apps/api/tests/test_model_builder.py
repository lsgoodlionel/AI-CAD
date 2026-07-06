"""模型基座场景构建测试（scene 契约 / 渲染降级 / 坐标稳定性 / 构建任务）"""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import services.model_builder as model_builder
from services.model_builder import build_scene
from tasks.batch_review import _maybe_trigger_model_build
from tasks.model_build import _do_build, _mark_failed, build_project_model

PROJECT_ID = "22222222-2222-2222-2222-222222222222"
DRAWING_1 = "77777777-7777-7777-7777-777777777771"
DRAWING_2 = "77777777-7777-7777-7777-777777777772"
ISSUE_1 = "88888888-8888-8888-8888-888888888881"
ISSUE_2 = "88888888-8888-8888-8888-888888888882"

SCENE_KEYS = {"project", "floors", "markers", "cross_links", "ifc_models", "stats", "generated_at"}
DRAWING_ENTRY_KEYS = {
    "drawing_id", "drawing_no", "title", "discipline", "status",
    "current_stage", "image_key", "issue_count", "critical_count",
}
MARKER_KEYS = {"id", "type", "severity", "floor_key", "x", "y", "title", "discipline_code", "ref"}


def _drawing(did: str, no: str, title: str, file_key: str = "projects/p/d.pdf") -> dict:
    return {
        "id": did, "drawing_no": no, "title": title, "discipline": "structure",
        "status": "ai_done", "current_stage": "technical_review", "file_key": file_key,
    }


def _issue(iid: str, did: str, severity: str = "critical",
           levels: list | None = None, axes: list | None = None) -> dict:
    return {
        "drawing_id": did, "issue_id": iid, "severity": severity,
        "description": "钢筋锚固长度不足" * 20,
        "discipline_code": "JG",
        "location_json": json.dumps({"levels": levels or [], "axes": axes or []}),
    }


def _arrange(fake_db, drawings: list, issues: list, cross_row: dict | None = None):
    """按 build_scene 查询顺序布置 FakeDB：项目 → 图纸 → 问题 → 跨图批次。"""
    fake_db.fetch_one.side_effect = [{"id": PROJECT_ID, "name": "测试项目"}, cross_row]
    fake_db.fetch_all.side_effect = [drawings, issues] if drawings else [drawings]


def _fake_render(project_id, drawing_id, file_key, file_ext) -> dict:
    return {
        "image_key": f"projects/{project_id}/model_assets/{drawing_id}.png",
        "width": 1600, "height": 1131, "parser": file_ext,
    }


# ── scene 契约字段齐全 ────────────────────────────────────────

@pytest.mark.asyncio
async def test_build_scene_contract_fields(fake_db, monkeypatch):
    # Arrange
    monkeypatch.setattr(model_builder, "_render_and_upload_sync", _fake_render)
    drawings = [
        _drawing(DRAWING_1, "JG-B2-01", "地下二层结构平面图"),
        _drawing(DRAWING_2, "JG-3F-01", "三层结构平面图"),
    ]
    issues = [_issue(ISSUE_1, DRAWING_1, levels=["B2"], axes=["A-3轴"])]
    _arrange(fake_db, drawings, issues)

    # Act
    scene, assets = await build_scene(fake_db, PROJECT_ID)

    # Assert：顶层契约
    assert set(scene.keys()) == SCENE_KEYS
    assert scene["project"] == {"id": PROJECT_ID, "name": "测试项目"}

    # 楼层：B2(-2) 排在 F3(3) 前
    assert [f["key"] for f in scene["floors"]] == ["B2", "F3"]
    floor_b2 = scene["floors"][0]
    assert floor_b2["label"] == "地下二层"
    assert floor_b2["elevation"] == -2 and floor_b2["order"] == -2
    entry = floor_b2["drawings"][0]
    assert set(entry.keys()) == DRAWING_ENTRY_KEYS
    assert entry["image_key"] == f"projects/{PROJECT_ID}/model_assets/{DRAWING_1}.png"
    assert entry["issue_count"] == 1 and entry["critical_count"] == 1

    # 标记：坐标范围 + 标题截断 80 字
    marker = scene["markers"][0]
    assert set(marker.keys()) == MARKER_KEYS
    assert marker["id"] == f"issue:{ISSUE_1}" and marker["type"] == "issue"
    assert marker["floor_key"] == "B2"
    assert 0.1 <= marker["x"] <= 0.9 and 0.1 <= marker["y"] <= 0.9
    assert len(marker["title"]) == 80
    assert marker["ref"] == {"drawing_id": DRAWING_1, "issue_id": ISSUE_1}

    # 统计与资产
    assert scene["stats"]["total_drawings"] == 2
    assert scene["stats"]["total_issues"] == 1
    assert scene["stats"]["by_severity"]["critical"] == 1
    assert scene["stats"]["floors"] == 2
    assert "ifc_skipped" not in scene["stats"]
    assert assets[DRAWING_1]["parser"] == "pdf"


# ── 渲染失败降级 ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_render_failure_degrades_to_empty_image_key(fake_db, monkeypatch):
    def _boom(*args):
        raise RuntimeError("render exploded")

    monkeypatch.setattr(model_builder, "_render_and_upload_sync", _boom)
    _arrange(fake_db, [_drawing(DRAWING_1, "JG-B2-01", "地下二层结构平面图")], [])

    scene, assets = await build_scene(fake_db, PROJECT_ID)

    assert assets[DRAWING_1]["image_key"] == ""
    assert scene["floors"][0]["drawings"][0]["image_key"] == ""


@pytest.mark.asyncio
async def test_missing_file_key_skips_render(fake_db, monkeypatch):
    def _boom(*args):
        raise AssertionError("不应触发渲染")

    monkeypatch.setattr(model_builder, "_render_and_upload_sync", _boom)
    _arrange(fake_db, [_drawing(DRAWING_1, "JG-01", "总说明", file_key="")], [])

    scene, assets = await build_scene(fake_db, PROJECT_ID)

    assert assets[DRAWING_1] == {"image_key": "", "width": 0, "height": 0, "parser": "none"}
    assert scene["floors"][0]["key"] == "UNZONED"


# ── IFC 缺依赖降级 ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_ifc_import_error_sets_ifc_skipped(fake_db, monkeypatch):
    def _no_ifc(*args):
        raise ImportError("no ifcopenshell")

    monkeypatch.setattr(model_builder, "_ifc_to_glb_sync", _no_ifc)
    _arrange(fake_db, [_drawing(DRAWING_1, "JG-01", "三层平面图", file_key="p/m.ifc")], [])

    scene, assets = await build_scene(fake_db, PROJECT_ID)

    assert scene["stats"]["ifc_skipped"] is True
    assert scene["ifc_models"] == []
    assert assets[DRAWING_1]["parser"] == "ifc"


# ── 坐标稳定性 ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_marker_coordinates_stable_across_rebuilds(fake_db, monkeypatch):
    monkeypatch.setattr(model_builder, "_render_and_upload_sync", _fake_render)

    def _inputs():
        return (
            [_drawing(DRAWING_1, "JG-B2-01", "地下二层结构平面图")],
            [_issue(ISSUE_1, DRAWING_1, levels=["B2"], axes=["A-3轴"]),
             _issue(ISSUE_2, DRAWING_1, levels=["B2"], axes=[])],
        )

    _arrange(fake_db, *_inputs())
    scene_first, _ = await build_scene(fake_db, PROJECT_ID)
    _arrange(fake_db, *_inputs())
    scene_second, _ = await build_scene(fake_db, PROJECT_ID)

    coords_first = [(m["x"], m["y"]) for m in scene_first["markers"]]
    coords_second = [(m["x"], m["y"]) for m in scene_second["markers"]]
    assert coords_first == coords_second


@pytest.mark.asyncio
async def test_same_axes_cluster_offsets_by_step(fake_db, monkeypatch):
    monkeypatch.setattr(model_builder, "_render_and_upload_sync", _fake_render)
    issues = [
        _issue(ISSUE_1, DRAWING_1, levels=["B2"], axes=["A-3轴"]),
        _issue(ISSUE_2, DRAWING_1, levels=["B2"], axes=["A-3轴"]),
    ]
    _arrange(fake_db, [_drawing(DRAWING_1, "JG-B2-01", "地下二层结构平面图")], issues)

    scene, _ = await build_scene(fake_db, PROJECT_ID)

    first, second = scene["markers"]
    assert second["x"] == pytest.approx(min(first["x"] + 0.02, 0.9), abs=1e-4)
    assert second["y"] == pytest.approx(min(first["y"] + 0.02, 0.9), abs=1e-4)


# ── 跨图发现 → cross_links ───────────────────────────────────

@pytest.mark.asyncio
async def test_cross_findings_mapped_to_cross_links(fake_db, monkeypatch):
    monkeypatch.setattr(model_builder, "_render_and_upload_sync", _fake_render)
    cross = {
        "重复图号": [{"drawing_no": "JG-B2-01", "drawing_ids": [DRAWING_1, DRAWING_2]}],
        "版本冲突": [{"drawing_no": "JG-B2-01", "versions": ["A", "B"]}],
        "接口缺图": [{"missing_discipline": "mep",
                      "referenced_by": [{"drawing_no": "JG-B2-01", "interface": "给排水"}]}],
        "问题聚类": [{"location_key": "B2@A-3轴", "count": 2,
                      "drawings": ["JG-B2-01"], "disciplines": ["structure"]}],
    }
    _arrange(
        fake_db,
        [_drawing(DRAWING_1, "JG-B2-01", "地下二层结构平面图")],
        [],
        cross_row={"cross_findings": json.dumps(cross, ensure_ascii=False)},
    )

    scene, _ = await build_scene(fake_db, PROJECT_ID)

    kinds = [link["kind"] for link in scene["cross_links"]]
    assert sorted(kinds) == sorted(["重复图号", "版本冲突", "接口缺图", "问题聚类"])
    dup = next(link for link in scene["cross_links"] if link["kind"] == "重复图号")
    assert dup["drawing_ids"] == [DRAWING_1, DRAWING_2]
    assert dup["floor_keys"] == ["B2"]
    cluster = next(link for link in scene["cross_links"] if link["kind"] == "问题聚类")
    assert cluster["label"] == "B2@A-3轴"
    assert cluster["drawing_ids"] == [DRAWING_1]


# ── 项目不存在 ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_build_scene_raises_for_unknown_project(fake_db):
    fake_db.fetch_one.side_effect = [None]

    with pytest.raises(ValueError):
        await build_scene(fake_db, PROJECT_ID)


# ── 渲染上传（同步单元）──────────────────────────────────────

def test_render_and_upload_sync_pdf_path(monkeypatch):
    uploaded: dict = {}

    def _capture_upload(data, key, content_type):
        uploaded.update({"key": key, "content_type": content_type})
        return key

    monkeypatch.setattr(model_builder, "get_file_bytes", lambda key: b"%PDF")
    monkeypatch.setattr(model_builder, "_render_pdf_sync", lambda data: (b"png", 1600, 1131))
    monkeypatch.setattr(model_builder, "upload_file", _capture_upload)

    asset = model_builder._render_and_upload_sync(PROJECT_ID, DRAWING_1, "p/d.pdf", "pdf")

    assert asset == {
        "image_key": f"projects/{PROJECT_ID}/model_assets/{DRAWING_1}.png",
        "width": 1600, "height": 1131, "parser": "pdf",
    }
    assert uploaded["content_type"] == "image/png"


def test_render_and_upload_sync_dwg_conversion_warning_raises(monkeypatch):
    monkeypatch.setattr(model_builder, "get_file_bytes", lambda key: b"AC1032")
    monkeypatch.setattr(
        model_builder, "ensure_dxf", lambda data, ext: (data, "dwg", "ODA 未配置")
    )

    with pytest.raises(RuntimeError, match="ODA"):
        model_builder._render_and_upload_sync(PROJECT_ID, DRAWING_1, "p/d.dwg", "dwg")


def test_render_and_upload_sync_rejects_unknown_ext(monkeypatch):
    monkeypatch.setattr(model_builder, "get_file_bytes", lambda key: b"data")

    with pytest.raises(ValueError, match="UNSUPPORTED_RENDER_EXT"):
        model_builder._render_and_upload_sync(PROJECT_ID, DRAWING_1, "p/d.docx", "docx")


def test_unlink_quiet_ignores_missing_files(tmp_path):
    existing = tmp_path / "a.png"
    existing.write_bytes(b"x")

    model_builder._unlink_quiet(str(existing), str(tmp_path / "missing.png"))

    assert not existing.exists()


# ── Celery 构建任务 ──────────────────────────────────────────

def _fake_databases_module(db: AsyncMock) -> MagicMock:
    module = MagicMock()
    module.Database.return_value = db
    return module


@pytest.mark.asyncio
async def test_do_build_marks_ready_and_bumps_version():
    db = AsyncMock()
    db.fetch_one.return_value = {"version": 4}
    scene = {"stats": {"total_drawings": 0}}

    with (
        patch("tasks.model_build.databases", _fake_databases_module(db)),
        patch("tasks.model_build.build_scene", AsyncMock(return_value=(scene, {}))),
    ):
        result = await _do_build(PROJECT_ID)

    assert result == {"project_id": PROJECT_ID, "status": "ready", "version": 4}
    update_sql = db.fetch_one.await_args.args[0]
    assert "status='ready'" in update_sql and "version=version+1" in update_sql
    db.disconnect.assert_awaited_once()


@pytest.mark.asyncio
async def test_mark_failed_truncates_error_to_500():
    db = AsyncMock()

    with patch("tasks.model_build.databases", _fake_databases_module(db)):
        await _mark_failed(PROJECT_ID, "炸" * 600)

    params = db.execute.await_args.args[1]
    assert len(params["error"]) == 500
    db.disconnect.assert_awaited_once()


def test_task_marks_failed_and_retries_on_error():
    retry_mock = MagicMock(side_effect=RuntimeError("RETRY_CALLED"))
    mark_failed = AsyncMock()

    with (
        patch("tasks.model_build._do_build", AsyncMock(side_effect=ValueError("boom"))),
        patch("tasks.model_build._mark_failed", mark_failed),
        patch.object(build_project_model, "retry", retry_mock),
        pytest.raises(RuntimeError, match="RETRY_CALLED"),
    ):
        build_project_model(PROJECT_ID)

    mark_failed.assert_awaited_once()
    retry_mock.assert_called_once()


def test_task_returns_result_when_build_succeeds():
    async def _fake(_project_id: str) -> dict:
        return {"project_id": PROJECT_ID, "status": "ready", "version": 1}

    with patch("tasks.model_build._do_build", _fake):
        result = build_project_model(PROJECT_ID)

    assert result["status"] == "ready"


# ── 套图汇总后自动触发钩子 ───────────────────────────────────

@pytest.mark.asyncio
async def test_batch_hook_triggers_when_model_exists():
    db = AsyncMock()
    db.fetch_one.return_value = {"project_id": PROJECT_ID}
    delay = MagicMock()

    with patch("tasks.model_build.build_project_model.delay", delay):
        await _maybe_trigger_model_build(db, "batch-1")

    delay.assert_called_once_with(PROJECT_ID)


@pytest.mark.asyncio
async def test_batch_hook_skips_when_no_model_record():
    db = AsyncMock()
    db.fetch_one.return_value = None
    delay = MagicMock()

    with patch("tasks.model_build.build_project_model.delay", delay):
        await _maybe_trigger_model_build(db, "batch-1")

    delay.assert_not_called()


@pytest.mark.asyncio
async def test_batch_hook_swallows_exceptions():
    db = AsyncMock()
    db.fetch_one.side_effect = RuntimeError("db down")

    # 不抛出即通过（钩子失败不得影响批次状态）
    await _maybe_trigger_model_build(db, "batch-1")
