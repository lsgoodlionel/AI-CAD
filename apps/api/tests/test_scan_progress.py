"""
GET /projects/{id}/info/scan-progress 单测(Phase F2)
"""
import pytest

API = "/api/v1/projects/p-0001/info/scan-progress"


@pytest.mark.asyncio
async def test_scan_progress_overall_and_rows(client, fake_db):
    fake_db.fetch_one.return_value = {
        "total": 100, "ready": 40, "extracting": 5, "pending": 55,
    }
    fake_db.fetch_all.return_value = [
        {"drawing_id": "d1", "drawing_no": "S-1", "title": "结构平面",
         "discipline": "structure", "status": "ready", "item_count": 356,
         "extractors_done": '["vector_text","ocr","vlm"]',
         "summary": '{"by_extractor":{"ocr":300,"vlm":6},"by_category":{"elevation":15},"samples":[]}',
         "updated_at": "2026-07-16T00:00:00+00:00"},
    ]

    resp = await client.get(API)

    assert resp.status_code == 200
    body = resp.json()
    assert body["overall"]["total"] == 100
    assert body["overall"]["ready"] == 40
    assert body["overall"]["percent"] == 40
    row = body["drawings"][0]
    assert row["status"] == "ready"
    assert row["extractors_done"] == ["vector_text", "ocr", "vlm"]
    assert row["summary"]["by_extractor"]["vlm"] == 6


@pytest.mark.asyncio
async def test_scan_progress_status_filter(client, fake_db):
    fake_db.fetch_one.return_value = {"total": 10, "ready": 2, "extracting": 1, "pending": 7}
    fake_db.fetch_all.return_value = []

    resp = await client.get(API, params={"status": "extracting"})

    assert resp.status_code == 200
    sql = fake_db.fetch_all.call_args.args[0]
    assert "status" in sql


@pytest.mark.asyncio
async def test_scan_progress_empty_project(client, fake_db):
    fake_db.fetch_one.return_value = None
    fake_db.fetch_all.return_value = []

    resp = await client.get(API)

    assert resp.status_code == 200
    assert resp.json()["overall"]["total"] == 0
