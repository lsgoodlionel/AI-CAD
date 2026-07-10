"""符号级标注人审 Router 测试（Phase C · C-16）。

覆盖：列出（低置信优先/映射）、保存写两表（标注 + 埋点）、确认/否定/改类/补框、
导出仅金标签、404/400 边界、序列化纯函数。
路由未在 main.py 注册（收口由上游做），测试内自注册到 app。

注：与既有 services 层 ``test_model_annotations.py`` 区分（后者测楼层归属服务），
本文件测 C-16 符号级 router，命名对齐 ``test_project_models_router.py`` 约定。
"""
import pytest

from routers.model_annotations import serialize_coco

PROJECT_ID = "22222222-2222-2222-2222-222222222222"
DRAWING_ID = "77777777-7777-7777-7777-777777777777"
BASE = f"/api/v1/projects/{PROJECT_ID}/drawings/{DRAWING_ID}/symbol-annotations"
EXPORT = f"/api/v1/projects/{PROJECT_ID}/symbol-annotations/export"


@pytest.fixture(autouse=True)
def _register_router():
    """把 C-16 router 挂到全局 app（main.py 未注册，测试内自注册，幂等）。"""
    from main import app
    from routers.model_annotations import router as annotations_router

    has = any(
        str(getattr(route, "path", "")).endswith("/symbol-annotations/export")
        for route in app.routes
    )
    if not has:
        app.include_router(annotations_router, prefix="/api/v1")
    yield


def _annotation_row(**over):
    base = {
        "id": 10,
        "project_id": PROJECT_ID,
        "drawing_id": DRAWING_ID,
        "category": "column",
        "mep_system": None,
        "bbox": [10, 20, 40, 60],
        "confidence": 0.9,
        "source": "model",
        "status": "pending",
        "primitive_ids": None,
        "reviewer_id": None,
        "evidence": None,
        "created_at": None,
        "updated_at": None,
    }
    base.update(over)
    return base


# ── 列出 ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_returns_envelope_and_camel_mapping(client, fake_db):
    fake_db.fetch_one.return_value = {"id": DRAWING_ID}  # 图纸存在
    fake_db.fetch_all.return_value = [
        _annotation_row(id=1, confidence=0.30, category="beam", status="pending"),
        _annotation_row(id=2, confidence=0.95, category="column", status="confirmed"),
    ]

    resp = await client.get(BASE)

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["error"] is None
    assert body["meta"]["count"] == 2
    assert body["meta"]["pending_count"] == 1
    assert "column" in body["meta"]["taxonomy"]
    first = body["data"][0]
    assert first["drawingId"] == DRAWING_ID  # snake→camel 映射
    assert first["bbox"] == [10, 20, 40, 60]
    assert first["mepSystem"] is None


@pytest.mark.asyncio
async def test_list_returns_404_when_drawing_missing(client, fake_db):
    fake_db.fetch_one.return_value = None

    resp = await client.get(BASE)

    assert resp.status_code == 404
    assert resp.json()["detail"] == "DRAWING_NOT_FOUND"


# ── 保存：补框（新增人工框，写两表）────────────────────────────

@pytest.mark.asyncio
async def test_addbox_inserts_annotation_and_writes_action(client, fake_db, admin_user):
    inserted = _annotation_row(id=42, source="human", status="confirmed", category="door")
    fake_db.fetch_one.side_effect = [{"id": DRAWING_ID}, inserted]

    resp = await client.post(
        BASE,
        json={
            "actionType": "addbox",
            "category": "door",
            "bbox": [5, 5, 25, 25],
            "confidence": 1.0,
            "note": "人工补框",
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["id"] == 42
    assert body["meta"]["action"] == "addbox"
    assert body["meta"]["status"] == "confirmed"

    # 同时写埋点表（model_review_actions），target_id=标注 id、无旧类别、新类别=door
    fake_db.execute.assert_awaited_once()
    action_args = fake_db.execute.await_args.args
    assert action_args[1] == PROJECT_ID
    assert action_args[2] == DRAWING_ID
    assert action_args[3] == "42"            # target_id
    assert action_args[4] == "addbox"        # action_type
    assert action_args[5] is None            # old_category
    assert action_args[6] == "door"          # new_category
    assert action_args[11] == admin_user["id"]  # reviewer_id


@pytest.mark.asyncio
async def test_addbox_requires_category(client, fake_db):
    fake_db.fetch_one.return_value = {"id": DRAWING_ID}

    resp = await client.post(BASE, json={"actionType": "addbox", "bbox": [1, 1, 2, 2]})

    assert resp.status_code == 400
    assert resp.json()["detail"] == "CATEGORY_REQUIRED"


@pytest.mark.asyncio
async def test_addbox_requires_valid_bbox(client, fake_db):
    fake_db.fetch_one.return_value = {"id": DRAWING_ID}

    resp = await client.post(
        BASE, json={"actionType": "addbox", "category": "wall", "bbox": [1, 2, 3]}
    )

    assert resp.status_code == 400
    assert resp.json()["detail"] == "BBOX_REQUIRED"


# ── 保存：确认/否定/改类/编辑（更新既有候选）─────────────────────

@pytest.mark.asyncio
async def test_confirm_updates_status_and_logs_audit_confidence(client, fake_db, admin_user):
    existing = _annotation_row(id=7, category="beam", confidence=0.42, status="pending")
    updated = _annotation_row(id=7, category="beam", confidence=0.42, status="confirmed")
    fake_db.fetch_one.side_effect = [{"id": DRAWING_ID}, existing, updated]

    resp = await client.post(BASE, json={"actionType": "confirm", "id": 7})

    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "confirmed"
    action_args = fake_db.execute.await_args.args
    assert action_args[4] == "confirm"
    assert action_args[5] == "beam"     # old_category（沿用既有）
    assert action_args[6] == "beam"     # new_category
    assert action_args[10] == 0.42      # 审前候选置信度进埋点


@pytest.mark.asyncio
async def test_reclass_records_old_and_new_category(client, fake_db):
    existing = _annotation_row(id=8, category="pipe", status="pending")
    updated = _annotation_row(id=8, category="equipment", status="reclassed")
    fake_db.fetch_one.side_effect = [{"id": DRAWING_ID}, existing, updated]

    resp = await client.post(
        BASE, json={"actionType": "reclass", "id": 8, "category": "equipment"}
    )

    assert resp.status_code == 200
    assert resp.json()["data"]["category"] == "equipment"
    action_args = fake_db.execute.await_args.args
    assert action_args[4] == "reclass"
    assert action_args[5] == "pipe"        # old
    assert action_args[6] == "equipment"   # new


@pytest.mark.asyncio
async def test_reject_records_no_new_category(client, fake_db):
    existing = _annotation_row(id=9, category="window", status="pending")
    updated = _annotation_row(id=9, category="window", status="rejected")
    fake_db.fetch_one.side_effect = [{"id": DRAWING_ID}, existing, updated]

    resp = await client.post(BASE, json={"actionType": "reject", "id": 9})

    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "rejected"
    action_args = fake_db.execute.await_args.args
    assert action_args[4] == "reject"
    assert action_args[5] == "window"  # old
    assert action_args[6] is None      # reject 无新类别


@pytest.mark.asyncio
async def test_edit_updates_bbox(client, fake_db):
    existing = _annotation_row(id=11, category="slab", bbox=[0, 0, 10, 10])
    updated = _annotation_row(id=11, category="slab", bbox=[2, 2, 30, 30], status="confirmed")
    fake_db.fetch_one.side_effect = [{"id": DRAWING_ID}, existing, updated]

    resp = await client.post(
        BASE, json={"actionType": "edit", "id": 11, "bbox": [2, 2, 30, 30]}
    )

    assert resp.status_code == 200
    assert resp.json()["data"]["bbox"] == [2, 2, 30, 30]


# ── 保存：边界 ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_save_rejects_invalid_action(client, fake_db):
    fake_db.fetch_one.return_value = {"id": DRAWING_ID}

    resp = await client.post(BASE, json={"actionType": "delete", "id": 1})

    assert resp.status_code == 400
    assert resp.json()["detail"] == "INVALID_ACTION_TYPE"


@pytest.mark.asyncio
async def test_confirm_without_id_requires_id(client, fake_db):
    fake_db.fetch_one.return_value = {"id": DRAWING_ID}

    resp = await client.post(BASE, json={"actionType": "confirm"})

    assert resp.status_code == 400
    assert resp.json()["detail"] == "ANNOTATION_ID_REQUIRED"


@pytest.mark.asyncio
async def test_update_missing_annotation_returns_404(client, fake_db):
    fake_db.fetch_one.side_effect = [{"id": DRAWING_ID}, None]

    resp = await client.post(BASE, json={"actionType": "confirm", "id": 999})

    assert resp.status_code == 404
    assert resp.json()["detail"] == "ANNOTATION_NOT_FOUND"


@pytest.mark.asyncio
async def test_save_404_when_drawing_missing(client, fake_db):
    fake_db.fetch_one.return_value = None

    resp = await client.post(
        BASE, json={"actionType": "addbox", "category": "wall", "bbox": [1, 1, 2, 2]}
    )

    assert resp.status_code == 404
    assert resp.json()["detail"] == "DRAWING_NOT_FOUND"


# ── 导出金标签 ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_export_returns_coco_gold_labels(client, fake_db):
    fake_db.fetch_all.return_value = [
        {
            "id": 1, "drawing_id": "d1", "category": "column",
            "mep_system": None, "bbox": [10, 20, 40, 60],
            "confidence": 0.9, "status": "confirmed", "primitive_ids": [5, 6],
        },
        {
            "id": 2, "drawing_id": "d1", "category": "beam",
            "mep_system": None, "bbox": [0, 0, 5, 8],
            "confidence": 0.8, "status": "confirmed", "primitive_ids": None,
        },
    ]

    resp = await client.get(EXPORT)

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["meta"]["format"] == "coco"
    assert body["meta"]["image_count"] == 1        # 同图纸去重
    assert body["meta"]["annotation_count"] == 2
    data = body["data"]
    assert len(data["categories"]) == 9
    first = data["annotations"][0]
    assert first["category_id"] == 1               # column → id 1
    assert first["bbox"] == [10, 20, 30, 40]       # xyxy → xywh


@pytest.mark.asyncio
async def test_export_rejects_unsupported_format(client):
    resp = await client.get(EXPORT, params={"format": "csv"})

    assert resp.status_code == 400
    assert resp.json()["detail"] == "UNSUPPORTED_FORMAT"


# ── 序列化纯函数（DRY 共享逻辑）───────────────────────────────

def test_serialize_coco_dedups_images_and_maps_categories():
    rows = [
        {"id": 1, "drawing_id": "d1", "category": "wall", "bbox": [0, 0, 4, 6],
         "mep_system": None, "confidence": 1.0, "status": "confirmed", "primitive_ids": None},
        {"id": 2, "drawing_id": "d2", "category": "axis", "bbox": [1, 1, 3, 5],
         "mep_system": None, "confidence": 1.0, "status": "confirmed", "primitive_ids": None},
    ]

    dataset = serialize_coco(rows, project_id=PROJECT_ID, exported_at="2026-07-10T00:00:00+00:00")

    assert len(dataset["images"]) == 2
    assert dataset["info"]["exported_at"] == "2026-07-10T00:00:00+00:00"
    ann = {a["category_name"]: a for a in dataset["annotations"]}
    assert ann["wall"]["category_id"] == 4
    assert ann["axis"]["category_id"] == 9
    assert ann["wall"]["bbox"] == [0, 0, 4, 6]


def test_serialize_coco_handles_bad_bbox_gracefully():
    rows = [{"id": 1, "drawing_id": "d1", "category": "unknown_kind",
             "bbox": "not-a-box", "mep_system": None, "confidence": None,
             "status": "confirmed", "primitive_ids": None}]

    dataset = serialize_coco(rows, project_id=PROJECT_ID)

    ann = dataset["annotations"][0]
    assert ann["category_id"] == 0        # 非 taxonomy → 0
    assert ann["bbox"] == [0.0, 0.0, 0.0, 0.0]
