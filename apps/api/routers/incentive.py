"""
创效激励提案 API

状态流转（单向）：
  draft → calculating → pending_sign → public_notice → distributing → approved → paid
  任意非终态 → rejected

铁三角比例（不可绕过）：集团 20% / 项目部 50% / 提案人 30%
公示期：7 自然日（硬编码）
"""
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import Response
from pydantic import BaseModel, Field

from dependencies import get_db, get_current_user
from services.audit import write_audit
from services.bonus_calculator import calculate, amounts_from_snapshot
from services.certificate_generator import generate_certificate
from services.notification import (
    notify_proposal_submitted,
    notify_proposal_sign_required,
    notify_proposal_public_notice,
    notify_proposal_approved,
)

router = APIRouter(prefix="/incentive/proposals", tags=["incentive"])

PUBLIC_NOTICE_DAYS = 7  # 公示期（日）

# 需要签字的岗位（任意满足即可代表该角色）
_SIGN_ROLES = {
    "project_manager": {"project_manager", "group_admin"},
    "economist":       {"economist", "group_admin", "group_commercial_director"},
}

# ──────────────────────────── Pydantic Schema ────────────────

class SubmitProposalBody(BaseModel):
    project_id:    str
    drawing_id:    str | None = None
    proposal_type: str = Field(..., pattern="^[AB]$")
    title:         str = Field(..., min_length=2, max_length=300)
    description:   str = Field(..., min_length=10)
    raw_saving_est: float | None = None


class CalculateBody(BaseModel):
    net_saving:  float = Field(..., gt=0)
    bonus_rate:  float = Field(0.15, ge=0.01, le=0.50)
    notes:       str = ""


class SignBody(BaseModel):
    comment: str = ""


class DistributeBody(BaseModel):
    team_breakdown: list[dict] = []   # [{user_id, display_name, amount}]


class RejectBody(BaseModel):
    reason: str = Field(..., min_length=2)


# ──────────────────────────── 辅助函数 ────────────────────────

async def _get_proposal_or_404(proposal_id: str, db) -> dict:
    row = await db.fetch_one(
        "SELECT * FROM incentive_proposals WHERE id=$1", proposal_id
    )
    if not row:
        raise HTTPException(404, "提案不存在")
    return dict(row)


def _require_status(proposal: dict, *allowed: str) -> None:
    if proposal["status"] not in allowed:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"当前状态「{proposal['status']}」不支持此操作",
        )


def _require_role(user: dict, *roles: str) -> None:
    if user["role"] not in roles:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "角色权限不足")


async def _has_signed(db, proposal_id: str, role_key: str) -> bool:
    row = await db.fetch_one(
        "SELECT id FROM proposal_approvals WHERE proposal_id=$1 AND role=$2 AND signed_at IS NOT NULL",
        proposal_id, role_key,
    )
    return row is not None


# ──────────────────────────── 提交提案 ───────────────────────

@router.post("", status_code=201)
async def submit_proposal(
    body: SubmitProposalBody,
    request: Request,
    db=Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    row = await db.fetch_one(
        """
        INSERT INTO incentive_proposals
            (project_id, drawing_id, proposer_id, proposal_type,
             title, description, raw_saving_est)
        VALUES ($1,$2,$3,$4,$5,$6,$7) RETURNING id
        """,
        body.project_id, body.drawing_id, current_user["id"],
        body.proposal_type, body.title, body.description, body.raw_saving_est,
    )
    proposal_id = str(row["id"])
    await write_audit(
        db, user_id=current_user["id"], action="submit_proposal",
        resource="proposal", resource_id=proposal_id,
        new_state={"type": body.proposal_type, "title": body.title},
        ip_address=request.client.host if request.client else None,
    )

    # 查询项目名称用于通知
    proj = await db.fetch_one("SELECT name FROM projects WHERE id=$1", body.project_id)
    project_name = proj["name"] if proj else ""
    await notify_proposal_submitted(body.title, current_user.get("display_name", ""), project_name)

    return {"proposal_id": proposal_id, "status": "draft"}


# ──────────────────────────── 列表 & 详情 ────────────────────

@router.get("")
async def list_proposals(
    project_id: str | None = None,
    proposal_type: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
    db=Depends(get_db),
    _=Depends(get_current_user),
):
    conditions, args = [], []
    idx = 1
    for col, val in [("p.project_id", project_id),
                     ("p.proposal_type", proposal_type),
                     ("p.status", status)]:
        if val:
            conditions.append(f"{col}=${idx}")
            args.append(val)
            idx += 1
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    rows = await db.fetch_all(
        f"""
        SELECT p.id, p.proposal_type, p.title, p.status, p.raw_saving_est,
               p.net_saving, p.created_at, p.updated_at,
               u.display_name AS proposer_name,
               pr.name AS project_name
        FROM incentive_proposals p
        JOIN users u ON p.proposer_id = u.id
        JOIN projects pr ON p.project_id = pr.id
        {where}
        ORDER BY p.created_at DESC
        LIMIT ${idx} OFFSET ${idx+1}
        """,
        *args, limit, offset,
    )
    total = await db.fetch_val(
        f"SELECT COUNT(*) FROM incentive_proposals p {where}", *args
    )
    return {"items": [dict(r) for r in rows], "total": total}


@router.get("/{proposal_id}")
async def get_proposal(
    proposal_id: str,
    db=Depends(get_db),
    _=Depends(get_current_user),
):
    proposal = await _get_proposal_or_404(proposal_id, db)

    approvals = await db.fetch_all(
        "SELECT role, signed_at, comment FROM proposal_approvals WHERE proposal_id=$1 ORDER BY created_at",
        proposal_id,
    )
    distribution = await db.fetch_one(
        "SELECT * FROM bonus_distributions WHERE proposal_id=$1", proposal_id
    )
    return {
        **proposal,
        "approvals": [dict(a) for a in approvals],
        "distribution": dict(distribution) if distribution else None,
    }


# ──────────────────────────── 经济核算 ────────────────────────

@router.post("/{proposal_id}/calculate")
async def calculate_saving(
    proposal_id: str,
    body: CalculateBody,
    request: Request,
    db=Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    _require_role(current_user, "economist", "group_admin", "group_commercial_director")
    proposal = await _get_proposal_or_404(proposal_id, db)
    _require_status(proposal, "draft", "calculating")

    snapshot = calculate(
        net_saving=Decimal(str(body.net_saving)),
        bonus_rate=Decimal(str(body.bonus_rate)),
        calculated_by=str(current_user["id"]),
    )

    await db.execute(
        """
        UPDATE incentive_proposals
        SET net_saving=$2, cost_snapshot=$3::jsonb, status='pending_sign', updated_at=now()
        WHERE id=$1
        """,
        proposal_id, body.net_saving, json.dumps(snapshot),
    )
    await write_audit(
        db, user_id=current_user["id"], action="calculate_saving",
        resource="proposal", resource_id=proposal_id,
        new_state={"net_saving": body.net_saving, "bonus_pool": snapshot["bonus_pool"]},
        ip_address=request.client.host if request.client else None,
    )
    return {"ok": True, "snapshot": snapshot}


# ──────────────────────────── 多方签字 ────────────────────────

@router.post("/{proposal_id}/sign")
async def sign_proposal(
    proposal_id: str,
    body: SignBody,
    request: Request,
    db=Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    proposal = await _get_proposal_or_404(proposal_id, db)
    _require_status(proposal, "pending_sign")

    role = current_user["role"]
    # 判断该用户代表哪个签字岗位
    role_key = None
    for key, valid_roles in _SIGN_ROLES.items():
        if role in valid_roles:
            role_key = key
            break
    if role_key is None:
        raise HTTPException(403, "当前角色不在签字岗位列表中")

    if await _has_signed(db, proposal_id, role_key):
        raise HTTPException(400, f"「{role_key}」岗位已完成签字")

    await db.execute(
        """
        INSERT INTO proposal_approvals (proposal_id, role, approver_id, signed_at, comment)
        VALUES ($1,$2,$3,now(),$4)
        ON CONFLICT DO NOTHING
        """,
        proposal_id, role_key, current_user["id"], body.comment,
    )

    # 检查是否所有必要角色均已签字
    all_signed = all(
        [await _has_signed(db, proposal_id, k) for k in _SIGN_ROLES]
    )
    new_status = proposal["status"]
    if all_signed:
        notice_ends = datetime.now(timezone.utc) + timedelta(days=PUBLIC_NOTICE_DAYS)
        await db.execute(
            "UPDATE incentive_proposals SET status='public_notice', notice_ends_at=$2, updated_at=now() WHERE id=$1",
            proposal_id, notice_ends,
        )
        new_status = "public_notice"

    await write_audit(
        db, user_id=current_user["id"], action="sign_proposal",
        resource="proposal", resource_id=proposal_id,
        new_state={"role_key": role_key, "all_signed": all_signed},
        ip_address=request.client.host if request.client else None,
    )

    if all_signed:
        await notify_proposal_public_notice(
            proposal["title"], "", PUBLIC_NOTICE_DAYS
        )
    else:
        # 通知下一个待签字角色
        unsigned = [k for k in _SIGN_ROLES if not await _has_signed(db, proposal_id, k)]
        if unsigned:
            role_labels = {"project_manager": "项目经理", "economist": "经济师"}
            label = role_labels.get(unsigned[0], unsigned[0])
            await notify_proposal_sign_required(proposal["title"], label)

    return {"ok": True, "new_status": new_status, "all_signed": all_signed}


# ──────────────────────────── 奖金分配 ────────────────────────

@router.post("/{proposal_id}/distribute")
async def distribute_bonus(
    proposal_id: str,
    body: DistributeBody,
    request: Request,
    db=Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    _require_role(current_user, "group_admin", "group_chief_engineer")
    proposal = await _get_proposal_or_404(proposal_id, db)
    _require_status(proposal, "public_notice")

    # 公示期必须结束
    notice_ends = proposal.get("notice_ends_at")
    if notice_ends and datetime.now(timezone.utc) < notice_ends:
        remaining = (notice_ends - datetime.now(timezone.utc)).days + 1
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"公示期尚未结束，还剩 {remaining} 天",
        )

    snapshot = proposal.get("cost_snapshot")
    if not snapshot:
        raise HTTPException(400, "缺少核算快照，请先完成经济核算步骤")
    if isinstance(snapshot, str):
        snapshot = json.loads(snapshot)

    group_amount, team_pool, proposer_amount = amounts_from_snapshot(snapshot)

    dist_row = await db.fetch_one(
        """
        INSERT INTO bonus_distributions
            (proposal_id, group_amount, team_pool, proposer_amount,
             team_breakdown, created_by)
        VALUES ($1,$2,$3,$4,$5::jsonb,$6) RETURNING id
        """,
        proposal_id, float(group_amount), float(team_pool), float(proposer_amount),
        json.dumps(body.team_breakdown), current_user["id"],
    )

    await db.execute(
        "UPDATE incentive_proposals SET status='approved', updated_at=now() WHERE id=$1",
        proposal_id,
    )

    await write_audit(
        db, user_id=current_user["id"], action="distribute_bonus",
        resource="proposal", resource_id=proposal_id,
        new_state={
            "distribution_id": str(dist_row["id"]),
            "group_amount": float(group_amount),
            "team_pool": float(team_pool),
            "proposer_amount": float(proposer_amount),
        },
        ip_address=request.client.host if request.client else None,
    )

    # 查询提案人姓名
    meta = await db.fetch_one(
        "SELECT u.display_name AS proposer_name FROM incentive_proposals ip "
        "JOIN users u ON ip.proposer_id = u.id WHERE ip.id=$1",
        proposal_id,
    )
    proposer_name = meta["proposer_name"] if meta else ""
    bonus_pool_val = float(group_amount) + float(team_pool) + float(proposer_amount)
    await notify_proposal_approved(proposal["title"], proposer_name, bonus_pool_val)

    return {
        "ok": True,
        "distribution_id": str(dist_row["id"]),
        "group_amount": float(group_amount),
        "team_pool": float(team_pool),
        "proposer_amount": float(proposer_amount),
    }


# ──────────────────────────── 兑现凭证 PDF ────────────────────

@router.get("/{proposal_id}/certificate")
async def download_certificate(
    proposal_id: str,
    db=Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """生成兑现凭证 PDF（仅 approved/paid 状态可下载）"""
    proposal = await _get_proposal_or_404(proposal_id, db)
    _require_status(proposal, "approved", "paid")

    distribution = await db.fetch_one(
        "SELECT * FROM bonus_distributions WHERE proposal_id=$1", proposal_id
    )
    if not distribution:
        raise HTTPException(400, "奖金分配记录不存在，无法生成凭证")

    approvals = await db.fetch_all(
        "SELECT role, signed_at, comment FROM proposal_approvals "
        "WHERE proposal_id=$1 AND signed_at IS NOT NULL ORDER BY signed_at",
        proposal_id,
    )

    # 查询提案人姓名和项目名称
    meta = await db.fetch_one(
        """
        SELECT u.display_name AS proposer_name, p.name AS project_name
        FROM incentive_proposals ip
        JOIN users u ON ip.proposer_id = u.id
        JOIN projects p ON ip.project_id = p.id
        WHERE ip.id = $1
        """,
        proposal_id,
    )

    pdf_bytes = generate_certificate(
        proposal=dict(proposal),
        distribution=dict(distribution),
        approvals=[dict(a) for a in approvals],
        proposer_name=meta["proposer_name"] if meta else "",
        project_name=meta["project_name"] if meta else "",
    )

    filename = f"cert_{proposal_id[:8]}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ──────────────────────────── 驳回 ────────────────────────────

@router.post("/{proposal_id}/reject")
async def reject_proposal(
    proposal_id: str,
    body: RejectBody,
    request: Request,
    db=Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    _require_role(
        current_user,
        "project_manager", "economist", "group_admin",
        "group_chief_engineer", "group_commercial_director",
    )
    proposal = await _get_proposal_or_404(proposal_id, db)
    _require_status(proposal, "draft", "calculating", "pending_sign", "public_notice")

    await db.execute(
        "UPDATE incentive_proposals SET status='rejected', updated_at=now() WHERE id=$1",
        proposal_id,
    )
    await db.execute(
        """
        INSERT INTO proposal_approvals (proposal_id, role, approver_id, signed_at, comment)
        VALUES ($1,'reject',$2,now(),$3)
        """,
        proposal_id, current_user["id"], body.reason,
    )
    await write_audit(
        db, user_id=current_user["id"], action="reject_proposal",
        resource="proposal", resource_id=proposal_id,
        new_state={"reason": body.reason},
        ip_address=request.client.host if request.client else None,
    )
    return {"ok": True, "new_status": "rejected"}
