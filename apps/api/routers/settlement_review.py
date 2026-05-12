"""
三审 API — 结算合规化

系统硬约束（不可绕过）：
  未生成《限额领料单》（material_quota_sheet IS NULL）→ 图纸无法发布

API：
  POST   /drawings/{id}/settlement-review          # 提交三审（含结算节点配置）
  POST   /drawings/{id}/settlement-review/quota    # 上传/生成限额领料单 PDF
  POST   /drawings/{id}/settlement-review/approve  # 通过三审 → published
  POST   /drawings/{id}/settlement-review/reject   # 驳回 → draft
  GET    /drawings/{id}/settlement-review          # 查看三审状态
"""
import uuid
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Request, status
from pydantic import BaseModel

from core.storage import upload_file, presigned_get_url
from core.workflow.drawing_state_machine import assert_valid_transition, next_state
from dependencies import get_db, get_current_user
from services.audit import write_audit
from services.notification import notify_drawing_published

router = APIRouter(prefix="/drawings/{drawing_id}/settlement-review", tags=["review"])


class SettlementNode(BaseModel):
    node_name: str
    description: str
    amount: float | None = None


class SubmitSettlement(BaseModel):
    settlement_nodes: list[SettlementNode] = []
    notes: str = ""


async def _get_drawing_or_404(drawing_id: str, db) -> dict:
    row = await db.fetch_one(
        "SELECT id, status, drawing_no, material_quota_sheet FROM drawings WHERE id=$1",
        drawing_id,
    )
    if not row:
        raise HTTPException(404, "图纸不存在")
    return dict(row)


def _require_pm_role(current_user: dict) -> None:
    if current_user["role"] not in (
        "project_manager", "group_admin", "group_chief_engineer"
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "需要项目经理权限")


# ── 提交三审结算节点配置 ──────────────────────────────────────

@router.post("", status_code=201)
async def submit_settlement_review(
    drawing_id: str,
    body: SubmitSettlement,
    request: Request,
    db=Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    _require_pm_role(current_user)
    drawing = await _get_drawing_or_404(drawing_id, db)
    assert_valid_transition(drawing["status"], "approve_settlement")  # 验证在 settlement_review 状态

    nodes_json = [n.model_dump() for n in body.settlement_nodes]

    existing = await db.fetch_one(
        "SELECT id FROM settlement_reviews WHERE drawing_id=$1", drawing_id
    )
    if existing:
        await db.execute(
            "UPDATE settlement_reviews SET settlement_nodes=$2, notes=$3 WHERE id=$1",
            existing["id"], nodes_json, body.notes,
        )
    else:
        await db.execute(
            """
            INSERT INTO settlement_reviews (drawing_id, pm_id, settlement_nodes, notes)
            VALUES ($1,$2,$3,$4)
            """,
            drawing_id, current_user["id"], nodes_json, body.notes,
        )

    await write_audit(
        db,
        user_id=current_user["id"],
        action="submit_settlement_nodes",
        resource="drawing",
        resource_id=drawing_id,
        new_state={"settlement_nodes_count": len(body.settlement_nodes)},
        ip_address=request.client.host if request.client else None,
    )

    return {"ok": True, "nodes_count": len(body.settlement_nodes)}


# ── 上传限额领料单 PDF ────────────────────────────────────────

@router.post("/quota")
async def upload_quota_sheet(
    drawing_id: str,
    request: Request,
    file: UploadFile = File(...),
    db=Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    _require_pm_role(current_user)
    drawing = await _get_drawing_or_404(drawing_id, db)

    content = await file.read()
    if len(content) > 50 * 1024 * 1024:
        raise HTTPException(413, "文件超过 50MB")

    object_key = f"reports/quota/{drawing_id}/{uuid.uuid4()}.pdf"
    upload_file(content, object_key, "application/pdf")

    # 更新图纸：写入 material_quota_sheet 路径（解锁发布约束）
    await db.execute(
        "UPDATE drawings SET material_quota_sheet=$2, updated_at=now() WHERE id=$1",
        drawing_id, object_key,
    )

    await write_audit(
        db,
        user_id=current_user["id"],
        action="upload_quota_sheet",
        resource="drawing",
        resource_id=drawing_id,
        new_state={"quota_sheet_key": object_key},
        ip_address=request.client.host if request.client else None,
    )

    return {"ok": True, "quota_sheet_key": object_key}


# ── 通过三审 → 发布 ───────────────────────────────────────────

@router.post("/approve")
async def approve_settlement(
    drawing_id: str,
    request: Request,
    db=Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    _require_pm_role(current_user)
    drawing = await _get_drawing_or_404(drawing_id, db)

    # ══════════════════════════════════════════════════════════
    # 核心强制约束：限额领料单未生成 → 禁止发布
    # ══════════════════════════════════════════════════════════
    if not drawing["material_quota_sheet"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "QUOTA_SHEET_MISSING",
                "message": "《限额领料单》尚未生成，图纸无法发布至班组。请先上传限额领料单 PDF。",
            },
        )

    assert_valid_transition(drawing["status"], "approve_settlement")
    new_status = next_state("approve_settlement")

    await db.execute(
        "UPDATE drawings SET status=$2, updated_at=now() WHERE id=$1",
        drawing_id, new_status,
    )

    # 记录物资经理签字时间
    await db.execute(
        "UPDATE settlement_reviews SET material_signed_at=now() WHERE drawing_id=$1",
        drawing_id,
    )

    await write_audit(
        db,
        user_id=current_user["id"],
        action="approve_settlement",
        resource="drawing",
        resource_id=drawing_id,
        old_state={"status": drawing["status"]},
        new_state={"status": new_status},
        ip_address=request.client.host if request.client else None,
    )

    # 获取项目名通知班组
    project = await db.fetch_one(
        "SELECT p.name FROM drawings d JOIN projects p ON d.project_id=p.id WHERE d.id=$1",
        drawing_id,
    )
    await notify_drawing_published(
        drawing["drawing_no"],
        project["name"] if project else "—",
    )

    return {"ok": True, "new_status": new_status}


# ── 三审驳回 ─────────────────────────────────────────────────

@router.post("/reject")
async def reject_settlement(
    drawing_id: str,
    request: Request,
    notes: str = "",
    db=Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    _require_pm_role(current_user)
    drawing = await _get_drawing_or_404(drawing_id, db)
    assert_valid_transition(drawing["status"], "reject_settlement")
    new_status = next_state("reject_settlement")

    await db.execute(
        "UPDATE drawings SET status=$2, updated_at=now() WHERE id=$1",
        drawing_id, new_status,
    )
    await write_audit(
        db,
        user_id=current_user["id"],
        action="reject_settlement",
        resource="drawing",
        resource_id=drawing_id,
        old_state={"status": drawing["status"]},
        new_state={"status": new_status, "notes": notes},
        ip_address=request.client.host if request.client else None,
    )
    return {"ok": True, "new_status": new_status}


# ── 查看三审状态 ──────────────────────────────────────────────

@router.get("")
async def get_settlement_review(
    drawing_id: str,
    db=Depends(get_db),
    _=Depends(get_current_user),
):
    row = await db.fetch_one(
        "SELECT * FROM settlement_reviews WHERE drawing_id=$1", drawing_id
    )
    drawing = await _get_drawing_or_404(drawing_id, db)

    quota_url = None
    if drawing["material_quota_sheet"]:
        quota_url = presigned_get_url(drawing["material_quota_sheet"])

    return {
        "settlement_review": dict(row) if row else None,
        "quota_sheet_uploaded": drawing["material_quota_sheet"] is not None,
        "quota_download_url": quota_url,
    }
