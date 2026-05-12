"""
二审 API — 经济最优化【一票否决核心节点】

系统硬约束（不可绕过）：
  1. 方案数 < 2 → 拒绝提交（422）
  2. 经济师未完成在线签字 → 任何推进请求返回 HTTP 403
     错误码：ECONOMIC_REVIEW_NOT_SIGNED

API 设计：
  POST   /drawings/{id}/economic-review           # 提交方案对比表
  POST   /drawings/{id}/economic-review/sign      # 经济师在线签字
  POST   /drawings/{id}/economic-review/approve   # 签字后推进至三审
  POST   /drawings/{id}/economic-review/reject    # 驳回退回 draft
  GET    /drawings/{id}/economic-review           # 查看当前二审数据
"""
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, field_validator

from core.workflow.drawing_state_machine import assert_valid_transition, next_state
from dependencies import get_db, get_current_user, require_economist
from services.audit import write_audit
from services.notification import notify_economic_signed, notify_review_task

router = APIRouter(prefix="/drawings/{drawing_id}/economic-review", tags=["review"])


class AlternativeSchema(BaseModel):
    option_id: str                   # "A" | "B" | "C"
    description: str
    cost_est: float                  # 预估成本（元）
    notes: str = ""


class SubmitEconomicReview(BaseModel):
    alternatives: list[AlternativeSchema]
    selected_option: str | None = None
    total_saving_est: float | None = None
    notes: str = ""

    @field_validator("alternatives")
    @classmethod
    def at_least_two(cls, v: list) -> list:
        if len(v) < 2:
            raise ValueError("必须提交至少 2 种方案进行经济对比")
        return v


class SignRequest(BaseModel):
    confirm: bool = False            # 前端二次确认弹窗


async def _get_drawing_or_404(drawing_id: str, db) -> dict:
    row = await db.fetch_one(
        "SELECT id, status, drawing_no, created_by FROM drawings WHERE id=$1",
        drawing_id,
    )
    if not row:
        raise HTTPException(404, "图纸不存在")
    return dict(row)


async def _get_review_or_none(drawing_id: str, db) -> dict | None:
    row = await db.fetch_one(
        "SELECT * FROM economic_reviews WHERE drawing_id=$1 ORDER BY created_at DESC LIMIT 1",
        drawing_id,
    )
    return dict(row) if row else None


# ── 提交方案对比表 ────────────────────────────────────────────

@router.post("", status_code=201)
async def submit_economic_alternatives(
    drawing_id: str,
    body: SubmitEconomicReview,
    request: Request,
    db=Depends(get_db),
    current_user: dict = Depends(require_economist),
):
    drawing = await _get_drawing_or_404(drawing_id, db)
    assert_valid_transition(drawing["status"], "approve_economic")  # 确认在 economic_review 状态

    alts_json = [a.model_dump() for a in body.alternatives]

    existing = await _get_review_or_none(drawing_id, db)
    if existing:
        # 更新（未签字前允许修改）
        if existing["economist_signed_at"]:
            raise HTTPException(400, "已完成签字，不允许修改方案")
        await db.execute(
            """
            UPDATE economic_reviews
            SET alternatives=$2, selected_option=$3, total_saving_est=$4, notes=$5
            WHERE id=$1
            """,
            existing["id"], alts_json, body.selected_option,
            body.total_saving_est, body.notes,
        )
        review_id = existing["id"]
    else:
        row = await db.fetch_one(
            """
            INSERT INTO economic_reviews
                (drawing_id, alternatives, selected_option, total_saving_est, notes)
            VALUES ($1,$2,$3,$4,$5) RETURNING id
            """,
            drawing_id, alts_json, body.selected_option,
            body.total_saving_est, body.notes,
        )
        review_id = row["id"]

    await write_audit(
        db,
        user_id=current_user["id"],
        action="submit_economic_alternatives",
        resource="drawing",
        resource_id=drawing_id,
        new_state={"alternatives_count": len(body.alternatives)},
        ip_address=request.client.host if request.client else None,
    )

    return {"review_id": str(review_id), "alternatives_count": len(body.alternatives)}


# ── 经济师在线签字【核心约束解锁节点】────────────────────────

@router.post("/sign")
async def sign_economic_review(
    drawing_id: str,
    body: SignRequest,
    request: Request,
    db=Depends(get_db),
    current_user: dict = Depends(require_economist),
):
    if not body.confirm:
        raise HTTPException(400, "需要前端二次确认（confirm=true）")

    drawing = await _get_drawing_or_404(drawing_id, db)
    review = await _get_review_or_none(drawing_id, db)

    if not review:
        raise HTTPException(400, "请先提交方案对比表，再进行签字")
    if len(review["alternatives"]) < 2:
        raise HTTPException(422, "方案数量不足 2 个，不可签字")
    if review["economist_signed_at"]:
        raise HTTPException(400, "已完成签字，不可重复操作")
    if not review["selected_option"]:
        raise HTTPException(422, "请先选定经济最优方案再签字")

    # 执行签字：记录 economist_id + signed_at（系统时间）
    await db.execute(
        """
        UPDATE economic_reviews
        SET economist_id=$2, economist_signed_at=now()
        WHERE id=$1
        """,
        review["id"], current_user["id"],
    )

    await write_audit(
        db,
        user_id=current_user["id"],
        action="sign_economic_review",
        resource="drawing",
        resource_id=drawing_id,
        new_state={"signed": True, "economist_id": str(current_user["id"])},
        ip_address=request.client.host if request.client else None,
    )

    await notify_economic_signed(drawing["drawing_no"], current_user.get("display_name", "经济师"))

    return {"ok": True, "message": "签字成功，图纸已解锁，可进入三审"}


# ── 推进至三审（签字后操作）────────────────────────────────────

@router.post("/approve")
async def approve_economic_review(
    drawing_id: str,
    request: Request,
    db=Depends(get_db),
    current_user: dict = Depends(require_economist),
):
    drawing = await _get_drawing_or_404(drawing_id, db)
    review = await _get_review_or_none(drawing_id, db)

    # ══════════════════════════════════════════════════════════
    # 核心强制约束：经济师未签字 → HTTP 403
    # 错误码：ECONOMIC_REVIEW_NOT_SIGNED
    # 前端根据此错误码显示"经济师签字"提示，三审入口灰色禁用
    # ══════════════════════════════════════════════════════════
    if not review or not review["economist_signed_at"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "ECONOMIC_REVIEW_NOT_SIGNED",
                "message": "经济师尚未完成在线签字，图纸无法进入三审。请联系经济师完成签字后再操作。",
            },
        )

    assert_valid_transition(drawing["status"], "approve_economic")
    new_status = next_state("approve_economic")

    await db.execute(
        "UPDATE drawings SET status=$2, updated_at=now() WHERE id=$1",
        drawing_id, new_status,
    )

    await write_audit(
        db,
        user_id=current_user["id"],
        action="approve_economic",
        resource="drawing",
        resource_id=drawing_id,
        old_state={"status": drawing["status"]},
        new_state={"status": new_status},
        ip_address=request.client.host if request.client else None,
    )

    await notify_review_task(drawing["drawing_no"], "三审（结算合规化）", "项目经理 / 物资经理")

    return {"ok": True, "new_status": new_status}


# ── 二审驳回 ────────────────────────────────────────────────

@router.post("/reject")
async def reject_economic_review(
    drawing_id: str,
    request: Request,
    notes: str = "",
    db=Depends(get_db),
    current_user: dict = Depends(require_economist),
):
    drawing = await _get_drawing_or_404(drawing_id, db)
    assert_valid_transition(drawing["status"], "reject_economic")
    new_status = next_state("reject_economic")

    await db.execute(
        "UPDATE drawings SET status=$2, updated_at=now() WHERE id=$1",
        drawing_id, new_status,
    )
    await write_audit(
        db,
        user_id=current_user["id"],
        action="reject_economic",
        resource="drawing",
        resource_id=drawing_id,
        old_state={"status": drawing["status"]},
        new_state={"status": new_status, "notes": notes},
        ip_address=request.client.host if request.client else None,
    )

    return {"ok": True, "new_status": new_status}


# ── 查看当前二审数据 ────────────────────────────────────────

@router.get("")
async def get_economic_review(
    drawing_id: str,
    db=Depends(get_db),
    _=Depends(get_current_user),
):
    review = await _get_review_or_none(drawing_id, db)
    if not review:
        return None
    # 敏感字段脱敏：不暴露 economist_id 的其他信息
    return {
        "id": str(review["id"]),
        "alternatives": review["alternatives"],
        "selected_option": review["selected_option"],
        "total_saving_est": review["total_saving_est"],
        "economist_signed": review["economist_signed_at"] is not None,
        "economist_signed_at": str(review["economist_signed_at"]) if review["economist_signed_at"] else None,
        "notes": review["notes"],
    }
