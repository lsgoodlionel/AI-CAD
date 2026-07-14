"""Finding 统一模型测试（Phase D · 泳道2 · D-05）。

覆盖：
- services/finding_service.py 五类来源的 SQL 行 → Finding 归一映射（严重度/标题/描述）
- 状态覆盖 overlay 的默认状态推导 + 覆盖优先
- list_findings 聚合（合并/排序/筛选/分页/汇总统计）
- get_finding 单条查找 + 未命中
- update_finding_status 状态机（单向推进/拒绝回退/拒绝非法状态）
- routers/findings.py 三个端点（envelope、参数校验 400、404、409、审计写入）
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from services import finding_service

PROJECT_ID = "11111111-1111-1111-1111-111111111111"


# ══════════════════════════════════════════════════════════════
# 纯函数：严重度归一 / 排序 / 状态机校验
# ══════════════════════════════════════════════════════════════

def test_engine_severity_map_covers_all_four_levels():
    assert finding_service._ENGINE_SEVERITY_MAP["critical"] == "critical"
    assert finding_service._ENGINE_SEVERITY_MAP["major"] == "high"
    assert finding_service._ENGINE_SEVERITY_MAP["minor"] == "medium"
    assert finding_service._ENGINE_SEVERITY_MAP["info"] == "low"


def test_severity_from_confidence_conflict_always_high():
    assert finding_service._severity_from_confidence(confidence=0.99, conflict=True) == "high"


def test_severity_from_confidence_buckets_without_conflict():
    assert finding_service._severity_from_confidence(0.2, False) == "high"
    assert finding_service._severity_from_confidence(0.6, False) == "medium"
    assert finding_service._severity_from_confidence(0.95, False) == "low"
    assert finding_service._severity_from_confidence(None, False) == "medium"


def test_sort_key_orders_by_severity_then_recency():
    older = {"severity": "high", "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc)}
    newer = {"severity": "high", "created_at": datetime(2026, 6, 1, tzinfo=timezone.utc)}
    critical = {"severity": "critical", "created_at": datetime(2020, 1, 1, tzinfo=timezone.utc)}
    items = [older, newer, critical]
    items.sort(key=finding_service._sort_key)
    assert items == [critical, newer, older]


def test_validate_transition_allows_forward_and_same():
    finding_service._validate_transition("pending", "acknowledged")
    finding_service._validate_transition("pending", "closed")  # 允许跳跃前进
    finding_service._validate_transition("closed", "closed")  # 原地重复提交


def test_validate_transition_blocks_backward():
    with pytest.raises(finding_service.InvalidTransitionError):
        finding_service._validate_transition("closed", "pending")


def test_validate_transition_rejects_unknown_status():
    with pytest.raises(ValueError):
        finding_service._validate_transition("pending", "bogus")


# ══════════════════════════════════════════════════════════════
# 来源①：engine（ai_review_issues）
# ══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_fetch_engine_findings_maps_row_to_finding(fake_db):
    fake_db.fetch_all.return_value = [{
        "id": "eee1", "drawing_id": "ddd1", "project_id": PROJECT_ID,
        "severity": "major", "category": "消防间距不足", "description": "疏散距离超限",
        "suggestion": None, "status": "open", "location_json": {"levels": ["F1"]},
        "created_at": datetime(2026, 5, 1, tzinfo=timezone.utc),
    }]

    items = await finding_service._fetch_engine_findings(fake_db, PROJECT_ID)

    assert len(items) == 1
    item = items[0]
    assert item["source"] == "engine"
    assert item["source_key"] == "eee1"
    assert item["severity"] == "high"  # major -> high
    assert item["title"] == "消防间距不足"
    assert item["description"] == "疏散距离超限"
    assert item["location"] == {"levels": ["F1"]}
    assert item["native_status"] == "open"


# ══════════════════════════════════════════════════════════════
# 来源②：review（review_audit_findings）
# ══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_fetch_review_findings_maps_risk_level_to_severity(fake_db):
    fake_db.fetch_all.return_value = [{
        "id": "rrr1", "project_id": PROJECT_ID, "discipline_name": "结构",
        "risk_level": "高", "object_level": "构件级", "standard_question": "锚固长度是否满足？",
        "location_json": None, "created_at": datetime(2026, 5, 2, tzinfo=timezone.utc),
    }]

    items = await finding_service._fetch_review_findings(fake_db, PROJECT_ID)

    assert items[0]["source"] == "review"
    assert items[0]["severity"] == "high"
    assert "结构会审发现" in items[0]["title"]
    assert items[0]["description"] == "锚固长度是否满足？"
    assert items[0]["native_status"] is None


@pytest.mark.asyncio
async def test_fetch_review_findings_unknown_risk_level_defaults_medium(fake_db):
    fake_db.fetch_all.return_value = [{
        "id": "rrr2", "project_id": PROJECT_ID, "discipline_name": None,
        "risk_level": None, "object_level": None, "standard_question": "",
        "location_json": None, "created_at": None,
    }]
    items = await finding_service._fetch_review_findings(fake_db, PROJECT_ID)
    assert items[0]["severity"] == "medium"


# ══════════════════════════════════════════════════════════════
# 来源③：cross（review_batches.cross_findings）
# ══════════════════════════════════════════════════════════════

def test_cross_items_from_batch_covers_all_four_categories():
    cross = {
        "重复图号": [{"drawing_no": "S-01", "drawing_ids": ["a", "b"]}],
        "版本冲突": [{"drawing_no": "S-01", "versions": ["A", "B"]}],
        "接口缺图": [{"missing_discipline": "mep", "referenced_by": [{"drawing_no": "A-01"}]}],
        "问题聚类": [{"location_key": "F1@1-2", "count": 3, "drawings": ["A-01", "S-01"], "disciplines": []}],
    }
    items = finding_service._cross_items_from_batch("batch1", cross, None)

    keys = {it["source_key"] for it in items}
    assert keys == {
        "batch1:dup:S-01", "batch1:conflict:S-01",
        "batch1:missing:mep", "batch1:cluster:F1@1-2",
    }
    by_key = {it["source_key"]: it for it in items}
    assert by_key["batch1:conflict:S-01"]["severity"] == "high"
    assert by_key["batch1:dup:S-01"]["severity"] == "medium"
    assert by_key["batch1:cluster:F1@1-2"]["severity"] == "high"  # count=3 >= 3


@pytest.mark.asyncio
async def test_fetch_cross_findings_skips_malformed_json(fake_db):
    fake_db.fetch_all.return_value = [
        {"id": "b1", "cross_findings": "not-a-json-object", "created_at": None},
    ]
    items = await finding_service._fetch_cross_findings(fake_db, PROJECT_ID)
    assert items == []


# ══════════════════════════════════════════════════════════════
# 来源④：semantic（project_models.scene 派生）
# ══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_fetch_semantic_findings_reuses_build_review_queue(fake_db):
    fake_db.fetch_one.return_value = {
        "scene": {
            "review_candidates": [
                {"id": "c1", "target_kind": "element", "confidence": 0.2, "title": "柱 C1"},
            ]
        }
    }
    items = await finding_service._fetch_semantic_findings(fake_db, PROJECT_ID)

    assert len(items) == 1
    assert items[0]["source"] == "semantic"
    assert items[0]["source_key"] == "c1"
    assert items[0]["severity"] == "high"  # confidence 0.2 < 0.5
    assert items[0]["title"] == "柱 C1"


@pytest.mark.asyncio
async def test_fetch_semantic_findings_no_model_returns_empty(fake_db):
    fake_db.fetch_one.return_value = None
    items = await finding_service._fetch_semantic_findings(fake_db, PROJECT_ID)
    assert items == []


# ══════════════════════════════════════════════════════════════
# 来源⑤：symbol（model_symbol_annotations）
# ══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_fetch_symbol_findings_maps_confidence_and_status(fake_db):
    fake_db.fetch_all.return_value = [{
        "id": 7, "drawing_id": "ddd2", "category": "door", "mep_system": None,
        "confidence": 0.4, "status": "pending", "created_at": datetime(2026, 5, 3, tzinfo=timezone.utc),
    }]
    items = await finding_service._fetch_symbol_findings(fake_db, PROJECT_ID)

    assert items[0]["source"] == "symbol"
    assert items[0]["source_key"] == "7"
    assert items[0]["severity"] == "high"  # 0.4 < 0.5
    assert "door" in items[0]["title"]
    assert items[0]["native_status"] == "pending"


# ══════════════════════════════════════════════════════════════
# 状态覆盖 + _finalize
# ══════════════════════════════════════════════════════════════

def test_finalize_uses_default_status_when_no_overlay():
    item = {
        "source": "engine", "source_key": "e1", "project_id": PROJECT_ID,
        "drawing_id": None, "severity": "high", "title": "t", "description": "d",
        "location": None, "created_at": None, "native_status": "closed",
    }
    result = finding_service._finalize(item, overlay={})
    assert result["status"] == "closed"  # ai_review_issues.closed -> our closed
    assert result["id"] == "engine:e1"
    assert result["note"] is None


def test_finalize_overlay_takes_priority_over_native_default():
    item = {
        "source": "symbol", "source_key": "7", "project_id": PROJECT_ID,
        "drawing_id": None, "severity": "medium", "title": "t", "description": "d",
        "location": None, "created_at": None, "native_status": "pending",
    }
    overlay = {("symbol", "7"): {"status": "acknowledged", "note": "已知晓", "updated_at": None}}
    result = finding_service._finalize(item, overlay)
    assert result["status"] == "acknowledged"
    assert result["note"] == "已知晓"


def test_default_status_maps_symbol_native_statuses():
    assert finding_service._default_status("symbol", "confirmed") == "remediated"
    assert finding_service._default_status("symbol", "rejected") == "closed"
    assert finding_service._default_status("symbol", "reclassed") == "remediated"
    assert finding_service._default_status("symbol", None) == "pending"
    assert finding_service._default_status("review", None) == "pending"  # 无原生状态来源恒 pending


# ══════════════════════════════════════════════════════════════
# list_findings 聚合编排（SQL 下推后：patch finding_query.query_table_source
# 隔离聚合/归并/计数逻辑；派生来源经 _FETCHERS 全量拉取）
# ══════════════════════════════════════════════════════════════

def _stub_fetcher(items: list[dict]):
    """派生/单条来源全量拉取桩（get_finding 等仍走 _FETCHERS 全量路径）。"""
    async def _fetch(db, project_id):
        return items
    return _fetch


def _final_finding(source: str, key: str, severity: str, created_at=None, **over) -> dict:
    """构造一条已 finalize 的对外 Finding（query_table_source 的返回元素形态）。"""
    base = {
        "id": f"{source}:{key}", "source": source, "project_id": PROJECT_ID,
        "drawing_id": over.get("drawing_id"), "severity": severity,
        "title": over.get("title", key), "description": over.get("description", ""),
        "status": over.get("status", "pending"), "location": None, "note": None,
        "status_updated_at": None, "created_at": created_at,
        "has_saving_potential": over.get("has_saving_potential", False),
    }
    return base


def _counts(source: str, *, total: int, by_severity: dict, by_status: dict, saving: int = 0) -> dict:
    return {
        "total": total,
        "by_source": {source: total} if total else {},
        "by_severity": dict(by_severity), "by_status": dict(by_status), "saving": saving,
    }


def _empty_counts() -> dict:
    return {"total": 0, "by_source": {}, "by_severity": {}, "by_status": {}, "saving": 0}


def _table_stub(mapping: dict[str, tuple[list[dict], dict]]):
    """按 source 返回 (page, counts)；记录每次调用的 kwargs 供性能断言。"""
    calls: list[dict] = []

    async def _query(db, source, project_id, **kwargs):
        calls.append({"source": source, **kwargs})
        return mapping.get(source, ([], _empty_counts()))

    _query.calls = calls
    return _query


def _stub_derived(mapping: dict[str, list[dict]]):
    """派生来源（cross/semantic）走 _FETCHERS：返回中间态（含 native_status）。"""
    def _make(items):
        async def _fetch(db, project_id):
            return items
        return _fetch
    return {src: _make(items) for src, items in mapping.items()}


@pytest.mark.asyncio
async def test_list_findings_merges_sorts_and_summarizes(fake_db, monkeypatch):
    from services import finding_query

    engine_page = [_final_finding("engine", "e1", "low",
                                  created_at=datetime(2026, 1, 1, tzinfo=timezone.utc))]
    symbol_page = [_final_finding("symbol", "s1", "critical",
                                  created_at=datetime(2026, 6, 1, tzinfo=timezone.utc))]
    stub = _table_stub({
        "engine": (engine_page, _counts("engine", total=1, by_severity={"low": 1}, by_status={"pending": 1})),
        "symbol": (symbol_page, _counts("symbol", total=1, by_severity={"critical": 1}, by_status={"pending": 1})),
    })
    monkeypatch.setattr(finding_query, "query_table_source", stub)
    fake_db.fetch_all.return_value = []  # overlay + 派生来源查询皆空

    items, summary = await finding_service.list_findings(fake_db, PROJECT_ID)

    assert [it["id"] for it in items] == ["symbol:s1", "engine:e1"]  # critical 排前
    assert summary["total"] == 2
    assert summary["by_source"] == {"engine": 1, "symbol": 1}
    assert summary["by_severity"]["critical"] == 1


@pytest.mark.asyncio
async def test_list_findings_skips_failing_source_and_keeps_others(fake_db, monkeypatch):
    """某表行来源下推查询抛错（如该来源表在当前部署缺失/未迁移）时，聚合应跳过
    该源、仍返回其余来源，而非整体 500——审查中心核心页需优雅降级。"""
    from services import finding_query

    engine_page = [_final_finding("engine", "e1", "high",
                                  created_at=datetime(2026, 1, 1, tzinfo=timezone.utc))]

    async def _query(db, source, project_id, **kwargs):
        if source == "review":
            raise RuntimeError('relation "review_audit_findings" does not exist')
        if source == "engine":
            return engine_page, _counts("engine", total=1, by_severity={"high": 1}, by_status={"pending": 1})
        return [], _empty_counts()

    monkeypatch.setattr(finding_query, "query_table_source", _query)
    fake_db.fetch_all.return_value = []

    items, summary = await finding_service.list_findings(fake_db, PROJECT_ID)

    assert [it["id"] for it in items] == ["engine:e1"]  # review 源被跳过，engine 仍在
    assert summary["total"] == 1
    assert "review" not in summary["by_source"]


@pytest.mark.asyncio
async def test_list_findings_filters_pushed_down_to_table_source(fake_db, monkeypatch):
    """severity/status/drawing_id 筛选参数应原样下推给 query_table_source。"""
    from services import finding_query

    stub = _table_stub({"engine": ([], _empty_counts())})
    monkeypatch.setattr(finding_query, "query_table_source", stub)
    fake_db.fetch_all.return_value = []

    items, summary = await finding_service.list_findings(
        fake_db, PROJECT_ID, source="engine", severity="high", status="pending",
        drawing_id="d9",
    )
    assert items == []
    assert summary["total"] == 0
    # 仅查询了指定来源，且筛选条件已下推
    assert stub.calls == [{
        "source": "engine", "severity": "high", "status": "pending",
        "drawing_id": "d9", "top_n": 200,
    }]


@pytest.mark.asyncio
async def test_list_findings_pushes_down_limit_offset_as_top_n(fake_db, monkeypatch):
    """性能核心：分页应下推为 LIMIT offset+limit（top_n），而非全量拉取后 Python 切片。"""
    from services import finding_query

    # 桩模拟 SQL 返回 top_n(=offset+limit=3) 行；下游 _merge_and_slice 取 [1:3]
    page = [_final_finding("engine", f"e{i}", "medium") for i in range(3)]
    stub = _table_stub({
        "engine": (page, _counts("engine", total=23417, by_severity={"medium": 23417},
                                 by_status={"pending": 23417})),
    })
    monkeypatch.setattr(finding_query, "query_table_source", stub)
    fake_db.fetch_all.return_value = []

    items, summary = await finding_service.list_findings(
        fake_db, PROJECT_ID, source="engine", limit=2, offset=1,
    )
    assert len(items) == 2
    # 汇总统计基于筛选后全量（SQL GROUP BY），不受分页影响
    assert summary["total"] == 23417
    # 下推窗口 = offset + limit（只物化窗口所需行，不拉 2.3 万条）
    assert stub.calls[0]["top_n"] == 3


# ══════════════════════════════════════════════════════════════
# get_finding
# ══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_get_finding_returns_match(fake_db, monkeypatch):
    items = [{
        "source": "engine", "source_key": "e1", "project_id": PROJECT_ID, "drawing_id": None,
        "severity": "high", "title": "t", "description": "d", "location": None,
        "created_at": None, "native_status": "open",
    }]
    monkeypatch.setitem(finding_service._FETCHERS, "engine", _stub_fetcher(items))
    fake_db.fetch_all.return_value = []

    result = await finding_service.get_finding(fake_db, PROJECT_ID, "engine", "e1")
    assert result is not None
    assert result["id"] == "engine:e1"


@pytest.mark.asyncio
async def test_get_finding_returns_none_when_missing(fake_db, monkeypatch):
    monkeypatch.setitem(finding_service._FETCHERS, "engine", _stub_fetcher([]))
    result = await finding_service.get_finding(fake_db, PROJECT_ID, "engine", "missing")
    assert result is None


@pytest.mark.asyncio
async def test_get_finding_rejects_invalid_source(fake_db):
    with pytest.raises(ValueError):
        await finding_service.get_finding(fake_db, PROJECT_ID, "bogus", "x")


# ══════════════════════════════════════════════════════════════
# update_finding_status
# ══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_update_finding_status_first_transition_assumes_pending_baseline(fake_db):
    # 第一次调用（查现状）返回 None → 无覆盖记录，起点视为 pending；
    # 第二次调用（upsert RETURNING）返回落库后的行。
    fake_db.fetch_one = _sequenced_fetch_one([None, {
        "id": 1, "project_id": PROJECT_ID, "source": "engine", "source_key": "e1",
        "status": "acknowledged", "note": None, "updated_by": None, "updated_at": None,
    }])

    result = await finding_service.update_finding_status(
        fake_db, project_id=PROJECT_ID, source="engine", source_key="e1",
        target_status="acknowledged",
    )
    assert result["status"] == "acknowledged"


@pytest.mark.asyncio
async def test_update_finding_status_blocks_backward(fake_db):
    fake_db.fetch_one = _sequenced_fetch_one([{"status": "closed"}])
    with pytest.raises(finding_service.InvalidTransitionError):
        await finding_service.update_finding_status(
            fake_db, project_id=PROJECT_ID, source="engine", source_key="e1",
            target_status="pending",
        )


@pytest.mark.asyncio
async def test_update_finding_status_rejects_invalid_source(fake_db):
    with pytest.raises(ValueError):
        await finding_service.update_finding_status(
            fake_db, project_id=PROJECT_ID, source="bogus", source_key="e1",
            target_status="pending",
        )


def _sequenced_fetch_one(returns: list):
    return AsyncMock(side_effect=returns)


# ══════════════════════════════════════════════════════════════
# Router：GET /projects/{id}/findings 等三个端点
# ══════════════════════════════════════════════════════════════

@pytest.fixture(autouse=True)
def _register_router():
    """把 D-05 router 挂到全局 app（main.py 未注册，测试内自注册，幂等）。"""
    from main import app
    from routers.findings import router as findings_router

    has = any(
        str(getattr(route, "path", "")).endswith("/findings") for route in app.routes
    )
    if not has:
        app.include_router(findings_router, prefix="/api/v1")
    yield


@pytest.mark.asyncio
async def test_list_findings_endpoint_returns_envelope(client):
    canned_items = [{
        "id": "engine:e1", "source": "engine", "project_id": PROJECT_ID,
        "drawing_id": None, "severity": "high", "title": "t", "description": "d",
        "status": "pending", "location": None, "note": None,
        "status_updated_at": None, "created_at": None,
    }]
    canned_summary = {"total": 1, "by_source": {"engine": 1}, "by_severity": {"high": 1}, "by_status": {"pending": 1}}

    with patch("services.finding_service.list_findings", return_value=(canned_items, canned_summary)):
        resp = await client.get(f"/api/v1/projects/{PROJECT_ID}/findings")

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["error"] is None
    assert body["data"] == canned_items
    assert body["meta"]["total"] == 1
    assert body["meta"]["limit"] == 200


@pytest.mark.asyncio
async def test_list_findings_endpoint_rejects_invalid_source(client):
    resp = await client.get(f"/api/v1/projects/{PROJECT_ID}/findings", params={"source": "bogus"})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "INVALID_SOURCE"


@pytest.mark.asyncio
async def test_list_findings_endpoint_rejects_invalid_severity(client):
    resp = await client.get(f"/api/v1/projects/{PROJECT_ID}/findings", params={"severity": "urgent"})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "INVALID_SEVERITY"


@pytest.mark.asyncio
async def test_get_finding_endpoint_returns_404_when_missing(client):
    with patch("services.finding_service.get_finding", return_value=None):
        resp = await client.get(f"/api/v1/projects/{PROJECT_ID}/findings/engine/missing")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "FINDING_NOT_FOUND"


@pytest.mark.asyncio
async def test_get_finding_endpoint_rejects_invalid_source(client):
    resp = await client.get(f"/api/v1/projects/{PROJECT_ID}/findings/bogus/x")
    assert resp.status_code == 400
    assert resp.json()["detail"] == "INVALID_SOURCE"


@pytest.mark.asyncio
async def test_transition_status_endpoint_success_writes_audit(client, fake_db):
    canned = {
        "id": 1, "project_id": PROJECT_ID, "source": "engine", "source_key": "e1",
        "status": "acknowledged", "note": "已复核", "updated_by": None,
        "updated_at": "2026-07-14T00:00:00+00:00",
    }
    with patch("services.finding_service.update_finding_status", return_value=canned):
        resp = await client.post(
            f"/api/v1/projects/{PROJECT_ID}/findings/engine/e1/status",
            json={"status": "acknowledged", "note": "已复核"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["status"] == "acknowledged"
    assert body["data"]["id"] == "engine:e1"
    fake_db.execute.assert_awaited()  # write_audit 落库


@pytest.mark.asyncio
async def test_transition_status_endpoint_rejects_invalid_status(client):
    resp = await client.post(
        f"/api/v1/projects/{PROJECT_ID}/findings/engine/e1/status",
        json={"status": "bogus"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "INVALID_STATUS"


@pytest.mark.asyncio
async def test_transition_status_endpoint_returns_409_on_backward_transition(client):
    with patch(
        "services.finding_service.update_finding_status",
        side_effect=finding_service.InvalidTransitionError("cannot move backward"),
    ):
        resp = await client.post(
            f"/api/v1/projects/{PROJECT_ID}/findings/engine/e1/status",
            json={"status": "pending"},
        )
    assert resp.status_code == 409
