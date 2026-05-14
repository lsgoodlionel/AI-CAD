"""
一审 API — 技术规范化审批（项目总工）

前置条件（全部满足才能通过）：
  1. AI 审查报告已确认（ai_report_confirmed = true）
  2. BIM 碰撞检查已确认（bim_check_confirmed = true）
  3. 所有 critical/major 问题已关闭或标注已知风险（issues_all_closed = true）

驳回：填写驳回原因，图纸退回 draft
"""
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from core.workflow.drawing_state_machine import assert_valid_transition, next_state
from dependencies import get_db, get_current_user
from services.audit import write_audit
from services.notification import notify_review_task

router = APIRouter(prefix="/drawings/{drawing_id}/technical-review", tags=["review"])


class StartTechnicalReview(BaseModel):
    pass   # 启动一审无需额外参数


class SubmitTechnicalReview(BaseModel):
    result: str                          # "approved" | "rejected"
    ai_report_confirmed: bool = False
    bim_check_confirmed: bool = False
    issues_all_closed: bool = False
    notes: str = ""


async def _get_drawing_or_404(drawing_id: str, db):
    row = await db.fetch_one(
        "SELECT id, status, drawing_no, project_id FROM drawings WHERE id=$1", drawing_id
    )
    if not row:
        raise HTTPException(404, "图纸不存在")
    return dict(row)


@router.post("", status_code=201)
async def submit_technical_review(
    drawing_id: str,
    body: SubmitTechnicalReview,
    request: Request,
    db=Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    if current_user["role"] not in (
        "project_chief_engineer", "group_admin", "group_chief_engineer"
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "需要项目总工权限")

    drawing = await _get_drawing_or_404(drawing_id, db)

    if body.result == "approved":
        # ── 前置条件检查 ──────────────────────────────────────
        if not body.ai_report_confirmed:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "必须确认 AI 审查报告后才能通过一审",
            )
        if not body.issues_all_closed:
            # 检查 DB 中是否还有未关闭的 critical/major 问题
            open_critical = await db.fetch_val(
                """
                SELECT COUNT(*) FROM ai_review_issues i
                JOIN ai_review_reports r ON i.report_id = r.id
                WHERE r.drawing_id=$1
                  AND i.severity IN ('critical','major')
                  AND i.status NOT IN ('closed','waived')
                """,
                drawing_id,
            )
            if open_critical and open_critical > 0:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    f'还有 {open_critical} 个 critical/major 问题未处理，请全部关闭或标注"已知风险"',
                )

        assert_valid_transition(drawing["status"], "approve_technical")
        new_status = next_state("approve_technical")
        trigger = "approve_technical"
    else:
        assert_valid_transition(drawing["status"], "reject_technical")
        new_status = next_state("reject_technical")
        trigger = "reject_technical"

    # 写入一审记录
    await db.execute(
        """
        INSERT INTO technical_reviews
            (drawing_id, reviewer_id, result, ai_report_confirmed, bim_check_confirmed,
             issues_all_closed, notes, reviewed_at)
        VALUES ($1,$2,$3,$4,$5,$6,$7,now())
        ON CONFLICT DO NOTHING
        """,
        drawing_id, current_user["id"],
        body.result, body.ai_report_confirmed, body.bim_check_confirmed,
        body.issues_all_closed, body.notes,
    )

    # 更新图纸状态
    await db.execute(
        "UPDATE drawings SET status=$2, updated_at=now() WHERE id=$1",
        drawing_id, new_status,
    )

    await write_audit(
        db,
        user_id=current_user["id"],
        action=trigger,
        resource="drawing",
        resource_id=drawing_id,
        old_state={"status": drawing["status"]},
        new_state={"status": new_status, "result": body.result, "notes": body.notes},
        ip_address=request.client.host if request.client else None,
    )

    if body.result == "approved":
        await notify_review_task(drawing["drawing_no"], "二审（经济最优化）", "经济师")

    return {"ok": True, "new_status": new_status}
