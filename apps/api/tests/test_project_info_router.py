"""
routers/project_info.py 单测 — 工程信息聚合 API(Phase E1-2)

FakeDB 驱动,不需真库;Celery 触发用 patch 断言入队而不真跑。
"""
from unittest.mock import MagicMock, patch

import pytest


API = "/api/v1/projects/p-0001/info"


def _row(**kw):
    """模拟 databases 的 Record(dict 即可,router 用 dict() 转换)。"""
    base = {
        "id": "i-1", "drawing_id": "d-1", "category": "elevation",
        "content": "-2.350", "value_json": '{"elevation_m": -2.35}',
        "location_json": '{"x": 1.0, "y": 2.0}', "extractor": "vector_text",
        "confidence": None, "extraction_version": 1,
        "drawing_no": "结施-05", "drawing_title": "剖面图", "discipline": "structure",
    }
    base.update(kw)
    return base


# ── summary ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_summary_returns_counts_and_coverage(client, fake_db):
    fake_db.fetch_all.return_value = [
        {"category": "elevation", "cnt": 13},
        {"category": "axis", "cnt": 40},
    ]
    fake_db.fetch_one.return_value = {"total_drawings": 100, "extracted_drawings": 60}

    resp = await client.get(f"{API}/summary")

    assert resp.status_code == 200
    body = resp.json()
    assert body["categories"] == [
        {"category": "elevation", "count": 13},
        {"category": "axis", "count": 40},
    ]
    assert body["coverage"]["total_drawings"] == 100
    assert body["coverage"]["extracted_drawings"] == 60


# ── items:分页 + 溯源字段 ────────────────────────────────────────

@pytest.mark.asyncio
async def test_items_returns_rows_with_drawing_source(client, fake_db):
    fake_db.fetch_val.return_value = 1
    fake_db.fetch_all.return_value = [_row()]

    resp = await client.get(f"{API}/items", params={"category": "elevation"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    item = body["items"][0]
    # 溯源三件套:图纸 id/图号/标题
    assert item["drawing_id"] == "d-1"
    assert item["drawing_no"] == "结施-05"
    assert item["drawing_title"] == "剖面图"
    # JSONB 已反序列化为对象
    assert item["value_json"] == {"elevation_m": -2.35}


@pytest.mark.asyncio
async def test_items_search_binds_like_param(client, fake_db):
    fake_db.fetch_val.return_value = 0
    fake_db.fetch_all.return_value = []

    resp = await client.get(f"{API}/items", params={"q": "泵房"})

    assert resp.status_code == 200
    # 检索词以 ILIKE 参数绑定(防注入:不得拼进 SQL)
    sql = fake_db.fetch_all.call_args.args[0]
    assert "ILIKE" in sql
    assert "泵房" not in sql
    bound = fake_db.fetch_all.call_args.args[1]
    assert any("泵房" in str(v) for v in bound.values())


@pytest.mark.asyncio
async def test_items_page_size_capped(client, fake_db):
    fake_db.fetch_val.return_value = 0
    fake_db.fetch_all.return_value = []

    resp = await client.get(f"{API}/items", params={"page_size": 9999})

    assert resp.status_code == 422  # 超出 le=200 校验


# ── axes:轴网专用聚合 ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_axes_returns_axis_items_only(client, fake_db):
    fake_db.fetch_all.return_value = [
        _row(category="axis", content="B",
             value_json='{"label": "B", "coord": 12.5, "dir": "y"}'),
    ]

    resp = await client.get(f"{API}/axes")

    assert resp.status_code == 200
    body = resp.json()
    assert body["axes"][0]["value_json"]["label"] == "B"
    sql = fake_db.fetch_all.call_args.args[0]
    assert "category" in sql  # 必须按 axis 过滤


# ── extract:触发 Celery ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_extract_enqueues_celery_task(client, fake_db):
    fake_db.fetch_one.return_value = {"id": "p-0001"}

    with patch(
        "routers.project_info.extract_project_drawing_info"
    ) as task:
        task.delay = MagicMock(return_value=MagicMock(id="task-1"))
        resp = await client.post(f"{API}/extract")

    assert resp.status_code == 202
    task.delay.assert_called_once_with("p-0001", False)


@pytest.mark.asyncio
async def test_extract_404_when_project_missing(client, fake_db):
    fake_db.fetch_one.return_value = None

    resp = await client.post(f"{API}/extract")

    assert resp.status_code == 404
