"""工程 3D 模型基座 API（Phase 6 模块 D）

- POST /projects/{project_id}/model/rebuild   UPSERT 置 building + 触发 Celery 构建 + 审计
- GET  /projects/{project_id}/model           模型状态与 scene（无记录 → 404 MODEL_NOT_BUILT）
- GET  /projects/{project_id}/model/asset-url 贴图/glb 签名 URL（key 前缀防越权）

蓝图：docs/MODEL_BASE_BLUEPRINT.md 第 6 节。
"""
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from core.storage import presigned_get_url
from dependencies import get_db, get_current_user
from services.audit import write_audit
from services import (
    model_annotations,
    model_qto_summary,
    model_semantics,
    model_story,
    model_story_manual,
)
from services.model_qto import compute_rebar_quantities
from services.model_semantics import SemanticHierarchyError, SemanticVersionConflict
from tasks.model_build import build_project_model

router = APIRouter(prefix="/projects", tags=["project-models"])

ASSET_URL_EXPIRES_SECONDS = 300

_ANNOTATION_DRAWINGS_SQL = """
SELECT id, drawing_no, title, discipline, status, current_stage, file_key
FROM drawings
WHERE project_id=$1
ORDER BY drawing_no, created_at
"""


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


def _model_quality_from_scene(scene: dict | None) -> dict:
    if not isinstance(scene, dict):
        return {}
    quality = scene.get("quality")
    return quality if isinstance(quality, dict) else {}


def _model_annotation_queue_from_scene(scene: dict | None) -> list:
    if not isinstance(scene, dict):
        return []
    queue = scene.get("annotation_queue")
    if isinstance(queue, list):
        return queue
    quality = _model_quality_from_scene(scene)
    queue = quality.get("unclassified_drawings")
    return queue if isinstance(queue, list) else []


def _model_building_units_from_scene(scene: dict | None) -> dict:
    if not isinstance(scene, dict):
        return {"detected": [], "manual": []}
    units = scene.get("building_units")
    if isinstance(units, dict):
        return {
            "detected": units.get("detected") if isinstance(units.get("detected"), list) else [],
            "manual": units.get("manual") if isinstance(units.get("manual"), list) else [],
        }
    quality = _model_quality_from_scene(scene)
    detected = quality.get("building_units")
    return {"detected": detected if isinstance(detected, list) else [], "manual": []}


async def _build_annotation_context(db, project_id: str) -> dict:
    drawings = [
        dict(row) for row in await db.fetch_all(_ANNOTATION_DRAWINGS_SQL, project_id)
    ]
    annotations = await model_annotations.load_annotation_overrides(db, project_id)
    normalization = model_story.normalize_story_table(drawings, annotations)
    quality = {
        "building_units": normalization.building_units,
        "unclassified_drawings": normalization.unclassified_drawings,
        "unassigned_story_count": len(normalization.unclassified_drawings),
        "pending_manual_count": len(normalization.unclassified_drawings),
        "story_conflict_count": sum(
            1 for issue in normalization.issues
            if issue.issue_type == "story_spacing_too_small"
        ),
        "issues": [issue.__dict__ for issue in normalization.issues],
    }
    return {
        "items": normalization.unclassified_drawings,
        "annotation_queue": normalization.unclassified_drawings,
        "building_units": {
            "detected": normalization.building_units,
            "manual": [
                unit for unit in normalization.building_units
                if unit.get("source") == "manual"
            ],
        },
        "quality": quality,
    }


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
        SELECT status, version, built_at, error, scene, progress
        FROM project_models WHERE project_id=$1
        """,
        project_id,
    )
    if row is None:
        raise HTTPException(404, "MODEL_NOT_BUILT")
    record = dict(row)
    scene = _parse_jsonb(record["scene"], None)
    return {
        "status": record["status"],
        "version": record["version"],
        "built_at": record["built_at"],
        "error": record["error"],
        "scene": scene,
        "quality": _model_quality_from_scene(scene),
        "annotation_queue": _model_annotation_queue_from_scene(scene),
        "building_units": _model_building_units_from_scene(scene),
        # 构建实时进度（migration 014；building 状态时前端展示）
        "progress": _parse_jsonb(record.get("progress"), None),
    }


@router.get("/{project_id}/model/quantities")
async def get_model_quantities(
    project_id: str,
    db=Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """QTO 工程量汇总（B-19）：混凝土/模板/钢筋，分楼层/分单体下钻，统一信封。"""
    row = await db.fetch_one(
        "SELECT scene FROM project_models WHERE project_id=$1", project_id
    )
    if row is None:
        raise HTTPException(404, "MODEL_NOT_BUILT")
    scene = _parse_jsonb(dict(row)["scene"], None)
    data = (
        model_qto_summary.build_scene_quantities(scene)
        if scene
        else {"project": model_qto_summary.summarize([]), "by_floor": [], "by_building": []}
    )
    return {"success": True, "data": data, "error": None, "meta": {"scope": "scene"}}


class QtoToProposalBody(BaseModel):
    rebar_inputs: list[dict] = []
    rebar_params: dict | None = None
    extra_saving_yuan: float = 0.0     # 混凝土/模板量差价值（调用方另算，可选叠加）
    title: str | None = None


def _qto_proposal_description(qto: dict, rebar: dict, raw_saving: float) -> str:
    concrete = qto["project"]["concrete"]
    summary = rebar.get("summary") or {}
    return (
        f"由 QTO 算量自动生成创效草稿。混凝土净体积 {concrete['net_m3']} m³"
        f"（毛 {concrete['gross_m3']} m³）；钢筋优化节约 {summary.get('saving_kg', 0)} kg / "
        f"{summary.get('saving_yuan', 0)} 元。预估净节约（待经济师测算复核）约 {raw_saving} 元。"
    )


@router.post("/{project_id}/model/quantities/to-proposal", status_code=201)
async def qto_to_proposal(
    project_id: str,
    body: QtoToProposalBody,
    request: Request,
    db=Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """QTO 差值 → 创效提案草稿（B-20）。仅造 draft，下游 calculate/签字硬约束不被绕过。"""
    row = await db.fetch_one(
        "SELECT scene FROM project_models WHERE project_id=$1", project_id
    )
    if row is None:
        raise HTTPException(404, "MODEL_NOT_BUILT")
    scene = _parse_jsonb(dict(row)["scene"], None)
    if not scene:
        raise HTTPException(409, "MODEL_SCENE_EMPTY")

    rebar = compute_rebar_quantities(body.rebar_inputs, body.rebar_params)
    rebar_saving = (
        0.0 if rebar["rebar_missing"]
        else float((rebar.get("summary") or {}).get("saving_yuan") or 0.0)
    )
    raw_saving = round(rebar_saving + max(body.extra_saving_yuan, 0.0), 2)
    if raw_saving <= 0:
        raise HTTPException(400, "NO_POSITIVE_SAVING")

    qto = model_qto_summary.build_scene_quantities(
        scene, rebar_inputs=body.rebar_inputs, rebar_params=body.rebar_params
    )
    inserted = await db.fetch_one(
        """
        INSERT INTO incentive_proposals
            (project_id, drawing_id, proposer_id, proposal_type,
             title, description, raw_saving_est)
        VALUES ($1,$2,$3,$4,$5,$6,$7) RETURNING id
        """,
        project_id, None, current_user["id"], "B",
        body.title or "QTO算量创效草稿", _qto_proposal_description(qto, rebar, raw_saving),
        raw_saving,
    )
    proposal_id = str(inserted["id"])
    await write_audit(
        db, user_id=current_user["id"], action="qto_to_proposal",
        resource="proposal", resource_id=proposal_id,
        new_state={"raw_saving_est": raw_saving, "project_id": project_id},
        ip_address=request.client.host if request.client else None,
    )
    return {"proposal_id": proposal_id, "status": "draft", "raw_saving_est": raw_saving}


@router.get("/{project_id}/model/annotation-queue")
async def get_model_annotation_queue(
    project_id: str,
    db=Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    project = await db.fetch_one("SELECT id FROM projects WHERE id=$1", project_id)
    if project is None:
        raise HTTPException(404, "PROJECT_NOT_FOUND")
    return await _build_annotation_context(db, project_id)


@router.post("/{project_id}/model/annotations")
async def save_model_annotation(
    project_id: str,
    body: dict[str, Any],
    db=Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    project = await db.fetch_one("SELECT id FROM projects WHERE id=$1", project_id)
    if project is None:
        raise HTTPException(404, "PROJECT_NOT_FOUND")

    drawing_id = str(body.get("drawing_id") or "").strip()
    if not drawing_id:
        raise HTTPException(400, "DRAWING_ID_REQUIRED")
    drawing = await db.fetch_one(
        "SELECT id FROM drawings WHERE id=$1 AND project_id=$2",
        drawing_id,
        project_id,
    )
    if drawing is None:
        raise HTTPException(404, "DRAWING_NOT_FOUND")

    try:
        annotation = await model_annotations.save_drawing_annotation(
            db,
            project_id=project_id,
            drawing_id=drawing_id,
            payload=body,
            annotated_by=str(current_user["id"]),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    return {"annotation": annotation}


# ── 语义图谱与人工操作 ─────────────────────────────────────────

@router.get("/{project_id}/model/semantics")
async def get_model_semantics(
    project_id: str,
    db=Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    drawings = [
        dict(row) for row in await db.fetch_all(_ANNOTATION_DRAWINGS_SQL, project_id)
    ]
    graph = await model_semantics.build_semantic_graph(db, project_id, drawings)
    return graph.as_dict()


@router.post("/{project_id}/model/semantic-operations")
async def apply_model_semantic_operation(
    project_id: str,
    body: dict[str, Any],
    db=Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    expected_version = body.get("expected_version")
    try:
        return await model_semantics.apply_semantic_operation(
            db,
            project_id=project_id,
            actor_id=str(current_user["id"]),
            operation=body,
            expected_version=int(expected_version) if expected_version is not None else None,
        )
    except SemanticVersionConflict as exc:
        raise HTTPException(
            409,
            {"code": "SEMANTIC_VERSION_CONFLICT", "latest": exc.latest},
        ) from exc
    except SemanticHierarchyError as exc:
        raise HTTPException(
            422,
            {"code": "INVALID_SEMANTIC_HIERARCHY", "message": str(exc)},
        ) from exc


@router.get("/{project_id}/model/rebuild-impact")
async def get_model_rebuild_impact(
    project_id: str,
    node_id: str | None = Query(None),
    drawing_id: str | None = Query(None),
    target_node_id: str | None = Query(None),
    operation_type: str | None = Query(None),
    expected_version: int | None = Query(None),
    current_user: dict = Depends(get_current_user),
):
    scope = [item for item in (node_id, target_node_id, drawing_id) if item]
    return {
        "project_id": project_id,
        "rebuild_required": True,
        "affected_nodes": [node_id] if node_id else [],
        "affected_drawings": [drawing_id] if drawing_id else [],
        "affected_stories": [],
        "affected_assets": [],
        "affected_scope": scope,
        "summary": "语义操作将触发相关模型分支重建" if operation_type else "模型重建影响预估",
        "rebuild_scope": "branch" if target_node_id else "node",
        "expected_version": expected_version,
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


# ── 楼层标高人工录入/校正（Task 3：自动识别打底 → 人工校正）──────────────

def _auto_story_rows_from_scene(scene: dict | None) -> list[dict]:
    """从 scene.floors 提取每层「自动识别」参考:标高 + 由标高差推层高,按单体分组。"""
    if not isinstance(scene, dict):
        return []
    floors = scene.get("floors") or []
    # 按单体分组(floor.building_key，缺省 main)
    by_scope: dict[str, list[dict]] = {}
    for floor in floors:
        if not isinstance(floor, dict):
            continue
        scope = str(floor.get("building_key") or "main")
        by_scope.setdefault(scope, []).append(floor)
    rows: list[dict] = []
    for scope, items in by_scope.items():
        ordered = sorted(items, key=lambda f: int(f.get("order") or 0))
        for i, floor in enumerate(ordered):
            elev = floor.get("elevation_m")
            nxt = ordered[i + 1].get("elevation_m") if i + 1 < len(ordered) else None
            auto_height = (
                round(float(nxt) - float(elev), 3)
                if elev is not None and nxt is not None
                else None
            )
            rows.append({
                "scope_key": scope,
                "story_key": str(floor.get("key") or ""),
                "story_label": str(floor.get("label") or floor.get("key") or ""),
                "story_order": int(floor.get("order") or 0),
                "auto_elevation_m": (round(float(elev), 3) if elev is not None else None),
                "auto_height_m": auto_height,
            })
    return rows


@router.get("/{project_id}/model/story-heights")
async def get_model_story_heights(
    project_id: str,
    db=Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """楼层标高:自动识别参考值 + 人工录入值(供人工校正界面)。"""
    row = await db.fetch_one(
        "SELECT scene FROM project_models WHERE project_id=$1", project_id
    )
    scene = _parse_jsonb(row["scene"], None) if row else None
    auto_rows = _auto_story_rows_from_scene(scene)
    manual_rows = await model_story_manual.fetch_manual_rows(db, project_id)
    manual_by_key = {
        (str(m["scope_key"]), str(m["story_key"])): m for m in manual_rows
    }
    items = []
    for auto in auto_rows:
        manual = manual_by_key.get((auto["scope_key"], auto["story_key"]))
        items.append({
            **auto,
            "manual_height_m": (float(manual["height_m"]) if manual else None),
            "manual_elevation_m": (
                float(manual["elevation_bottom_m"])
                if manual and manual.get("elevation_bottom_m") is not None
                else None
            ),
            "note": (manual.get("note") if manual else None),
        })
    return {"data": items, "meta": {"count": len(items)}}


class _StoryHeightItem(BaseModel):
    scope_key: str = "main"
    story_key: str
    story_order: int = 0
    height_m: float | None = None       # <=0 或 None 视为清除(恢复自动)
    elevation_bottom_m: float | None = None
    note: str | None = None


class _StoryHeightsBody(BaseModel):
    items: list[_StoryHeightItem]


@router.post("/{project_id}/model/story-heights")
async def save_model_story_heights(
    project_id: str,
    body: _StoryHeightsBody,
    request: Request,
    db=Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """保存人工录入/校正的层高(UPSERT;下次重建生效为最高优先级 override)。"""
    saved = await model_story_manual.save_manual_heights(
        db,
        project_id,
        [item.model_dump() for item in body.items],
        updated_by=str(current_user.get("id") or ""),
    )
    await write_audit(
        db, user_id=current_user["id"], action="model.story_heights.save",
        resource="project_model", resource_id=project_id,
        new_state={"saved": saved},
        ip_address=request.client.host if request.client else None,
    )
    return {"data": {"saved": saved}, "meta": {"note": "重建模型后生效"}}
