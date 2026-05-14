"""项目管理 API：项目档案、成员、工作分区。"""
from datetime import date
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from dependencies import get_current_user, get_db, require_admin

router = APIRouter(prefix="/projects", tags=["projects"])


PROJECT_ROLES = {
    "project_manager",
    "project_chief_engineer",
    "commercial_manager",
    "economist",
    "designer",
    "site_engineer",
    "labor_crew",
    "viewer",
}


class ProjectCreate(BaseModel):
    org_id: UUID
    name: str = Field(min_length=2, max_length=200)
    code: str | None = None
    project_type: str | None = None
    annual_output: float | None = None
    status: str = "active"
    description: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    manager_id: UUID | None = None
    chief_engineer_id: UUID | None = None
    commercial_manager_id: UUID | None = None


class ProjectUpdate(BaseModel):
    org_id: UUID | None = None
    name: str | None = Field(default=None, min_length=2, max_length=200)
    code: str | None = None
    project_type: str | None = None
    annual_output: float | None = None
    status: str | None = None
    description: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    manager_id: UUID | None = None
    chief_engineer_id: UUID | None = None
    commercial_manager_id: UUID | None = None


class ProjectMemberCreate(BaseModel):
    user_id: UUID
    project_role: str
    is_primary: bool = False
    joined_at: date | None = None


class ProjectMemberUpdate(BaseModel):
    project_role: str | None = None
    is_primary: bool | None = None
    joined_at: date | None = None
    left_at: date | None = None


class WorkZoneCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    zone_code: str | None = None


class WorkZoneUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    zone_code: str | None = None


def _is_admin(user: dict) -> bool:
    return user.get("role") == "group_admin"


async def _ensure_project_visible(db, project_id: str, user: dict) -> None:
    if _is_admin(user):
        return
    exists = await db.fetch_val(
        """
        SELECT 1
        FROM project_members
        WHERE project_id=$1 AND user_id=$2 AND left_at IS NULL
        LIMIT 1
        """,
        project_id,
        user["id"],
    )
    if not exists:
        raise HTTPException(403, "无权访问该项目")


async def _ensure_project_manager(db, project_id: str, user: dict) -> None:
    if _is_admin(user):
        return
    exists = await db.fetch_val(
        """
        SELECT 1
        FROM project_members
        WHERE project_id=$1 AND user_id=$2 AND project_role IN ('project_manager','project_chief_engineer')
          AND left_at IS NULL
        LIMIT 1
        """,
        project_id,
        user["id"],
    )
    if not exists:
        raise HTTPException(403, "需要项目管理权限")


@router.get("")
async def list_projects(
    keyword: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db=Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    conds, args = [], []
    idx = 1
    if not _is_admin(current_user):
        conds.append(
            f"""EXISTS (
                SELECT 1 FROM project_members pm
                WHERE pm.project_id=p.id AND pm.user_id=${idx} AND pm.left_at IS NULL
            )"""
        )
        args.append(current_user["id"])
        idx += 1
    if keyword:
        conds.append(f"(p.name ILIKE ${idx} OR p.code ILIKE ${idx})")
        args.append(f"%{keyword}%")
        idx += 1
    if status:
        conds.append(f"p.status=${idx}")
        args.append(status)
        idx += 1

    where = f"WHERE {' AND '.join(conds)}" if conds else ""
    rows = await db.fetch_all(
        f"""
        SELECT p.id, p.name, p.code, p.project_type, p.annual_output, p.status,
               p.description, p.start_date, p.end_date, p.created_at, p.updated_at,
               o.name AS org_name,
               mgr.display_name AS manager_name,
               COUNT(DISTINCT pm.id)::int AS member_count,
               COUNT(DISTINCT d.id)::int AS drawing_count
        FROM projects p
        JOIN organizations o ON o.id=p.org_id
        LEFT JOIN users mgr ON mgr.id=p.manager_id
        LEFT JOIN project_members pm ON pm.project_id=p.id AND pm.left_at IS NULL
        LEFT JOIN drawings d ON d.project_id=p.id
        {where}
        GROUP BY p.id, o.name, mgr.display_name
        ORDER BY p.updated_at DESC NULLS LAST, p.created_at DESC
        LIMIT ${idx} OFFSET ${idx + 1}
        """,
        *args,
        limit,
        offset,
    )
    total = await db.fetch_val(f"SELECT COUNT(*) FROM projects p {where}", *args)
    return {"items": [dict(r) for r in rows], "total": total}


@router.post("", status_code=201)
async def create_project(
    body: ProjectCreate,
    db=Depends(get_db),
    current_user: dict = Depends(require_admin),
):
    row = await db.fetch_one(
        """
        INSERT INTO projects
            (org_id, name, code, project_type, annual_output, status, created_by,
             description, start_date, end_date, manager_id, chief_engineer_id, commercial_manager_id)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
        RETURNING id
        """,
        body.org_id,
        body.name,
        body.code,
        body.project_type,
        body.annual_output,
        body.status,
        current_user["id"],
        body.description,
        body.start_date,
        body.end_date,
        body.manager_id,
        body.chief_engineer_id,
        body.commercial_manager_id,
    )
    return {"id": str(row["id"])}


@router.get("/{project_id}")
async def get_project(
    project_id: str,
    db=Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    await _ensure_project_visible(db, project_id, current_user)
    row = await db.fetch_one(
        """
        SELECT p.*, o.name AS org_name,
               mgr.display_name AS manager_name,
               chief.display_name AS chief_engineer_name,
               commercial.display_name AS commercial_manager_name
        FROM projects p
        JOIN organizations o ON o.id=p.org_id
        LEFT JOIN users mgr ON mgr.id=p.manager_id
        LEFT JOIN users chief ON chief.id=p.chief_engineer_id
        LEFT JOIN users commercial ON commercial.id=p.commercial_manager_id
        WHERE p.id=$1
        """,
        project_id,
    )
    if not row:
        raise HTTPException(404, "项目不存在")
    return dict(row)


@router.patch("/{project_id}")
async def update_project(
    project_id: str,
    body: ProjectUpdate,
    db=Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    await _ensure_project_manager(db, project_id, current_user)
    fields = body.model_dump(exclude_none=True)
    if not fields:
        raise HTTPException(400, "无更新字段")
    sets = ", ".join(f"{key}=${idx + 2}" for idx, key in enumerate(fields))
    await db.execute(
        f"UPDATE projects SET {sets}, updated_at=now() WHERE id=$1",
        project_id,
        *fields.values(),
    )
    return {"ok": True}


@router.delete("/{project_id}")
async def archive_project(
    project_id: str,
    db=Depends(get_db),
    _: dict = Depends(require_admin),
):
    await db.execute(
        "UPDATE projects SET status='completed', updated_at=now() WHERE id=$1",
        project_id,
    )
    return {"ok": True}


@router.get("/{project_id}/members")
async def list_project_members(
    project_id: str,
    db=Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    await _ensure_project_visible(db, project_id, current_user)
    rows = await db.fetch_all(
        """
        SELECT pm.id, pm.project_id, pm.user_id, pm.project_role, pm.is_primary,
               pm.joined_at, pm.left_at, u.username, u.display_name, u.role,
               u.email, u.phone, u.is_active
        FROM project_members pm
        JOIN users u ON u.id=pm.user_id
        WHERE pm.project_id=$1
        ORDER BY pm.left_at NULLS FIRST, pm.is_primary DESC, u.display_name
        """,
        project_id,
    )
    return {"items": [dict(r) for r in rows]}


@router.post("/{project_id}/members", status_code=201)
async def add_project_member(
    project_id: str,
    body: ProjectMemberCreate,
    db=Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    await _ensure_project_manager(db, project_id, current_user)
    if body.project_role not in PROJECT_ROLES:
        raise HTTPException(400, "无效项目角色")
    row = await db.fetch_one(
        """
        INSERT INTO project_members (project_id, user_id, project_role, is_primary, joined_at)
        VALUES ($1,$2,$3,$4,COALESCE($5, CURRENT_DATE))
        RETURNING id
        """,
        project_id,
        body.user_id,
        body.project_role,
        body.is_primary,
        body.joined_at,
    )
    return {"id": str(row["id"])}


@router.patch("/{project_id}/members/{member_id}")
async def update_project_member(
    project_id: str,
    member_id: str,
    body: ProjectMemberUpdate,
    db=Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    await _ensure_project_manager(db, project_id, current_user)
    fields = body.model_dump(exclude_none=True)
    if not fields:
        raise HTTPException(400, "无更新字段")
    if fields.get("project_role") and fields["project_role"] not in PROJECT_ROLES:
        raise HTTPException(400, "无效项目角色")
    sets = ", ".join(f"{key}=${idx + 3}" for idx, key in enumerate(fields))
    await db.execute(
        f"UPDATE project_members SET {sets} WHERE id=$1 AND project_id=$2",
        member_id,
        project_id,
        *fields.values(),
    )
    return {"ok": True}


@router.delete("/{project_id}/members/{member_id}")
async def remove_project_member(
    project_id: str,
    member_id: str,
    db=Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    await _ensure_project_manager(db, project_id, current_user)
    await db.execute(
        "UPDATE project_members SET left_at=CURRENT_DATE WHERE id=$1 AND project_id=$2",
        member_id,
        project_id,
    )
    return {"ok": True}


@router.get("/{project_id}/work-zones")
async def list_work_zones(
    project_id: str,
    db=Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    await _ensure_project_visible(db, project_id, current_user)
    rows = await db.fetch_all(
        "SELECT id, project_id, name, zone_code, created_at FROM work_zones WHERE project_id=$1 ORDER BY zone_code, name",
        project_id,
    )
    return {"items": [dict(r) for r in rows]}


@router.post("/{project_id}/work-zones", status_code=201)
async def create_work_zone(
    project_id: str,
    body: WorkZoneCreate,
    db=Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    await _ensure_project_manager(db, project_id, current_user)
    row = await db.fetch_one(
        "INSERT INTO work_zones (project_id, name, zone_code) VALUES ($1,$2,$3) RETURNING id",
        project_id,
        body.name,
        body.zone_code,
    )
    return {"id": str(row["id"])}


@router.patch("/{project_id}/work-zones/{zone_id}")
async def update_work_zone(
    project_id: str,
    zone_id: str,
    body: WorkZoneUpdate,
    db=Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    await _ensure_project_manager(db, project_id, current_user)
    fields = body.model_dump(exclude_none=True)
    if not fields:
        raise HTTPException(400, "无更新字段")
    sets = ", ".join(f"{key}=${idx + 3}" for idx, key in enumerate(fields))
    await db.execute(
        f"UPDATE work_zones SET {sets} WHERE id=$1 AND project_id=$2",
        zone_id,
        project_id,
        *fields.values(),
    )
    return {"ok": True}
