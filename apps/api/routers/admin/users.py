"""人员与组织管理 API。"""
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from core.auth import hash_password
from dependencies import get_db, require_admin

router = APIRouter(prefix="/admin", tags=["admin-users"])

ROLES = {
    "group_admin",
    "group_chief_engineer",
    "group_deepening_director",
    "group_commercial_director",
    "project_manager",
    "project_chief_engineer",
    "economist",
    "designer",
    "site_engineer",
    "labor_crew",
}

ORG_TYPES = {"group", "company", "branch", "project_dept"}


class UserCreate(BaseModel):
    org_id: UUID | None = None
    username: str = Field(min_length=2, max_length=100)
    email: str | None = None
    password: str = Field(min_length=6, max_length=100)
    display_name: str = Field(min_length=1, max_length=100)
    role: str = "designer"
    phone: str | None = None
    position: str | None = None
    employee_no: str | None = None


class UserUpdate(BaseModel):
    org_id: UUID | None = None
    email: str | None = None
    display_name: str | None = Field(default=None, min_length=1, max_length=100)
    role: str | None = None
    phone: str | None = None
    position: str | None = None
    employee_no: str | None = None
    is_active: bool | None = None


class PasswordReset(BaseModel):
    password: str = Field(min_length=6, max_length=100)


class OrganizationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    code: str | None = None
    parent_id: UUID | None = None
    org_type: str = "company"


class OrganizationUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    code: str | None = None
    parent_id: UUID | None = None
    org_type: str | None = None


@router.get("/users")
async def list_users(
    keyword: Optional[str] = None,
    role: Optional[str] = None,
    org_id: UUID | None = None,
    is_active: bool | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db=Depends(get_db),
    _=Depends(require_admin),
):
    conds, args = [], []
    idx = 1
    if keyword:
        conds.append(f"(u.username ILIKE ${idx} OR u.display_name ILIKE ${idx} OR u.email ILIKE ${idx})")
        args.append(f"%{keyword}%")
        idx += 1
    if role:
        conds.append(f"u.role=${idx}")
        args.append(role)
        idx += 1
    if org_id:
        conds.append(f"u.org_id=${idx}")
        args.append(org_id)
        idx += 1
    if is_active is not None:
        conds.append(f"u.is_active=${idx}")
        args.append(is_active)
        idx += 1

    where = f"WHERE {' AND '.join(conds)}" if conds else ""
    rows = await db.fetch_all(
        f"""
        SELECT u.id, u.org_id, u.username, u.email, u.display_name, u.role,
               u.phone, u.position, u.employee_no, u.is_active, u.last_login_at,
               u.created_at, u.updated_at, o.name AS org_name,
               COUNT(DISTINCT pm.project_id)::int AS project_count
        FROM users u
        LEFT JOIN organizations o ON o.id=u.org_id
        LEFT JOIN project_members pm ON pm.user_id=u.id AND pm.left_at IS NULL
        {where}
        GROUP BY u.id, o.name
        ORDER BY u.is_active DESC, u.created_at DESC
        LIMIT ${idx} OFFSET ${idx + 1}
        """,
        *args,
        limit,
        offset,
    )
    total = await db.fetch_val(f"SELECT COUNT(*) FROM users u {where}", *args)
    return {"items": [dict(r) for r in rows], "total": total}


@router.post("/users", status_code=201)
async def create_user(body: UserCreate, db=Depends(get_db), _=Depends(require_admin)):
    if body.role not in ROLES:
        raise HTTPException(400, "无效角色")
    row = await db.fetch_one(
        """
        INSERT INTO users
            (org_id, username, email, hashed_password, display_name, role,
             phone, position, employee_no, password_changed_at)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,now())
        RETURNING id
        """,
        body.org_id,
        body.username,
        body.email,
        hash_password(body.password),
        body.display_name,
        body.role,
        body.phone,
        body.position,
        body.employee_no,
    )
    return {"id": str(row["id"])}


@router.get("/users/{user_id}")
async def get_user(user_id: str, db=Depends(get_db), _=Depends(require_admin)):
    row = await db.fetch_one(
        """
        SELECT u.id, u.org_id, u.username, u.email, u.display_name, u.role,
               u.phone, u.position, u.employee_no, u.is_active, u.last_login_at,
               u.created_at, u.updated_at, o.name AS org_name
        FROM users u
        LEFT JOIN organizations o ON o.id=u.org_id
        WHERE u.id=$1
        """,
        user_id,
    )
    if not row:
        raise HTTPException(404, "用户不存在")
    projects = await db.fetch_all(
        """
        SELECT p.id, p.name, p.code, pm.project_role, pm.is_primary
        FROM project_members pm
        JOIN projects p ON p.id=pm.project_id
        WHERE pm.user_id=$1 AND pm.left_at IS NULL
        ORDER BY p.name
        """,
        user_id,
    )
    result = dict(row)
    result["projects"] = [dict(p) for p in projects]
    return result


@router.patch("/users/{user_id}")
async def update_user(user_id: str, body: UserUpdate, db=Depends(get_db), _=Depends(require_admin)):
    fields = body.model_dump(exclude_none=True)
    if not fields:
        raise HTTPException(400, "无更新字段")
    if fields.get("role") and fields["role"] not in ROLES:
        raise HTTPException(400, "无效角色")
    sets = ", ".join(f"{key}=${idx + 2}" for idx, key in enumerate(fields))
    await db.execute(
        f"UPDATE users SET {sets}, updated_at=now() WHERE id=$1",
        user_id,
        *fields.values(),
    )
    return {"ok": True}


@router.post("/users/{user_id}/reset-password")
async def reset_password(
    user_id: str,
    body: PasswordReset,
    db=Depends(get_db),
    _=Depends(require_admin),
):
    await db.execute(
        "UPDATE users SET hashed_password=$2, password_changed_at=now(), updated_at=now() WHERE id=$1",
        user_id,
        hash_password(body.password),
    )
    return {"ok": True}


@router.post("/users/{user_id}/enable")
async def enable_user(user_id: str, db=Depends(get_db), _=Depends(require_admin)):
    await db.execute("UPDATE users SET is_active=true, updated_at=now() WHERE id=$1", user_id)
    return {"ok": True}


@router.post("/users/{user_id}/disable")
async def disable_user(user_id: str, db=Depends(get_db), _=Depends(require_admin)):
    await db.execute("UPDATE users SET is_active=false, updated_at=now() WHERE id=$1", user_id)
    return {"ok": True}


@router.get("/organizations")
async def list_organizations(db=Depends(get_db), _=Depends(require_admin)):
    rows = await db.fetch_all(
        """
        SELECT o.id, o.name, o.code, o.parent_id, o.org_type, o.created_at,
               parent.name AS parent_name,
               COUNT(DISTINCT u.id)::int AS user_count,
               COUNT(DISTINCT p.id)::int AS project_count
        FROM organizations o
        LEFT JOIN organizations parent ON parent.id=o.parent_id
        LEFT JOIN users u ON u.org_id=o.id
        LEFT JOIN projects p ON p.org_id=o.id
        GROUP BY o.id, parent.name
        ORDER BY o.org_type, o.name
        """
    )
    return {"items": [dict(r) for r in rows]}


@router.post("/organizations", status_code=201)
async def create_organization(
    body: OrganizationCreate,
    db=Depends(get_db),
    _=Depends(require_admin),
):
    if body.org_type not in ORG_TYPES:
        raise HTTPException(400, "无效组织类型")
    row = await db.fetch_one(
        "INSERT INTO organizations (name, code, parent_id, org_type) VALUES ($1,$2,$3,$4) RETURNING id",
        body.name,
        body.code,
        body.parent_id,
        body.org_type,
    )
    return {"id": str(row["id"])}


@router.patch("/organizations/{org_id}")
async def update_organization(
    org_id: str,
    body: OrganizationUpdate,
    db=Depends(get_db),
    _=Depends(require_admin),
):
    fields = body.model_dump(exclude_none=True)
    if not fields:
        raise HTTPException(400, "无更新字段")
    if fields.get("org_type") and fields["org_type"] not in ORG_TYPES:
        raise HTTPException(400, "无效组织类型")
    sets = ", ".join(f"{key}=${idx + 2}" for idx, key in enumerate(fields))
    await db.execute(
        f"UPDATE organizations SET {sets} WHERE id=$1",
        org_id,
        *fields.values(),
    )
    return {"ok": True}
