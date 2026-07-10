"""语义审校队列 Router 测试（Phase C · C-15）。

覆盖：
- build_review_queue 纯函数：低置信/规则-模型冲突优先排序、拓扑派生、去重、过滤、边界；
- GET /model/review-queue：200 排序 + 汇总、404 未建模、非法 target_kind 400；
- POST /model/review-actions：写 model_review_actions 埋点 + audit_logs、边界（改类必填/未知项目/非法动作）。
"""
import json
from unittest.mock import AsyncMock, patch

import pytest

from routers.model_review import build_review_queue

# 把 C-15 router 注册进测试 app（main.py 由集成收口，测试内自注册，幂等）
from main import app
from routers.model_review import router as _model_review_router

if not any(
    str(getattr(r, "path", "")).endswith("/model/review-queue") for r in app.routes
):
    app.include_router(_model_review_router, prefix="/api/v1")

PROJECT_ID = "22222222-2222-2222-2222-222222222222"


# ══════════════════════════════════════════════════════════════
# build_review_queue —— 纯函数（排序 / 派生 / 过滤 / 边界）
# ══════════════════════════════════════════════════════════════

def test_conflict_ranks_before_low_confidence():
    # Arrange：a=低置信无冲突；b=高置信但规则-模型冲突
    scene = {
        "review_candidates": [
            {"id": "a", "target_kind": "element", "confidence": 0.1},
            {"id": "b", "target_kind": "compliance", "confidence": 0.9, "conflict": True},
        ]
    }
    # Act
    items, summary = build_review_queue(scene)
    # Assert：冲突项永远优先，即使其置信度更高
    assert [it["id"] for it in items] == ["b", "a"]
    assert items[0]["conflict"] is True
    assert summary["conflict_count"] == 1
    assert summary["low_confidence_count"] == 1  # a 的 0.1 < 0.5


def test_low_confidence_ranks_first_within_non_conflict():
    scene = {
        "review_candidates": [
            {"id": "hi", "target_kind": "element", "confidence": 0.8},
            {"id": "lo", "target_kind": "element", "confidence": 0.2},
        ]
    }
    items, _ = build_review_queue(scene)
    assert [it["id"] for it in items] == ["lo", "hi"]


def test_topology_orphan_host_becomes_conflict():
    scene = {
        "topology": {
            "host_rels": [
                {"opening_id": "o1", "orphan": True},
                {"opening_id": "o2", "wall_id": "w9", "orphan": False, "confidence": 0.7},
            ]
        }
    }
    items, summary = build_review_queue(scene)
    by_id = {it["id"]: it for it in items}
    assert by_id["host:o1"]["conflict"] is True
    assert by_id["host:o2"]["conflict"] is False
    assert items[0]["id"] == "host:o1"  # 冲突优先
    assert summary["by_kind"]["topology"] == 2


def test_topology_beam_and_slab_and_relations():
    scene = {
        "topology": {
            "beam_supports": [
                {"beam_id": "b1"},                       # 无柱 → 冲突
                {"beam_id": "b2", "column_id": "c1"},    # 有柱
            ],
            "slab_supports": [
                {"slab_id": "s1", "beam_ids": []},       # 无梁 → 冲突
                {"slab_id": "s2", "beam_ids": ["b2"]},
            ],
            "relations": [
                {"id": "wc1", "closed": False, "title": "墙链闭合"},  # 未闭合 → 冲突
                {"id": "wc2", "closed": True},
            ],
        }
    }
    items, _ = build_review_queue(scene)
    by_id = {it["id"]: it for it in items}
    assert by_id["beam:b1"]["conflict"] is True
    assert by_id["beam:b2"]["conflict"] is False
    assert by_id["slab:s1"]["conflict"] is True
    assert by_id["rel:wc1"]["conflict"] is True
    assert by_id["rel:wc2"]["conflict"] is False


def test_fused_category_disagreement_is_conflict():
    scene = {
        "review_candidates": [
            {
                "id": "f1", "target_kind": "element", "source": "fused",
                "confidence": 0.95, "rule_category": "beam", "model_category": "column",
            }
        ]
    }
    items, _ = build_review_queue(scene)
    assert items[0]["conflict"] is True
    assert items[0]["suggested_category"] == "column"


def test_element_naming_derivation_and_geometry_only_skipped():
    scene = {
        "floors": [
            {
                "key": "F1",
                "elements": {
                    "columns": [
                        {"id": "col-1", "confidence": 0.3},          # 无名 → naming
                        {"id": "col-2", "name": "KZ1", "confidence": 0.9},  # 有名 → element
                        {"outline": [[0, 0]]},                        # 几何-only → 跳过
                    ]
                },
            }
        ]
    }
    items, _ = build_review_queue(scene)
    kinds = {it["id"]: it["target_kind"] for it in items}
    assert kinds["col-1"] == "naming"
    assert kinds["col-2"] == "element"
    assert len(items) == 2  # 几何-only 被跳过


def test_filters_and_limit_and_dedup():
    scene = {
        "review_candidates": [
            {"id": "x", "target_kind": "element", "confidence": 0.2, "discipline": "结构"},
            {"id": "x", "target_kind": "element", "confidence": 0.2, "discipline": "结构"},  # 重复
            {"id": "y", "target_kind": "compliance", "confidence": 0.9, "conflict": True,
             "discipline": "机电"},
        ]
    }
    # 去重
    all_items, _ = build_review_queue(scene)
    assert len(all_items) == 2
    # 按 kind 过滤
    only_elem, _ = build_review_queue(scene, target_kind="element")
    assert [it["id"] for it in only_elem] == ["x"]
    # 只看冲突
    conflicts, _ = build_review_queue(scene, only_conflicts=True)
    assert [it["id"] for it in conflicts] == ["y"]
    # discipline 过滤
    mep, _ = build_review_queue(scene, discipline="机电")
    assert [it["id"] for it in mep] == ["y"]
    # limit
    limited, _ = build_review_queue(scene, limit=1)
    assert len(limited) == 1


def test_empty_and_none_scene():
    items, summary = build_review_queue(None)
    assert items == []
    assert summary["total"] == 0
    items2, summary2 = build_review_queue({})
    assert items2 == []
    assert summary2["by_kind"] == {}


# ══════════════════════════════════════════════════════════════
# GET /model/review-queue
# ══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_get_review_queue_returns_sorted_items(client, fake_db):
    scene = {
        "review_candidates": [
            {"id": "a", "target_kind": "element", "confidence": 0.1},
            {"id": "b", "target_kind": "topology", "confidence": 0.9, "conflict": True},
        ]
    }
    fake_db.fetch_one.return_value = {"scene": json.dumps(scene, ensure_ascii=False)}

    resp = await client.get(f"/api/v1/projects/{PROJECT_ID}/model/review-queue")

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    items = body["data"]["items"]
    assert [it["id"] for it in items] == ["b", "a"]  # 冲突优先
    assert body["data"]["summary"]["conflict_count"] == 1
    assert body["meta"]["returned"] == 2


@pytest.mark.asyncio
async def test_get_review_queue_404_when_not_built(client, fake_db):
    fake_db.fetch_one.return_value = None

    resp = await client.get(f"/api/v1/projects/{PROJECT_ID}/model/review-queue")

    assert resp.status_code == 404
    assert resp.json()["detail"] == "MODEL_NOT_BUILT"


@pytest.mark.asyncio
async def test_get_review_queue_rejects_invalid_target_kind(client, fake_db):
    resp = await client.get(
        f"/api/v1/projects/{PROJECT_ID}/model/review-queue",
        params={"target_kind": "bogus"},
    )

    assert resp.status_code == 400
    assert resp.json()["detail"] == "INVALID_TARGET_KIND"


# ══════════════════════════════════════════════════════════════
# POST /model/review-actions
# ══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_submit_review_action_writes_tap_and_audit(client, fake_db, admin_user):
    # Arrange：项目存在 → 埋点 INSERT RETURNING id
    fake_db.fetch_one.side_effect = [
        {"id": PROJECT_ID},
        {"id": 77, "created_at": None},
    ]
    audit = AsyncMock()

    with patch("routers.model_review.write_audit", audit):
        resp = await client.post(
            f"/api/v1/projects/{PROJECT_ID}/model/review-actions",
            json={
                "target_kind": "topology",
                "action_type": "reclass",
                "target_id": "host:o1",
                "old_category": "opening_host",
                "new_category": "wall_bound",
                "source": "rule",
                "confidence": 0.42,
                "note": "洞口应归属 W3",
            },
        )

    # Assert：201 + 埋点写入 + 审计写入
    assert resp.status_code == 201
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["id"] == "77"
    assert body["meta"]["audited"] is True

    # 埋点：第二次 fetch_one 为 model_review_actions INSERT
    insert_call = fake_db.fetch_one.call_args_list[1]
    assert "model_review_actions" in insert_call.args[0]
    assert "topology" in insert_call.args  # target_kind 入参
    assert admin_user["id"] in insert_call.args  # reviewer_id 入参

    # 审计：action=model_semantic_review
    audit.assert_awaited_once()
    assert audit.await_args.kwargs["action"] == "model_semantic_review"
    assert audit.await_args.kwargs["resource"] == "model_review_action"
    assert audit.await_args.kwargs["new_state"]["target_kind"] == "topology"


@pytest.mark.asyncio
async def test_submit_confirm_action_minimal_body(client, fake_db):
    fake_db.fetch_one.side_effect = [{"id": PROJECT_ID}, {"id": 5, "created_at": None}]

    with patch("routers.model_review.write_audit", AsyncMock()):
        resp = await client.post(
            f"/api/v1/projects/{PROJECT_ID}/model/review-actions",
            json={"target_kind": "element", "action_type": "confirm", "target_id": "col-2"},
        )

    assert resp.status_code == 201
    assert resp.json()["data"]["action_type"] == "confirm"


@pytest.mark.asyncio
async def test_reclass_requires_new_category(client, fake_db):
    fake_db.fetch_one.side_effect = [{"id": PROJECT_ID}]

    with patch("routers.model_review.write_audit", AsyncMock()) as audit:
        resp = await client.post(
            f"/api/v1/projects/{PROJECT_ID}/model/review-actions",
            json={"target_kind": "element", "action_type": "reclass", "target_id": "col-1"},
        )

    assert resp.status_code == 400
    assert resp.json()["detail"] == "REVIEW_NEW_CATEGORY_REQUIRED"
    audit.assert_not_awaited()


@pytest.mark.asyncio
async def test_submit_review_action_404_for_unknown_project(client, fake_db):
    fake_db.fetch_one.side_effect = [None]

    resp = await client.post(
        f"/api/v1/projects/{PROJECT_ID}/model/review-actions",
        json={"target_kind": "naming", "action_type": "confirm"},
    )

    assert resp.status_code == 404
    assert resp.json()["detail"] == "PROJECT_NOT_FOUND"


@pytest.mark.asyncio
async def test_submit_review_action_rejects_invalid_action_type(client, fake_db):
    resp = await client.post(
        f"/api/v1/projects/{PROJECT_ID}/model/review-actions",
        json={"target_kind": "topology", "action_type": "frobnicate"},
    )

    assert resp.status_code == 422  # pydantic Literal 校验
