"""符号 spotting 推理微服务 API（Phase C · C-12）。

对外提供「图纸 → 符号候选 + 置信度」稳定契约，供 C-13 融合与 C-15/C-16 审校消费。
推理经 ``SpottingService`` → SpottingBackend（CADTransformer 优先，离线 mock 兜底），
并纳入 ModelRouter 引擎治理（引擎 ``symbol_spotting``，见迁移 023）。

端点：
- POST /projects/{project_id}/drawings/{drawing_id}/spot   对单张图纸做符号 spotting
- GET  /projects/{project_id}/drawings/{drawing_id}/spot/backends  观测后端选路与可用性

统一信封：``{ success, data, error, meta }``。
"""
from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Depends, HTTPException

from core.model3d.spotting.service import ENGINE_NAME, SpottingService
from core.storage import get_file_bytes
from dependencies import get_current_user, get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/projects", tags=["symbol-spotting"])

_DRAWING_SQL = "SELECT id, file_key FROM drawings WHERE id=$1 AND project_id=$2"


def get_spotting_service(db=Depends(get_db)) -> SpottingService:
    """可注入的服务工厂（测试可 override 覆盖）。"""
    return SpottingService(db=db)


def _file_ext(file_key: str) -> str:
    """从 MinIO key 推断扩展名（无则空串，交由预处理优雅降级）。"""
    _, ext = os.path.splitext(file_key or "")
    return ext.lower().lstrip(".")


async def _load_drawing(db, project_id: str, drawing_id: str) -> dict:
    row = await db.fetch_one(_DRAWING_SQL, drawing_id, project_id)
    if row is None:
        raise HTTPException(404, "DRAWING_NOT_FOUND")
    return dict(row)


@router.post("/{project_id}/drawings/{drawing_id}/spot")
async def spot_drawing(
    project_id: str,
    drawing_id: str,
    db=Depends(get_db),
    service: SpottingService = Depends(get_spotting_service),
    current_user: dict = Depends(get_current_user),
):
    """对单张图纸做符号 spotting，返回符号候选（统一信封）。"""
    drawing = await _load_drawing(db, project_id, drawing_id)
    file_key = drawing.get("file_key")
    if not file_key:
        raise HTTPException(409, "DRAWING_FILE_MISSING")

    try:
        data = get_file_bytes(file_key)
    except Exception as exc:  # noqa: BLE001 — 存储读取失败明确报错
        logger.error("[spotting] 图纸下载失败 key=%s: %s", file_key, exc)
        raise HTTPException(502, "DRAWING_DOWNLOAD_FAILED") from exc

    result = service.spot_drawing(
        data, _file_ext(file_key), project_id=project_id, drawing_id=drawing_id
    )
    return {
        "success": True,
        "data": result.to_dict(),
        "error": None,
        "meta": {
            "engine_name": ENGINE_NAME,
            "backend": result.backend,
            "candidate_count": len(result.candidates),
            "warnings": list(result.warnings),
        },
    }


@router.get("/{project_id}/drawings/{drawing_id}/spot/backends")
async def spot_backends(
    project_id: str,
    drawing_id: str,
    db=Depends(get_db),
    service: SpottingService = Depends(get_spotting_service),
    current_user: dict = Depends(get_current_user),
):
    """观测 spotting 后端选路与可用性（ops / 配置漂移排查）。"""
    await _load_drawing(db, project_id, drawing_id)
    backends = service.list_backends()
    active = next((b["name"] for b in backends if b["available"]), None)
    return {
        "success": True,
        "data": {"backends": backends, "active": active},
        "error": None,
        "meta": {"engine_name": ENGINE_NAME},
    }
