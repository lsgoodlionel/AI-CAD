"""工程模型 Router 测试（rebuild / 详情 / 404 / 资产 URL 越权）"""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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
    scene = {"project": {"id": PROJECT_ID, "name": "测试项目"}, "floors": []}
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


@pytest.mark.asyncio
async def test_get_model_returns_404_when_never_built(client, fake_db):
    fake_db.fetch_one.return_value = None

    resp = await client.get(f"/api/v1/projects/{PROJECT_ID}/model")

    assert resp.status_code == 404
    assert resp.json()["detail"] == "MODEL_NOT_BUILT"


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
