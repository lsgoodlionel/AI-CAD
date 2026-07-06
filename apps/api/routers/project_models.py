"""工程 3D 模型基座 API（Phase 6 模块 D）

- POST /projects/{project_id}/model/rebuild   UPSERT 置 building + 触发 Celery 构建 + 审计
- GET  /projects/{project_id}/model           模型状态与 scene（无记录 → 404 MODEL_NOT_BUILT）
- GET  /projects/{project_id}/model/asset-url 贴图/glb 签名 URL（key 前缀防越权）

蓝图：docs/MODEL_BASE_BLUEPRINT.md 第 6 节。
"""
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from core.storage import presigned_get_url
from dependencies import get_db, get_current_user
from services.audit import write_audit
from tasks.model_build import build_project_model

router = APIRouter(prefix="/projects", tags=["project-models"])

ASSET_URL_EXPIRES_SECONDS = 300


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


# ── 重建模型 ──────────────────────────────────────────────────

@router.post("/{project_id}/model/rebuild")
async def rebuild_project_model(
    request: Request,
    project_id: str,
    db=Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    project = await db.fetch_one("SELECT id FROM projects WHERE id=$1", project_id)
    if project is None:
        raise HTTPException(404, "PROJECT_NOT_FOUND")

    row = await db.fetch_one(
        """
        INSERT INTO project_models (project_id, status)
        VALUES ($1, 'building')
        ON CONFLICT (project_id)
        DO UPDATE SET status='building', error=NULL, updated_at=now()
        RETURNING version
        """,
        project_id,
    )
    version = row["version"] if row is not None else 0

    await write_audit(
        db,
        user_id=current_user["id"],
        action="rebuild_project_model",
        resource="project_model",
        resource_id=project_id,
        new_state={"status": "building", "version": version},
        ip_address=request.client.host if request.client else None,
    )
    build_project_model.delay(project_id)

    return {"project_id": project_id, "status": "building", "version": version}


# ── 模型详情 ──────────────────────────────────────────────────

@router.get("/{project_id}/model")
async def get_project_model(
    project_id: str,
    db=Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    row = await db.fetch_one(
        """
        SELECT status, version, built_at, error, scene
        FROM project_models WHERE project_id=$1
        """,
        project_id,
    )
    if row is None:
        raise HTTPException(404, "MODEL_NOT_BUILT")
    return {
        "status": row["status"],
        "version": row["version"],
        "built_at": row["built_at"],
        "error": row["error"],
        "scene": _parse_jsonb(row["scene"], None),
    }


# ── 资产签名 URL ─────────────────────────────────────────────

@router.get("/{project_id}/model/asset-url")
async def get_model_asset_url(
    project_id: str,
    key: str = Query(..., description="MinIO 对象 key"),
    current_user: dict = Depends(get_current_user),
):
    allowed_prefix = f"projects/{project_id}/model_assets/"
    if not key.startswith(allowed_prefix):
        raise HTTPException(403, "ASSET_FORBIDDEN")
    return {"url": presigned_get_url(key, expires_seconds=ASSET_URL_EXPIRES_SECONDS)}
