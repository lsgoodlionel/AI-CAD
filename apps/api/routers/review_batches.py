"""套图审查 API（批量 / 整套工程审图编排）

- POST   /review-batches        创建批次：显式 drawing_ids 或缺省 full_set（项目内 draft/ai_done 图纸）
- GET    /review-batches        批次列表（按项目过滤 + 分页）
- GET    /review-batches/{id}   批次详情（含每张图报告状态与进度聚合）

创建时对每张可触发图纸复用单张上传路径：报告占位 → 状态 ai_reviewing →
run_ai_review.delay，最后 finalize_batch_review.delay 轮询汇总。
蓝图：docs/BATCH_REVIEW_BLUEPRINT.md 第 4.4 节。
"""
import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from dependencies import get_db, get_current_user
from services.audit import write_audit
from services.ai_review_progress import estimate_total_seconds, progress_payload
from tasks.ai_review import run_ai_review
from tasks.batch_review import finalize_batch_review

router = APIRouter(prefix="/review-batches", tags=["review-batches"])

VALID_SCOPES = ("single", "multi", "full_set")
REVIEWABLE_STATUSES = ("draft", "ai_done")


class CreateBatchBody(BaseModel):
    """创建套图审查批次请求体"""
    project_id: str
    drawing_ids: list[str] | None = None
    scope: str | None = None


def _parse_jsonb(value: Any, default: Any) -> Any:
    """JSONB 字段经驱动可能返回 str，安全解析。"""
    if value is None:
        return default
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return default
    return value


def _batch_to_dict(row: Any) -> dict:
    """review_batches 行 → 可序列化 dict（解析 JSONB 列）。"""
    item = dict(row)
    item["drawing_ids"] = _parse_jsonb(item.get("drawing_ids"), [])
    item["summary"] = _parse_jsonb(item.get("summary"), None)
    item["cross_findings"] = _parse_jsonb(item.get("cross_findings"), None)
    return item


def _resolve_scope(body: CreateBatchBody) -> str:
    """校验 / 推导批次范围：显式列表按张数定 single/multi，缺省 full_set。"""
    if body.scope is not None:
        if body.scope not in VALID_SCOPES:
            raise HTTPException(400, "INVALID_SCOPE")
        return body.scope
    if not body.drawing_ids:
        return "full_set"
    return "single" if len(body.drawing_ids) == 1 else "multi"


async def _fetch_batch_drawings(db, body: CreateBatchBody) -> list[dict]:
    """取批次目标图纸；full_set 只取 draft/ai_done，显式列表需全部属于该项目。"""
    if body.drawing_ids:
        rows = await db.fetch_all(
            """
            SELECT id, status, file_size_kb FROM drawings
            WHERE project_id=$1 AND id::text = ANY($2)
            """,
            body.project_id, [str(did) for did in body.drawing_ids],
        )
        if len(rows) != len(set(body.drawing_ids)):
            raise HTTPException(400, "DRAWING_NOT_IN_PROJECT")
        return [dict(row) for row in rows]

    rows = await db.fetch_all(
        """
        SELECT id, status, file_size_kb FROM drawings
        WHERE project_id=$1 AND status = ANY($2)
        """,
        body.project_id, list(REVIEWABLE_STATUSES),
    )
    return [dict(row) for row in rows]


async def _trigger_single_review(db, drawing: dict) -> None:
    """复用单张上传路径：报告占位 → ai_reviewing → 初始进度 → Celery 触发。"""
    drawing_id = str(drawing["id"])
    report = await db.fetch_one(
        "INSERT INTO ai_review_reports (drawing_id, status) VALUES ($1,'pending') RETURNING id",
        drawing_id,
    )
    await db.execute(
        "UPDATE drawings SET status='ai_reviewing', updated_at=now() WHERE id=$1",
        drawing_id,
    )
    initial_progress = progress_payload(
        status="processing",
        stage_key="queued",
        started_at=datetime.now(timezone.utc),
        completed_keys=[],
        active_keys=["queued"],
        estimated_total_seconds=estimate_total_seconds(drawing.get("file_size_kb") or 0),
    )
    await db.execute(
        """
        UPDATE ai_review_reports
        SET status='processing',
            engine_results=jsonb_build_object('progress', CAST($2 AS jsonb))
        WHERE id=$1
        """,
        report["id"],
        json.dumps(initial_progress, ensure_ascii=False),
    )
    run_ai_review.delay(drawing_id)


# ── 创建批次 ──────────────────────────────────────────────────

@router.post("", status_code=201)
async def create_review_batch(
    request: Request,
    body: CreateBatchBody,
    db=Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    scope = _resolve_scope(body)

    project = await db.fetch_one("SELECT id FROM projects WHERE id=$1", body.project_id)
    if project is None:
        raise HTTPException(404, "PROJECT_NOT_FOUND")

    drawings = await _fetch_batch_drawings(db, body)
    if not drawings:
        raise HTTPException(400, "NO_REVIEWABLE_DRAWINGS")

    drawing_ids = [str(d["id"]) for d in drawings]
    batch = await db.fetch_one(
        """
        INSERT INTO review_batches (project_id, scope, drawing_ids, status, created_by)
        VALUES ($1,$2,CAST($3 AS jsonb),'processing',$4)
        RETURNING id
        """,
        body.project_id, scope, json.dumps(drawing_ids), current_user["id"],
    )
    batch_id = str(batch["id"])

    triggered = 0
    for drawing in drawings:
        if drawing["status"] == "ai_reviewing":
            continue   # 已在审：计入批次但不重复触发
        await _trigger_single_review(db, drawing)
        triggered += 1

    await write_audit(
        db,
        user_id=current_user["id"],
        action="create_review_batch",
        resource="review_batch",
        resource_id=batch_id,
        new_state={"scope": scope, "total": len(drawings), "triggered": triggered},
        ip_address=request.client.host if request.client else None,
    )
    finalize_batch_review.delay(batch_id)

    return {"batch_id": batch_id, "scope": scope, "total": len(drawings), "triggered": triggered}


# ── 批次列表 ──────────────────────────────────────────────────

@router.get("")
async def list_review_batches(
    project_id: str | None = Query(None),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db=Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    where = "WHERE project_id=$1" if project_id else ""
    params = [project_id] if project_id else []
    rows = await db.fetch_all(
        f"""
        SELECT id, project_id, scope, drawing_ids, status, summary,
               cross_findings, created_by, created_at, completed_at
        FROM review_batches {where}
        ORDER BY created_at DESC
        LIMIT ${len(params) + 1} OFFSET ${len(params) + 2}
        """,
        *params, limit, offset,
    )
    total = await db.fetch_val(
        f"SELECT COUNT(*) FROM review_batches {where}", *params
    )
    return {"items": [_batch_to_dict(row) for row in rows], "total": total or 0}


# ── 批次详情 ──────────────────────────────────────────────────

@router.get("/{batch_id}")
async def get_review_batch(
    batch_id: str,
    db=Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    row = await db.fetch_one(
        """
        SELECT id, project_id, scope, drawing_ids, status, summary,
               cross_findings, created_by, created_at, completed_at
        FROM review_batches WHERE id=$1
        """,
        batch_id,
    )
    if row is None:
        raise HTTPException(404, "BATCH_NOT_FOUND")

    batch = _batch_to_dict(row)
    items = await db.fetch_all(
        """
        SELECT d.id AS drawing_id, d.drawing_no, d.title, d.discipline,
               r.status AS report_status,
               COALESCE(r.total_issues, 0) AS total_issues,
               COALESCE(r.critical_issues, 0) AS critical_issues
        FROM drawings d
        LEFT JOIN LATERAL (
            SELECT status, total_issues, critical_issues
            FROM ai_review_reports WHERE drawing_id = d.id
            ORDER BY created_at DESC LIMIT 1
        ) r ON TRUE
        WHERE d.id::text = ANY($1)
        """,
        [str(did) for did in batch["drawing_ids"]],
    )

    progress = {"total": len(items), "done": 0, "failed": 0, "processing": 0}
    for item in items:
        status = item["report_status"]
        key = status if status in ("done", "failed") else "processing"
        progress[key] += 1

    return {"batch": batch, "items": [dict(item) for item in items], "progress": progress}
