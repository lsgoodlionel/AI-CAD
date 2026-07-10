"""工程模型 Router 测试（rebuild / 详情 / 404 / 资产 URL 越权）"""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.model_semantics import SemanticGraph, SemanticHierarchyError, SemanticVersionConflict

PROJECT_ID = "22222222-2222-2222-2222-222222222222"
OTHER_PROJECT_ID = "33333333-3333-3333-3333-333333333333"


# ── 重建模型 ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rebuild_upserts_and_triggers_build(client, fake_db):
    # Arrange：项目存在 + UPSERT 返回 version
    fake_db.fetch_one.side_effect = [{"id": PROJECT_ID}, {"version": 3}]
    delay = MagicMock()
    audit = AsyncMock()

    # Act
    with (
        patch("routers.project_models.build_project_model.delay", delay),
        patch("routers.project_models.write_audit", audit),
    ):
        resp = await client.post(f"/api/v1/projects/{PROJECT_ID}/model/rebuild")

    # Assert
    assert resp.status_code == 200
    assert resp.json() == {"project_id": PROJECT_ID, "status": "building", "version": 3}
    delay.assert_called_once_with(PROJECT_ID)
    audit.assert_awaited_once()
    assert audit.await_args.kwargs["action"] == "rebuild_project_model"


@pytest.mark.asyncio
async def test_rebuild_returns_404_for_unknown_project(client, fake_db):
    fake_db.fetch_one.side_effect = [None]

    with patch("routers.project_models.build_project_model.delay"):
        resp = await client.post(f"/api/v1/projects/{PROJECT_ID}/model/rebuild")

    assert resp.status_code == 404
    assert resp.json()["detail"] == "PROJECT_NOT_FOUND"


# ── 模型详情 ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_model_parses_scene_jsonb_string(client, fake_db):
    scene = {
        "project": {"id": PROJECT_ID, "name": "测试项目"},
        "floors": [],
        "quality": {
            "unclassified_drawings": [{"drawing_id": "d1", "title": "未分层图"}],
            "building_units": [{"unit_key": "south", "display_name": "南区"}],
        },
    }
    fake_db.fetch_one.return_value = {
        "status": "ready", "version": 2, "built_at": None,
        "error": None, "scene": json.dumps(scene, ensure_ascii=False),
    }

    resp = await client.get(f"/api/v1/projects/{PROJECT_ID}/model")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ready"
    assert data["version"] == 2
    assert data["scene"] == scene
    assert data["annotation_queue"][0]["drawing_id"] == "d1"
    assert data["building_units"]["detected"][0]["unit_key"] == "south"


@pytest.mark.asyncio
async def test_get_model_returns_404_when_never_built(client, fake_db):
    fake_db.fetch_one.return_value = None

    resp = await client.get(f"/api/v1/projects/{PROJECT_ID}/model")

    assert resp.status_code == 404
    assert resp.json()["detail"] == "MODEL_NOT_BUILT"


@pytest.mark.asyncio
async def test_get_annotation_queue_returns_dynamic_units(client, fake_db):
    fake_db.fetch_one.return_value = {"id": PROJECT_ID}
    fake_db.fetch_all.return_value = [
        {
            "id": "d1",
            "drawing_no": "S-0-20-102.01C",
            "title": "南区（大、中歌剧厅）一层结构平面总图",
            "discipline": "structure",
            "status": "uploaded",
            "current_stage": "uploaded",
            "file_key": "projects/p/南区.pdf",
        },
        {
            "id": "d2",
            "drawing_no": "S-3-20-001B",
            "title": "A、B、C区（上人屋面）总体布置图",
            "discipline": "structure",
            "status": "uploaded",
            "current_stage": "uploaded",
            "file_key": "projects/p/ABC区.pdf",
        },
    ]

    with patch("routers.project_models.model_annotations.load_annotation_overrides", AsyncMock(return_value={})):
        resp = await client.get(f"/api/v1/projects/{PROJECT_ID}/model/annotation-queue")

    assert resp.status_code == 200
    data = resp.json()
    unit_keys = {item["unit_key"] for item in data["building_units"]["detected"]}
    assert "south" in unit_keys
    assert data["quality"]["pending_manual_count"] >= 0


@pytest.mark.asyncio
async def test_save_model_annotation_calls_service(client, fake_db):
    fake_db.fetch_one.side_effect = [{"id": PROJECT_ID}, {"id": "d1"}]
    saved = {
        "project_id": PROJECT_ID,
        "drawing_id": "d1",
        "building_unit_key": "custom-unit",
        "building_unit_display_name": "自定义单体",
    }

    with patch(
        "routers.project_models.model_annotations.save_drawing_annotation",
        AsyncMock(return_value=saved),
    ) as save:
        resp = await client.post(
            f"/api/v1/projects/{PROJECT_ID}/model/annotations",
            json={
                "drawing_id": "d1",
                "building_unit_key": "custom-unit",
                "building_unit_name": "自定义单体",
                "story_key": "F1",
                "story_name": "一层",
                "drawing_type": "plan",
            },
        )

    assert resp.status_code == 200
    assert resp.json()["annotation"]["building_unit_key"] == "custom-unit"
    save.assert_awaited_once()


# ── 语义图谱与人工操作 ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_semantics_returns_tree_evidence_conflicts_and_unassigned(client, fake_db):
    fake_db.fetch_all.return_value = []
    graph = SemanticGraph(
        nodes=[],
        evidence=[],
        conflicts=[{"normalized_key": "a", "reason": "type_conflict"}],
        unassigned_drawings=[{"drawing_id": "d1", "reason": "semantic_unassigned"}],
        version=4,
    )

    with patch(
        "routers.project_models.model_semantics.build_semantic_graph",
        AsyncMock(return_value=graph),
    ) as build_graph:
        resp = await client.get(f"/api/v1/projects/{PROJECT_ID}/model/semantics")

    assert resp.status_code == 200
    data = resp.json()
    assert set(data) >= {"nodes", "evidence", "conflicts", "unassigned_drawings", "version"}
    assert data["version"] == 4
    assert data["unassigned_drawings"][0]["drawing_id"] == "d1"
    build_graph.assert_awaited_once()


@pytest.mark.asyncio
async def test_semantic_operation_returns_409_for_stale_version(client):
    conflict = SemanticVersionConflict({"id": "node-1", "version": 3})

    with patch(
        "routers.project_models.model_semantics.apply_semantic_operation",
        AsyncMock(side_effect=conflict),
    ):
        resp = await client.post(
            f"/api/v1/projects/{PROJECT_ID}/model/semantic-operations",
            json={
                "operation_type": "rename",
                "target_ids": ["node-1"],
                "canonical_name": "新名称",
                "expected_version": 1,
            },
        )

    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "SEMANTIC_VERSION_CONFLICT"
    assert resp.json()["detail"]["latest"]["version"] == 3


@pytest.mark.asyncio
async def test_semantic_operation_returns_422_for_invalid_hierarchy(client):
    with patch(
        "routers.project_models.model_semantics.apply_semantic_operation",
        AsyncMock(side_effect=SemanticHierarchyError("cycle rejected")),
    ):
        resp = await client.post(
            f"/api/v1/projects/{PROJECT_ID}/model/semantic-operations",
            json={
                "operation_type": "reparent",
                "target_ids": ["child"],
                "parent_id": "child",
                "expected_version": 1,
            },
        )

    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "INVALID_SEMANTIC_HIERARCHY"


@pytest.mark.asyncio
async def test_rebuild_impact_returns_affected_scope(client):
    resp = await client.get(
        f"/api/v1/projects/{PROJECT_ID}/model/rebuild-impact",
        params={"node_id": "node-1", "drawing_id": "drawing-1"},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["rebuild_required"] is True
    assert data["affected_nodes"] == ["node-1"]
    assert data["affected_drawings"] == ["drawing-1"]


# ── 资产签名 URL ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_asset_url_returns_presigned_url(client):
    key = f"projects/{PROJECT_ID}/model_assets/abc.png"
    presign = MagicMock(return_value="http://minio.local/signed")

    with patch("routers.project_models.presigned_get_url", presign):
        resp = await client.get(
            f"/api/v1/projects/{PROJECT_ID}/model/asset-url", params={"key": key}
        )

    assert resp.status_code == 200
    assert resp.json() == {"url": "http://minio.local/signed"}
    presign.assert_called_once_with(key, expires_seconds=300)


@pytest.mark.asyncio
async def test_asset_url_rejects_key_of_other_project(client):
    key = f"projects/{OTHER_PROJECT_ID}/model_assets/abc.png"

    resp = await client.get(
        f"/api/v1/projects/{PROJECT_ID}/model/asset-url", params={"key": key}
    )

    assert resp.status_code == 403
    assert resp.json()["detail"] == "ASSET_FORBIDDEN"


@pytest.mark.asyncio
async def test_asset_url_rejects_non_model_asset_prefix(client):
    key = f"projects/{PROJECT_ID}/drawings/abc.pdf"

    resp = await client.get(
        f"/api/v1/projects/{PROJECT_ID}/model/asset-url", params={"key": key}
    )

    assert resp.status_code == 403
    assert resp.json()["detail"] == "ASSET_FORBIDDEN"


# ── QTO 工程量汇总（B-19）────────────────────────────────────

@pytest.mark.asyncio
async def test_get_model_quantities_returns_envelope_with_drilldown(client, fake_db):
    scene = {
        "floors": [{
            "key": "F1", "label": "1层", "building_units": ["main"],
            "elements": {
                "beams": [{"path": [[0, 0], [6, 0]], "width": 0.3, "depth": 0.6}],
                "slabs": [{"outline": [[0, 0], [6, 0], [6, 6], [0, 6]], "thickness": 0.12}],
                "columns": [], "walls": [],
            },
        }],
        "quality": {"story_tables": {"main": [{"story_key": "F1", "height_m": 3.0}]}},
    }
    fake_db.fetch_one.return_value = {"scene": json.dumps(scene, ensure_ascii=False)}

    resp = await client.get(f"/api/v1/projects/{PROJECT_ID}/model/quantities")

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["error"] is None
    assert body["meta"]["scope"] == "scene"
    assert body["data"]["project"]["concrete"]["net_m3"] > 0
    assert body["data"]["by_floor"][0]["floor_key"] == "F1"
    assert body["data"]["project"]["rebar"]["missing"] is True


@pytest.mark.asyncio
async def test_get_model_quantities_404_when_not_built(client, fake_db):
    fake_db.fetch_one.return_value = None
    resp = await client.get(f"/api/v1/projects/{PROJECT_ID}/model/quantities")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "MODEL_NOT_BUILT"


# ── B-20 QTO → 创效提案草稿 ──────────────────────────────────

def _qto_scene():
    return {
        "floors": [{
            "key": "F1", "label": "1层", "building_units": ["main"],
            "elements": {
                "beams": [{"path": [[0, 0], [6, 0]], "width": 0.3, "depth": 0.6}],
                "slabs": [], "columns": [], "walls": [],
            },
        }],
        "quality": {"story_tables": {"main": [{"story_key": "F1", "height_m": 3.0}]}},
    }


@pytest.mark.asyncio
async def test_qto_to_proposal_creates_draft_with_positive_saving(client, fake_db):
    fake_db.fetch_one.side_effect = [
        {"scene": json.dumps(_qto_scene(), ensure_ascii=False)},   # 载入 scene
        {"id": "44444444-4444-4444-4444-444444444444"},            # INSERT RETURNING id
    ]
    rebar_inputs = [{"diameter": 20, "steel_grade": "HRB400", "required_length": 6000, "count": 40}]

    with patch("routers.project_models.write_audit", AsyncMock()) as audit:
        resp = await client.post(
            f"/api/v1/projects/{PROJECT_ID}/model/quantities/to-proposal",
            json={"rebar_inputs": rebar_inputs},
        )

    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "draft"                # 只造草稿，入正常流程
    assert body["raw_saving_est"] > 0
    audit.assert_awaited_once()
    assert audit.await_args.kwargs["action"] == "qto_to_proposal"


@pytest.mark.asyncio
async def test_qto_to_proposal_rejects_when_no_saving(client, fake_db):
    fake_db.fetch_one.return_value = {"scene": json.dumps(_qto_scene(), ensure_ascii=False)}
    with patch("routers.project_models.write_audit", AsyncMock()):
        resp = await client.post(
            f"/api/v1/projects/{PROJECT_ID}/model/quantities/to-proposal",
            json={"rebar_inputs": []},   # 无配筋 → 无优化节约
        )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "NO_POSITIVE_SAVING"


@pytest.mark.asyncio
async def test_qto_to_proposal_404_when_not_built(client, fake_db):
    fake_db.fetch_one.return_value = None
    resp = await client.post(
        f"/api/v1/projects/{PROJECT_ID}/model/quantities/to-proposal",
        json={"rebar_inputs": []},
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "MODEL_NOT_BUILT"
