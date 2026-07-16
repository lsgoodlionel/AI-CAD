"""图纸信息档案读取契约 + 人审 verify(Phase E1.5-3)。

档案层单一真相源的对外接口。下游(工程信息/建模/审图/算量)读生效值:
- GET  /drawings/{id}/archive            单图档案(生效值 + 状态)
- GET  /projects/{id}/archive/elevations 全项目生效标高(建模 section-z 消费)
- GET  /projects/{id}/archive/axes       全项目生效轴网(建模配准/轴网层消费)
- POST /drawings/{id}/archive/verify     人审修正(写 verified + audit + 事件)

蓝图:docs/PHASE_E_BLUEPRINT.md §0.5 / §8.5。
"""
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from core.pipeline.events import emit_event
from dependencies import get_db, get_current_user
from services import drawing_archive
from services.audit import write_audit

router = APIRouter(tags=["drawing-archive"])

_STATUS_SQL = """
SELECT status, item_count FROM drawing_archive_status WHERE drawing_id = :drawing_id
"""
_DRAWING_PROJECT_SQL = "SELECT project_id FROM drawings WHERE id = :drawing_id"


@router.get("/drawings/{drawing_id}/archive")
async def get_drawing_archive(
    drawing_id: str,
    db=Depends(get_db),
    _user=Depends(get_current_user),
):
    """单图档案:生效值(按 category 择优)+ 抽取状态。"""
    items = await drawing_archive.fetch_drawing_archive(db, drawing_id)
    status_row = await db.fetch_one(_STATUS_SQL, {"drawing_id": drawing_id})
    return {
        "drawing_id": drawing_id,
        "status": status_row["status"] if status_row else "unknown",
        "item_count": len(items),
        "items": items,
    }


@router.get("/projects/{project_id}/archive/elevations")
async def project_elevations(
    project_id: str,
    db=Depends(get_db),
    _user=Depends(get_current_user),
):
    """全项目生效标高(建模消费:替代自跑 OCR)。"""
    rows = await drawing_archive.fetch_project_category(db, project_id, "elevation")
    return {"elevations": rows}


@router.get("/projects/{project_id}/archive/axes")
async def project_axes(
    project_id: str,
    db=Depends(get_db),
    _user=Depends(get_current_user),
):
    """全项目生效轴网(建模配准/轴网层消费)。"""
    rows = await drawing_archive.fetch_project_category(db, project_id, "axis")
    return {"axes": rows}


class VerifyRequest(BaseModel):
    category: str
    content: str
    value_json: dict | None = None
    location_json: dict | None = None
    supersedes_id: str | None = None


@router.post("/drawings/{drawing_id}/archive/verify")
async def verify_archive_item(
    drawing_id: str,
    body: VerifyRequest,
    db=Depends(get_db),
    user=Depends(get_current_user),
):
    """人审修正:写 verified 行(置原 auto 失活)+ audit + 发档案变更事件。

    事件 archive.verified → pipeline 消费 → 建模增量重建(顺序推进)。
    """
    drawing = await db.fetch_one(_DRAWING_PROJECT_SQL, {"drawing_id": drawing_id})
    if drawing is None:
        raise HTTPException(404, "DRAWING_NOT_FOUND")
    project_id = str(drawing["project_id"])

    # 取被修正的原 auto 行原值 → 算稳定 supersedes_key(跨重抽抑制复活)
    superseded_content = None
    superseded_value_json = None
    if body.supersedes_id:
        orig = await db.fetch_one(
            "SELECT content, value_json FROM drawing_extracted_info WHERE id = :id",
            {"id": body.supersedes_id},
        )
        if orig:
            superseded_content = orig["content"]
            sv = orig["value_json"]
            superseded_value_json = json.loads(sv) if isinstance(sv, str) else sv

    params = drawing_archive.build_verify_params(
        project_id=project_id,
        drawing_id=drawing_id,
        category=body.category,
        content=body.content,
        value_json=body.value_json,
        location_json=body.location_json,
        supersedes_id=body.supersedes_id,
        reviewer_id=str(user["id"]),
        superseded_content=superseded_content,
        superseded_value_json=superseded_value_json,
    )
    await drawing_archive.persist_verify(db, params)

    await write_audit(
        db, user_id=str(user["id"]), action="archive.verify",
        resource="drawing", resource_id=drawing_id,
        new_state={"category": body.category, "content": body.content,
                   "supersedes": body.supersedes_id},
    )
    await emit_event(
        db, event_type="archive.verified", project_id=project_id,
        source_id=drawing_id,
        payload={"category": body.category, "reviewer": str(user["id"])},
    )
    return {"ok": True, "drawing_id": drawing_id, "category": body.category}
