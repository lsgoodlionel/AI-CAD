"""事件编排层建议待办 API（Phase D · 泳道3 · D-08）。

事件编排层（core/pipeline/）只生成「建议/待办」并落库；本路由提供查询与
人工采纳/忽略的出口。硬约束：采纳建议本身**不**自动执行任何重建/创建提案
等硬动作——前端/调用方在采纳后仍需自行调用既有端点
（POST /projects/{id}/model/rebuild、POST /projects/{id}/model/quantities/to-proposal）
完成实际操作，本路由只把建议标记为「已采纳」以便追踪，不代为触发。

端点（统一信封 ``{success, data, error, meta}``）：
- GET  /projects/{project_id}/pipeline/suggestions              列出建议待办（默认只看 open）
- POST /projects/{project_id}/pipeline/suggestions/{id}/accept  标记已采纳
- POST /projects/{project_id}/pipeline/suggestions/{id}/dismiss 标记已忽略

集成点（main.py，由集成方注册，本文件不改 main.py）：
    from routers import pipeline as pipeline_router
    app.include_router(pipeline_router.router, prefix="/api/v1")
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from dependencies import get_current_user, get_db
from services.audit import write_audit

router = APIRouter(prefix="/projects", tags=["pipeline"])

_VALID_STATUSES = frozenset({"open", "accepted", "dismissed"})
_AUDIT_ACTION = "pipeline_suggestion_resolve"


def _parse_payload(value: Any) -> dict:
    if value is None:
        return {}
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return value if isinstance(value, dict) else {}


def _serialize(row: dict) -> dict:
    record = dict(row)
    record["payload"] = _parse_payload(record.get("payload"))
    return record


@router.get("/{project_id}/pipeline/suggestions")
async def list_pipeline_suggestions(
    project_id: str,
    status: str | None = Query(None, description="open|accepted|dismissed，缺省只看 open"),
    db=Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    if status is not None and status not in _VALID_STATUSES:
        raise HTTPException(422, "INVALID_STATUS")

    query = """
        SELECT id, project_id, event_id, suggestion_type, status, title, summary,
               payload, created_at, resolved_at, resolved_by
        FROM pipeline_suggestions
        WHERE project_id=:project_id
    """
    params: dict[str, Any] = {"project_id": project_id}
    if status:
        query += " AND status=:status"
        params["status"] = status
    else:
        query += " AND status='open'"
    query += " ORDER BY created_at DESC"

    rows = await db.fetch_all(query, params)
    items = [_serialize(dict(r)) for r in rows]

    return {
        "success": True,
        "data": {"items": items, "total": len(items)},
        "error": None,
        "meta": {"project_id": project_id, "status_filter": status or "open"},
    }


async def _resolve_suggestion(
    db,
    request: Request,
    current_user: dict,
    project_id: str,
    suggestion_id: str,
    new_status: str,
) -> dict:
    row = await db.fetch_one(
        """
        SELECT id, project_id, suggestion_type, status, title, summary, payload
        FROM pipeline_suggestions
        WHERE id=:suggestion_id AND project_id=:project_id
        """,
        {"suggestion_id": suggestion_id, "project_id": project_id},
    )
    if row is None:
        raise HTTPException(404, "SUGGESTION_NOT_FOUND")
    record = dict(row)
    if record["status"] != "open":
        raise HTTPException(409, "SUGGESTION_ALREADY_RESOLVED")

    await db.execute(
        """
        UPDATE pipeline_suggestions
        SET status=:new_status, resolved_at=now(), resolved_by=:user_id
        WHERE id=:suggestion_id
        """,
        {
            "new_status": new_status,
            "user_id": current_user["id"],
            "suggestion_id": suggestion_id,
        },
    )

    await write_audit(
        db,
        user_id=current_user["id"],
        action=_AUDIT_ACTION,
        resource="pipeline_suggestion",
        resource_id=suggestion_id,
        old_state={"status": "open"},
        new_state={"status": new_status, "suggestion_type": record["suggestion_type"]},
        ip_address=request.client.host if request.client else None,
    )

    return {
        "id": suggestion_id,
        "project_id": project_id,
        "suggestion_type": record["suggestion_type"],
        "status": new_status,
    }


@router.post("/{project_id}/pipeline/suggestions/{suggestion_id}/accept")
async def accept_pipeline_suggestion(
    request: Request,
    project_id: str,
    suggestion_id: str,
    db=Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """采纳建议：仅标记状态，不代为触发重建/创建提案等硬动作（人工在前端自行发起）。"""
    data = await _resolve_suggestion(db, request, current_user, project_id, suggestion_id, "accepted")
    return {"success": True, "data": data, "error": None, "meta": None}


@router.post("/{project_id}/pipeline/suggestions/{suggestion_id}/dismiss")
async def dismiss_pipeline_suggestion(
    request: Request,
    project_id: str,
    suggestion_id: str,
    db=Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    data = await _resolve_suggestion(db, request, current_user, project_id, suggestion_id, "dismissed")
    return {"success": True, "data": data, "error": None, "meta": None}
