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
        SELECT status, version, built_at, error, scene, progress, updated_at
        FROM project_models WHERE project_id=$1
        """,
        project_id,
    )
    if row is None:
        raise HTTPException(404, "MODEL_NOT_BUILT")
    record = dict(row)
    scene = _parse_jsonb(record["scene"], None)
    progress = _parse_jsonb(record.get("progress"), None)
    # 僵尸构建检测:任务已死(worker 重启/OOM)但状态仍 building,前端会永远转圈。
    # 实测:进度停在 2308/2309 达 25 分钟而 worker 活跃任务为空。
    from services.build_health import build_health
    health = build_health(record["status"], record.get("updated_at"), progress)
    return {
        "build_health": health,
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


@router.get("/{project_id}/model/components")
async def get_model_components(
    project_id: str,
    db=Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """H4:装配 ComponentInstance 汇总(有信息的模型)——总数 + 类型分布 +
    Z/轴网覆盖 + 审核态(auto/待人审 conflict/已确认)。取最新模型版本。"""
    row = await db.fetch_one(
        "SELECT version FROM project_models WHERE project_id=$1", project_id
    )
    if row is None:
        raise HTTPException(status_code=404, detail="MODEL_NOT_FOUND")
    version = row["version"]
    from services.component_repository import fetch_instances_summary
    summary = await fetch_instances_summary(db, project_id, version)
    return {"project_id": project_id, "model_version": version, **summary}


class ComponentReviewBody(BaseModel):
    action: str                       # confirm | reject | reclass
    new_type: str | None = None       # reclass 时的新类型
    note: str | None = None


@router.get("/{project_id}/model/components/review-queue")
async def get_component_review_queue(
    project_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    db=Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """H4+:低置信(conflict)构件人审队列——带来源图纸/识别途径,按置信升序。"""
    row = await db.fetch_one(
        "SELECT version FROM project_models WHERE project_id=$1", project_id)
    if row is None:
        raise HTTPException(status_code=404, detail="MODEL_NOT_FOUND")
    from services.component_repository import fetch_review_queue
    queue = await fetch_review_queue(db, project_id, row["version"], limit)
    return {"project_id": project_id, "model_version": row["version"], "queue": queue}


@router.get("/{project_id}/model/components/metrics")
async def get_component_metrics(
    project_id: str,
    db=Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """H7 验收指标:竖向真实率/轴网定位率(位置代理)/审核收敛/人审工作量。

    位置误差需人审金标签作真值,当前不可测,用轴网定位率代理(如实标注)。
    数量准确率见 `POST /model/components/reconcile`(需设计 BOM)。
    """
    row = await db.fetch_one(
        "SELECT version FROM project_models WHERE project_id=$1", project_id)
    if row is None:
        raise HTTPException(status_code=404, detail="MODEL_NOT_FOUND")
    from services.component_repository import (
        fetch_instances_summary, fetch_review_action_counts)
    from services.component_metrics import compute_metrics
    summary = await fetch_instances_summary(db, project_id, row["version"])
    actions = await fetch_review_action_counts(db, project_id)
    metrics = compute_metrics(summary, actions)
    return {"project_id": project_id, "model_version": row["version"], **metrics}


class ComponentReconcileBody(BaseModel):
    bom: dict[str, int]                # 设计构件表数量 {type: count}


@router.post("/{project_id}/model/components/reconcile")
async def reconcile_components(
    project_id: str,
    body: ComponentReconcileBody,
    db=Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """H5 职责A:装配实体数量 vs 设计构件表 BOM,报每型 缺/多(数量对齐,驱动补漏/查重)。"""
    row = await db.fetch_one(
        "SELECT version FROM project_models WHERE project_id=$1", project_id)
    if row is None:
        raise HTTPException(status_code=404, detail="MODEL_NOT_FOUND")
    from services.component_repository import fetch_instances_summary
    from services.component_bom import reconcile_from_counts
    summary = await fetch_instances_summary(db, project_id, row["version"])
    report = reconcile_from_counts(summary.get("by_type") or {}, body.bom)
    return {"project_id": project_id, "model_version": row["version"], "report": report}


@router.get("/{project_id}/model/components/overlay")
async def get_components_overlay(
    project_id: str,
    drawing_id: str = Query(..., description="图纸 id"),
    db=Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """H4+ 回投:某图纸构件的归一化页面标记,供前端叠加到图纸预览让人在图上核对。"""
    row = await db.fetch_one(
        "SELECT version FROM project_models WHERE project_id=$1", project_id)
    if row is None:
        raise HTTPException(status_code=404, detail="MODEL_NOT_FOUND")
    from services.component_repository import fetch_overlay
    return await fetch_overlay(db, project_id, row["version"], drawing_id)


@router.get("/{project_id}/model/components/by-source")
async def get_components_by_source(
    project_id: str,
    drawing_id: str = Query(..., description="来源图纸 id"),
    comp_type: str = Query(..., description="构件类型 column/wall/pipe…"),
    db=Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """H6:3D 点击单构件 → 该图该类构件在装配层的证据(数量 + 审核态 + 竖向覆盖)。"""
    row = await db.fetch_one(
        "SELECT version FROM project_models WHERE project_id=$1", project_id)
    if row is None:
        raise HTTPException(status_code=404, detail="MODEL_NOT_FOUND")
    from services.component_repository import fetch_instances_by_source
    data = await fetch_instances_by_source(
        db, project_id, row["version"], drawing_id, comp_type)
    return {"project_id": project_id, "model_version": row["version"], **data}


_COMPONENT_REVIEW_ACTIONS = {"confirm", "reject", "reclass"}
_INSERT_COMPONENT_ACTION_SQL = """
INSERT INTO model_review_actions
    (project_id, drawing_id, target_kind, target_id, action_type,
     new_category, reviewer_id, note)
VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
"""


@router.post("/{project_id}/model/components/{instance_id}/llm-review")
async def llm_review_component(
    project_id: str,
    instance_id: str,
    db=Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """H5:大模型复核单个低置信构件,返回 确认/否定/改类 建议(不自动应用,供人审参考)。

    LLM 不可用/失败时优雅降级(available=False),端点仍 200。
    """
    from services.component_repository import fetch_instance_for_review
    inst = await fetch_instance_for_review(db, project_id, instance_id)
    if inst is None:
        raise HTTPException(status_code=404, detail="COMPONENT_NOT_FOUND")
    router_llm = None
    try:
        from redis.asyncio import Redis
        from core.llm.router import ModelRouter
        from core.config import settings as _settings
        router_llm = ModelRouter(
            db=db, redis=Redis.from_url(_settings.redis_url, decode_responses=True))
    except Exception:  # noqa: BLE001 — 无 Redis/依赖 → 降级
        router_llm = None
    from services.component_llm_review import review_component
    recommendation = await review_component(inst, router_llm)
    return {"instance_id": instance_id, "component": inst, "recommendation": recommendation}


@router.post("/{project_id}/model/components/{instance_id}/review", status_code=201)
async def submit_component_review(
    project_id: str,
    instance_id: str,
    body: ComponentReviewBody,
    request: Request,
    db=Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """H4+:构件人审 confirm/reject/reclass → 翻转 review_state + 埋点 + 审计(收敛飞轮)。"""
    if body.action not in _COMPONENT_REVIEW_ACTIONS:
        raise HTTPException(status_code=400, detail="INVALID_REVIEW_ACTION")
    if body.action == "reclass" and not (body.new_type or "").strip():
        raise HTTPException(status_code=400, detail="REVIEW_NEW_TYPE_REQUIRED")
    reviewer_id = current_user["id"]
    from services.component_repository import apply_component_review
    updated = await apply_component_review(
        db, project_id, instance_id, body.action, reviewer_id, body.new_type)
    if updated is None:
        raise HTTPException(status_code=404, detail="COMPONENT_NOT_FOUND")
    updated["id"] = str(updated["id"])
    await db.execute(
        _INSERT_COMPONENT_ACTION_SQL,
        project_id, None, "element", instance_id, body.action,
        body.new_type, str(reviewer_id), body.note,
    )
    await write_audit(
        db, user_id=reviewer_id, action="model.component.review",
        resource="component_instance", resource_id=instance_id,
        new_state={"action": body.action, "new_type": body.new_type,
                   "review_state": updated["review_state"]},
        ip_address=request.client.host if request.client else None,
    )
    return {"success": True, "data": updated, "error": None}


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
    # 方向1:平面图标注恢复的**标高候选**(须人审,不自动采用)。
    # 实测大歌剧院仅 24 张剖面(north 单体 0 张)→ 层高全为默认套;而 989 张平面图带
    # 28265 条标高标注,经区分度加权投票 + 单调性约束可恢复 9/12 层候选。
    # 诚实边界:IDF 会误压制"恰好正确的普遍值"(实测 F1 恢复 0.92m 而应为 ±0.000),
    # 故仅作建议值展示,由人确认后写入人工录入通道。
    try:
        suggestions = await _plan_elevation_suggestions(db, project_id, scene)
        for item in items:
            sug = suggestions.get(str(item.get("story_key")))
            if sug:
                item["suggested_elevation_m"] = sug["elevation_m"]
                item["suggestion_support"] = sug["support"]
                item["suggestion_confidence"] = sug.get("confidence")
                item["suggestion_source"] = "plan_annotation"
    except Exception:  # noqa: BLE001 — 建议值失败不影响主功能
        pass
    # 层高↔标高计算关系(工程约束):标高差分即层高,不自洽处即数据错误所在。
    # 领域依据:标高多在剖面/立面表格,层高在平面以标高符号标注,二者须互洽。
    meta: dict[str, Any] = {"count": len(items)}
    try:
        from services.story_elevation_calculus import (
            cross_validate, heights_from_elevations, unreasonable_heights)
        levels = [
            {"story_key": i.get("story_key"), "order": i.get("story_order") or 0,
             "elevation_m": (i.get("manual_elevation_m")
                             if i.get("manual_elevation_m") is not None
                             else i.get("auto_elevation_m"))}
            for i in items
        ]
        derived = {d["story_key"]: d for d in heights_from_elevations(levels)}
        for item in items:
            d = derived.get(item.get("story_key"))
            if d:
                item["derived_height_m"] = d["height_m"]         # 由上下层标高差算出
                item["height_reasonable"] = d["reasonable"]      # 2.5~9m 合理区间
        given = [
            {"story_key": i.get("story_key"),
             "height_m": (i.get("manual_height_m") if i.get("manual_height_m") is not None
                          else i.get("auto_height_m"))}
            for i in items
        ]
        meta["height_consistency"] = cross_validate(levels, given)
        meta["unreasonable_heights"] = unreasonable_heights(levels)
    except Exception:  # noqa: BLE001 — 校验失败不影响主功能
        pass
    return {"data": items, "meta": meta}


async def _plan_elevation_suggestions(db, project_id: str, scene: dict | None) -> dict:
    """从平面图标注恢复各层标高候选(带质量分)。失败/无数据 → 空 dict。"""
    if not scene:
        return {}
    from services.plan_elevation_recovery import grade_candidates, recover_plan_elevations

    rows = await db.fetch_all(
        """SELECT drawing_id, value_json FROM drawing_extracted_info
           WHERE project_id = :p AND category = 'elevation' AND is_active""",
        {"p": project_id})
    by_drawing: dict[str, list[float]] = {}
    for r in rows:
        value = _parse_jsonb(r["value_json"], None) or {}
        elevation = value.get("elevation_m")
        if elevation is not None:
            by_drawing.setdefault(str(r["drawing_id"]), []).append(float(elevation))
    per_floor: dict[str, dict] = {}
    baseline: dict[str, float] = {}
    orders: dict[str, int] = {}
    for floor in scene.get("floors") or []:
        key = str(floor.get("key") or "")
        if not key or key == "UNZONED":
            continue
        drawings = {
            str(d.get("drawing_id")): by_drawing[str(d.get("drawing_id"))]
            for d in (floor.get("drawings") or [])
            if str(d.get("drawing_id")) in by_drawing
        }
        if not drawings:
            continue
        orders[key] = int(floor.get("order") or 0)
        per_floor[key] = {"order": orders[key], "drawings": drawings}
        if floor.get("elevation_m") is not None:
            baseline[key] = float(floor["elevation_m"])
    if not per_floor:
        return {}
    plan = grade_candidates(recover_plan_elevations(per_floor), baseline, orders)
    # **剖面/立面优先**:标高本就标注在剖面/立面(常为表格),且其竖向按比例绘制
    # → 标高与图上 y 严格线性,可自校验(实测 19/31 张 R²≥0.98,最佳 R²=1.0)。
    # 质量高于平面图投票,故覆盖同层的平面建议。
    section = await _section_elevation_suggestions(db, project_id, scene, baseline, orders)
    plan.update(section)
    return plan


async def _section_elevation_suggestions(
    db, project_id: str, scene: dict, baseline: dict, orders: dict,
) -> dict:
    """从剖面/立面图恢复标高(线性自校验 + 主楼面序列 + 楼层匹配)。"""
    from services.drawing_view_classifier import classify_view_type
    from services.section_elevation_fit import (
        fit_elevation_axis, main_story_elevations, match_to_floors)

    drawings = [dict(r) for r in await db.fetch_all(
        "SELECT id, drawing_no, title, file_key, discipline FROM drawings "
        "WHERE project_id = :p", {"p": project_id})]
    section_ids = [str(d["id"]) for d in drawings
                   if classify_view_type(d).view_type in ("section", "elevation")]
    if not section_ids:
        return {}
    rows = await db.fetch_all(
        """SELECT drawing_id, value_json, location_json FROM drawing_extracted_info
           WHERE project_id = :p AND category = 'elevation' AND is_active
             AND drawing_id::text = ANY(:ids)""",
        {"p": project_id, "ids": section_ids})
    by_drawing: dict[str, list] = {}
    for r in rows:
        value = _parse_jsonb(r["value_json"], None) or {}
        loc = _parse_jsonb(r["location_json"], None) or {}
        y = loc.get("y")
        if y is None:
            bbox = loc.get("bbox")
            if isinstance(bbox, (list, tuple)) and len(bbox) >= 4:
                y = (bbox[1] + bbox[3]) / 2
        elevation = value.get("elevation_m")
        if elevation is not None and y is not None:
            by_drawing.setdefault(str(r["drawing_id"]), []).append(
                (float(y), float(elevation)))
    floors = [{"story_key": k, "order": orders.get(k, 0), "elevation_m": v}
              for k, v in baseline.items()]
    out: dict[str, dict] = {}
    for points in by_drawing.values():
        fit = fit_elevation_axis(points)
        if not fit["ok"]:
            continue          # 线性不成立 → 该图标高不可信,不采用
        sequence = main_story_elevations([e for _, e in points])
        for key, hit in match_to_floors(sequence, floors).items():
            prev = out.get(key)
            # 同层多图命中时取拟合优度更高者
            if prev is None or fit["r_squared"] > prev.get("r_squared", 0):
                out[key] = {
                    "elevation_m": hit["elevation_m"],
                    "support": fit["inliers"],
                    "confidence": round(min(fit["r_squared"], 1.0), 3),
                    "deviation_m": hit["delta_m"],
                    "story_height_ok": None,
                    "needs_review": True,
                    "z_source": "section_fit",
                    "r_squared": fit["r_squared"],
                }
    return out


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
