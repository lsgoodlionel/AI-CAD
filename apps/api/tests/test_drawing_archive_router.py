"""
routers/drawing_archive.py 单测 — 档案读取契约 + 人审 verify(Phase E1.5-3)
"""
from unittest.mock import patch

import pytest


@pytest.mark.asyncio
async def test_get_drawing_archive_returns_effective_values(client, fake_db):
    fake_db.fetch_all.return_value = [
        {"id": "a", "drawing_id": "d1", "category": "elevation", "content": "-2.350",
         "value_json": '{"elevation_m": -2.35}', "location_json": None,
         "extractor": "ocr", "confidence": 0.9, "source_kind": "auto", "is_active": True},
    ]
    fake_db.fetch_one.return_value = {"status": "ready", "item_count": 1}

    resp = await client.get("/api/v1/drawings/d1/archive")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert body["items"][0]["category"] == "elevation"
    assert body["items"][0]["value_json"] == {"elevation_m": -2.35}


@pytest.mark.asyncio
async def test_project_elevations_effective_only(client, fake_db):
    fake_db.fetch_all.return_value = [
        {"id": "a", "drawing_id": "d1", "category": "elevation", "content": "-2.350",
         "value_json": '{"elevation_m": -2.35}', "location_json": None,
         "extractor": "ocr", "confidence": 0.9, "source_kind": "auto", "is_active": True,
         "drawing_no": "S-1", "drawing_title": "剖面", "discipline": "structure"},
    ]
    resp = await client.get("/api/v1/projects/p1/archive/elevations")

    assert resp.status_code == 200
    body = resp.json()
    assert body["elevations"][0]["value_json"]["elevation_m"] == -2.35
    sql = fake_db.fetch_all.call_args.args[0]
    assert "category" in sql  # 按 elevation 过滤


@pytest.mark.asyncio
async def test_verify_writes_and_emits_event(client, fake_db):
    # 两次 fetch_one:①drawing 项目 ②被修正的原 auto 行原值
    fake_db.fetch_one.side_effect = [
        {"project_id": "p1"},
        {"content": "-2.350", "value_json": '{"elevation_m": -2.35}'},
    ]

    with patch("routers.drawing_archive.emit_event") as emit:
        async def _noop(*a, **k):
            return "evt-1"
        emit.side_effect = _noop
        resp = await client.post("/api/v1/drawings/d1/archive/verify", json={
            "category": "elevation",
            "content": "-2.400",
            "value_json": {"elevation_m": -2.40},
            "supersedes_id": "auto-1",
        })

    assert resp.status_code == 200
    # 落库(置失活+插verified)与事件各触发
    sqls = [c.args[0] for c in fake_db.execute.call_args_list]
    assert any("INSERT INTO drawing_extracted_info" in s for s in sqls)
    emit.assert_called_once()
    assert emit.call_args.kwargs["event_type"] == "archive.verified"


@pytest.mark.asyncio
async def test_verify_404_when_drawing_missing(client, fake_db):
    fake_db.fetch_one.return_value = None

    resp = await client.post("/api/v1/drawings/dX/archive/verify", json={
        "category": "elevation", "content": "-2.4", "value_json": {"elevation_m": -2.4},
    })
    assert resp.status_code == 404
