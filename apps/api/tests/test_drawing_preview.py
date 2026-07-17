"""
GET /drawings/{id}/preview 单测 — 图纸统一预览(Phase E1-4)

PDF/图片直接给 presigned;DXF/DWG 复用模型贴图资产(model_assets PNG),
miss 时按需渲染并写回同 key(与建模互为缓存)。
"""
from unittest.mock import patch

import pytest


API = "/api/v1/drawings/d-0001/preview"


def _drawing_row(file_key: str):
    return {"id": "d-0001", "project_id": "p-0001", "file_key": file_key}


@pytest.mark.asyncio
async def test_pdf_preview_returns_presigned_pdf(client, fake_db):
    fake_db.fetch_one.return_value = _drawing_row("projects/p/drawings/a.pdf")

    with patch("routers.drawings.presigned_get_url", return_value="https://x/signed") as p:
        resp = await client.get(API)

    assert resp.status_code == 200
    body = resp.json()
    assert body["kind"] == "pdf"
    assert body["url"] == "https://x/signed"
    p.assert_called_once()
    assert p.call_args.args[0] == "projects/p/drawings/a.pdf"


@pytest.mark.asyncio
async def test_image_file_preview_returns_image_kind(client, fake_db):
    fake_db.fetch_one.return_value = _drawing_row("projects/p/drawings/scan.png")

    with patch("routers.drawings.presigned_get_url", return_value="https://x/img"):
        resp = await client.get(API)

    assert resp.status_code == 200
    assert resp.json()["kind"] == "image"


@pytest.mark.asyncio
async def test_dxf_preview_uses_cached_model_asset(client, fake_db):
    fake_db.fetch_one.return_value = _drawing_row("projects/p/drawings/plan.dxf")

    with patch("routers.drawings.object_exists", return_value=True) as exists, \
         patch("routers.drawings.presigned_get_url", return_value="https://x/png") as p, \
         patch("routers.drawings._render_preview_asset") as render:
        resp = await client.get(API)

    assert resp.status_code == 200
    body = resp.json()
    assert body["kind"] == "image"
    # 命中缓存:presigned 的是 model_assets key,且不触发渲染
    assert p.call_args.args[0] == "projects/p-0001/model_assets/d-0001.png"
    render.assert_not_called()
    exists.assert_called_once()


@pytest.mark.asyncio
async def test_dxf_preview_renders_on_cache_miss(client, fake_db):
    fake_db.fetch_one.return_value = _drawing_row("projects/p/drawings/plan.dxf")

    async def _fake_render(*args, **kwargs):
        return None

    with patch("routers.drawings.object_exists", return_value=False), \
         patch("routers.drawings.presigned_get_url", return_value="https://x/png"), \
         patch("routers.drawings._render_preview_asset", side_effect=_fake_render) as render:
        resp = await client.get(API)

    assert resp.status_code == 200
    render.assert_called_once()


@pytest.mark.asyncio
async def test_dxf_preview_render_failure_returns_422(client, fake_db):
    fake_db.fetch_one.return_value = _drawing_row("projects/p/drawings/plan.dxf")

    async def _boom(*args, **kwargs):
        raise RuntimeError("RENDER_SKIPPED_TOO_LARGE:60MB")

    with patch("routers.drawings.object_exists", return_value=False), \
         patch("routers.drawings._render_preview_asset", side_effect=_boom):
        resp = await client.get(API)

    assert resp.status_code == 422
    assert "PREVIEW_UNAVAILABLE" in resp.text


@pytest.mark.asyncio
async def test_preview_404_when_no_file(client, fake_db):
    fake_db.fetch_one.return_value = None

    resp = await client.get(API)

    assert resp.status_code == 404
