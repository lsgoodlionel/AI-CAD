"""
独立会审审图模块 API（Agent C）

- POST /api/v1/drawing-review/audit         单条纯文本会审审查
- POST /api/v1/drawing-review/audit-batch   批量会审审查（≤ MAX_BATCH_ITEMS）
- GET  /api/v1/drawing-review/records        分页查询已持久化记录

复用纯函数引擎 `core.ai_review.review_audit.engine.audit_text`（无 db / 无 LLM 可跑）。
默认不持久化，便于无库环境测试；显式 persist=True 时写入
review_audit_records + review_audit_findings。
统一信封：{success, data, error}（分页另含 meta）。
"""
import json
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi import status as http_status
from pydantic import BaseModel, Field

from core.ai_review.review_audit.engine import audit_text
from dependencies import get_db, get_current_user

router = APIRouter(prefix="/drawing-review", tags=["drawing-review"])

MAX_BATCH_ITEMS = 200


# ── 请求模型 ──────────────────────────────────────────────────

class AuditRequest(BaseModel):
    discipline: str | None = None
    title: str
    body: str
    doc_type: str | None = None
    source_db: str | None = None
    related_disciplines: list[str] | None = None
    persist: bool = False
    project_id: str | None = None


class BatchAuditRequest(BaseModel):
    items: list[AuditRequest] = Field(default_factory=list)
    persist: bool = False


class DocumentRequest(BaseModel):
    title: str
    body: str
    discipline: str | None = None
    doc_kind: Literal["minutes", "reply"]


# ── 统一信封 ──────────────────────────────────────────────────

def _ok(data: Any, meta: dict | None = None) -> dict:
    envelope: dict[str, Any] = {"success": True, "data": data, "error": None}
    if meta is not None:
        envelope["meta"] = meta
    return envelope


def _validate_text(title: str, body: str) -> None:
    """系统边界输入校验：title/body 非空，fail fast。"""
    if not (title and title.strip()):
        raise HTTPException(http_status.HTTP_422_UNPROCESSABLE_ENTITY, "title 不能为空")
    if not (body and body.strip()):
        raise HTTPException(http_status.HTTP_422_UNPROCESSABLE_ENTITY, "body 不能为空")


def _run_audit(req: AuditRequest) -> dict:
    """调用纯函数引擎，返回契约第3节 data 结构。"""
    return audit_text(
        req.title,
        req.body,
        discipline=req.discipline,
        doc_type=req.doc_type,
    )


async def _persist(db, req: AuditRequest, result: dict, user_id: str) -> str:
    """写入 review_audit_records + review_audit_findings，返回 record_id。"""
    judgement = result.get("专业判断", {}) or {}
    interface = result.get("接口复核", {}) or {}
    risk = result.get("风险等级", {}) or {}
    questions = result.get("标准问题", []) or []

    # V2 section（缺省为空，向后兼容 V1 引擎输出）
    obj = result.get("对象识别", {}) or {}
    scenario = result.get("场景识别", {}) or {}
    question_pack = result.get("问题包", {}) or {}
    document = result.get("文书输出", {}) or {}
    doc_minutes = document.get("会审纪要口径", []) or []
    doc_reply = document.get("设计答复口径", []) or []

    record = await db.fetch_one(
        """
        INSERT INTO review_audit_records
            (project_id, discipline_code, title, body, doc_type, source_db, created_by)
        VALUES ($1,$2,$3,$4,$5,$6,$7)
        RETURNING id
        """,
        req.project_id,
        judgement.get("code"),
        req.title,
        req.body,
        req.doc_type,
        req.source_db,
        user_id,
    )
    record_id = record["id"]

    await db.execute(
        """
        INSERT INTO review_audit_findings
            (record_id, discipline_code, discipline_name, location_json, concerns,
             issue_class, interface_primary, interface_related, risk_level,
             object_level, standard_question, evidence_gap, raw_output,
             object_name, object_basis, scenario, scenario_reason,
             question_pack, doc_minutes, doc_reply)
        VALUES ($1,$2,$3,CAST($4 AS jsonb),CAST($5 AS jsonb),CAST($6 AS jsonb),$7,
                CAST($8 AS jsonb),$9,$10,$11,CAST($12 AS jsonb),CAST($13 AS jsonb),
                $14,$15,$16,$17,
                CAST($18 AS jsonb),CAST($19 AS jsonb),CAST($20 AS jsonb))
        """,
        record_id,
        judgement.get("code"),
        judgement.get("name"),
        json.dumps(result.get("定位信息", {}), ensure_ascii=False),
        json.dumps(result.get("核心concern", []), ensure_ascii=False),
        json.dumps(result.get("问题归类", []), ensure_ascii=False),
        interface.get("primary"),
        json.dumps(interface.get("related", []), ensure_ascii=False),
        risk.get("level"),
        result.get("object_level") or obj.get("level"),
        questions[0] if questions else None,
        json.dumps(result.get("证据缺口", []), ensure_ascii=False),
        json.dumps(result, ensure_ascii=False),
        obj.get("object"),
        obj.get("basis"),
        scenario.get("name"),
        scenario.get("priority_reason"),
        json.dumps(question_pack, ensure_ascii=False),
        json.dumps(doc_minutes, ensure_ascii=False),
        json.dumps(doc_reply, ensure_ascii=False),
    )
    return str(record_id)


# ── 单条会审审查 ──────────────────────────────────────────────

@router.post("/audit")
async def audit(
    req: AuditRequest,
    persist: bool = Query(False, description="是否持久化（亦可在请求体设置 persist）"),
    db=Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """单条纯文本会审审查。"""
    _validate_text(req.title, req.body)
    result = _run_audit(req)

    should_persist = persist or req.persist
    if should_persist:
        try:
            record_id = await _persist(db, req, result, current_user["id"])
            result = {**result, "record_id": record_id}
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(
                http_status.HTTP_500_INTERNAL_SERVER_ERROR,
                "会审记录持久化失败",
            )
    return _ok(result)


# ── 批量会审审查 ──────────────────────────────────────────────

@router.post("/audit-batch")
async def audit_batch(
    req: BatchAuditRequest,
    db=Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """批量会审审查，items 非空且长度 ≤ MAX_BATCH_ITEMS。"""
    if not req.items:
        raise HTTPException(
            http_status.HTTP_422_UNPROCESSABLE_ENTITY, "items 不能为空"
        )
    if len(req.items) > MAX_BATCH_ITEMS:
        raise HTTPException(
            http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"items 数量超过上限 {MAX_BATCH_ITEMS}",
        )

    results: list[dict] = []
    for item in req.items:
        _validate_text(item.title, item.body)
        result = _run_audit(item)
        if req.persist or item.persist:
            try:
                record_id = await _persist(db, item, result, current_user["id"])
                result = {**result, "record_id": record_id}
            except Exception:
                raise HTTPException(
                    http_status.HTTP_500_INTERNAL_SERVER_ERROR,
                    "会审记录持久化失败",
                )
        results.append(result)
    return _ok(results)


# ── 文书化口径输出 ────────────────────────────────────────────

_DOC_KIND_KEY: dict[str, str] = {
    "minutes": "会审纪要口径",
    "reply": "设计答复口径",
}


@router.post("/document")
async def document(
    req: DocumentRequest,
    _=Depends(get_current_user),
):
    """生成会审纪要口径（minutes）或设计答复口径（reply）。

    复用 audit_text，从 V2 section `文书输出` 取对应口径条目。
    无 db 依赖：纯文本审查不持久化。
    """
    _validate_text(req.title, req.body)
    if req.doc_kind not in _DOC_KIND_KEY:
        raise HTTPException(
            http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            "doc_kind 必须为 minutes 或 reply",
        )

    result = audit_text(req.title, req.body, discipline=req.discipline)

    document_section = result.get("文书输出", {}) or {}
    items = document_section.get(_DOC_KIND_KEY[req.doc_kind], []) or []

    data = {
        "doc_kind": req.doc_kind,
        "items": items,
        "对象识别": result.get("对象识别", {}) or {},
        "场景识别": result.get("场景识别", {}) or {},
    }
    return _ok(data)


# ── 分页查询已持久化记录 ──────────────────────────────────────

@router.get("/records")
async def list_records(
    project_id: str | None = Query(None),
    discipline_code: str | None = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db=Depends(get_db),
    _=Depends(get_current_user),
):
    """分页查询已持久化会审记录，meta 含 total/page/limit。"""
    conditions: list[str] = []
    args: list = []
    idx = 1
    if project_id:
        conditions.append(f"r.project_id=${idx}")
        args.append(project_id)
        idx += 1
    if discipline_code:
        conditions.append(f"r.discipline_code=${idx}")
        args.append(discipline_code)
        idx += 1

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    offset = (page - 1) * limit
    rows = await db.fetch_all(
        f"""
        SELECT r.id, r.project_id, r.discipline_code, r.title, r.doc_type,
               r.source_db, r.created_by, r.created_at
        FROM review_audit_records r
        {where}
        ORDER BY r.created_at DESC
        LIMIT ${idx} OFFSET ${idx + 1}
        """,
        *args, limit, offset,
    )
    total = await db.fetch_val(
        f"SELECT COUNT(*) FROM review_audit_records r {where}", *args
    )
    return _ok(
        [dict(row) for row in rows],
        meta={"total": total or 0, "page": page, "limit": limit},
    )
