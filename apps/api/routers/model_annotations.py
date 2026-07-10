"""符号级标注人审 API（Phase C · C-16 · 泳道 D 前端审校工作台）。

在二维图纸上叠加符号候选框 + 置信度，人可确认/否定/改类/补框（生产审校 +
C-06 金标签生产）。落库迁移 024 两张表：``model_symbol_annotations``（符号框标注）
与 ``model_review_actions``（人审埋点，供 C-17 度量）。统一信封；导出仅 confirmed
金标签喂 C-09 训练。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from dependencies import get_current_user, get_db

router = APIRouter(prefix="/projects", tags=["symbol-annotations"])

# 9 类 taxonomy（与 core/model3d/layer_conventions._KIND_ORDER 对齐；C-04/C-12 契约）
TAXONOMY: tuple[str, ...] = (
    "column", "beam", "slab", "wall", "door", "window", "pipe", "equipment", "axis",
)
_CATEGORY_ID: dict[str, int] = {name: idx for idx, name in enumerate(TAXONOMY, start=1)}

_VALID_ACTIONS = {"confirm", "reject", "reclass", "addbox", "edit"}
# 动作 → 落库状态（addbox/edit/confirm 均视为人已定案的金标签）
_STATUS_BY_ACTION = {
    "confirm": "confirmed",
    "reject": "rejected",
    "reclass": "reclassed",
    "addbox": "confirmed",
    "edit": "confirmed",
}

_DRAWING_SQL = "SELECT id FROM drawings WHERE id=$1 AND project_id=$2"

# 符号标注全列（SELECT/RETURNING 共用，避免漂移）
_COLS = (
    "id, project_id, drawing_id, category, mep_system, bbox, confidence, "
    "source, status, primitive_ids, reviewer_id, evidence, created_at, updated_at"
)

_LIST_SQL = (
    f"SELECT {_COLS} FROM model_symbol_annotations "
    "WHERE project_id=$1 AND drawing_id=$2 ORDER BY confidence ASC NULLS LAST, id ASC"
)

_GET_ONE_SQL = (
    f"SELECT {_COLS} FROM model_symbol_annotations "
    "WHERE id=$1 AND project_id=$2 AND drawing_id=$3"
)

_INSERT_SQL = (
    "INSERT INTO model_symbol_annotations "
    "(project_id, drawing_id, category, mep_system, bbox, confidence, "
    "source, status, primitive_ids, reviewer_id, evidence) "
    "VALUES ($1, $2, $3, $4, CAST($5 AS jsonb), $6, $7, $8, "
    "CAST($9 AS jsonb), $10, CAST($11 AS jsonb)) "
    f"RETURNING {_COLS}"
)

_UPDATE_SQL = (
    "UPDATE model_symbol_annotations SET category=$3, mep_system=$4, "
    "bbox=CAST($5 AS jsonb), status=$6, reviewer_id=$7, "
    "evidence=COALESCE(CAST($8 AS jsonb), evidence), updated_at=now() "
    f"WHERE id=$1 AND project_id=$2 RETURNING {_COLS}"
)

# 埋点表 append-only（不可变审计），target_kind 固定 'symbol'
_ACTION_SQL = """
INSERT INTO model_review_actions
    (project_id, drawing_id, target_kind, target_id, action_type,
     old_category, new_category, mep_system, discipline, source, confidence,
     reviewer_id, note)
VALUES ($1, $2, 'symbol', $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
"""

_EXPORT_SQL = """
SELECT id, drawing_id, category, mep_system, bbox, confidence, status, primitive_ids
FROM model_symbol_annotations
WHERE project_id=$1 AND status='confirmed'
ORDER BY drawing_id, id
"""


# ── 序列化辅助（GET 端点与 CLI 导出共用，DRY）─────────────────────────────

def _parse_json(value: Any, default: Any) -> Any:
    """JSONB 字段经驱动可能回传 str，安全解析。"""
    if value is None:
        return default
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return default
    return value


def _json_or_none(value: Any) -> str | None:
    """把可选 JSON 值序列化为 jsonb 参数（None 透传，供 CAST 落库）。"""
    return None if value is None else json.dumps(value)


def _valid_bbox(value: Any) -> bool:
    """校验 [x_min, y_min, x_max, y_max] 数值四元组。"""
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return False
    return all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value)


def _to_xywh(bbox: Any) -> list[float]:
    """[x_min,y_min,x_max,y_max] → COCO [x, y, w, h]；非法则返回全 0。"""
    if not _valid_bbox(bbox):
        return [0.0, 0.0, 0.0, 0.0]
    x_min, y_min, x_max, y_max = (float(v) for v in bbox)
    return [
        round(x_min, 4),
        round(y_min, 4),
        round(max(x_max - x_min, 0.0), 4),
        round(max(y_max - y_min, 0.0), 4),
    ]


def _row_to_annotation(row: Any) -> dict[str, Any]:
    """DB 行 → 前端契约（camelCase，对齐 SymbolAnnotation）。"""
    record = dict(row)
    return {
        "id": record.get("id"),
        "projectId": str(record["project_id"]) if record.get("project_id") is not None else None,
        "drawingId": str(record["drawing_id"]) if record.get("drawing_id") is not None else None,
        "category": record.get("category"),
        "mepSystem": record.get("mep_system"),
        "bbox": _parse_json(record.get("bbox"), None),
        "confidence": record.get("confidence"),
        "source": record.get("source"),
        "status": record.get("status"),
        "primitiveIds": _parse_json(record.get("primitive_ids"), None),
        "reviewerId": record.get("reviewer_id"),
        "evidence": _parse_json(record.get("evidence"), None),
        "createdAt": record.get("created_at"),
        "updatedAt": record.get("updated_at"),
    }


def serialize_coco(
    rows: list[Any], *, project_id: str, exported_at: str | None = None
) -> dict[str, Any]:
    """把符号标注行序列化为 COCO-like 训练格式（GET 导出端点 + CLI 共用）。

    仅期望传入 confirmed 金标签行。bbox 由 [x_min,y_min,x_max,y_max] 转 COCO
    [x,y,w,h]；类别映射到 9 类 taxonomy 的 category_id（未知类记 0）。
    """
    stamp = exported_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    images: list[dict[str, Any]] = []
    image_id_by_drawing: dict[str, int] = {}
    annotations: list[dict[str, Any]] = []

    for raw in rows:
        record = dict(raw)
        drawing_id = str(record.get("drawing_id"))
        image_id = image_id_by_drawing.get(drawing_id)
        if image_id is None:
            image_id = len(images) + 1
            image_id_by_drawing[drawing_id] = image_id
            images.append(
                {"id": image_id, "drawing_id": drawing_id, "file_name": f"{drawing_id}.png"}
            )
        bbox = _to_xywh(_parse_json(record.get("bbox"), None))
        category = str(record.get("category") or "")
        annotations.append(
            {
                "id": int(record["id"]) if record.get("id") is not None else len(annotations) + 1,
                "image_id": image_id,
                "category_id": _CATEGORY_ID.get(category, 0),
                "category_name": category,
                "bbox": bbox,
                "area": round(bbox[2] * bbox[3], 4),
                "iscrowd": 0,
                "mep_system": record.get("mep_system"),
                "confidence": record.get("confidence"),
                "status": record.get("status"),
                "primitive_ids": _parse_json(record.get("primitive_ids"), None),
            }
        )

    return {
        "info": {
            "project_id": project_id,
            "source": "C-16 gold labels",
            "description": "符号级金标签导出（仅 confirmed），喂 C-09 训练",
            "exported_at": stamp,
        },
        "categories": [{"id": _CATEGORY_ID[name], "name": name} for name in TAXONOMY],
        "images": images,
        "annotations": annotations,
    }


async def _require_drawing(db, project_id: str, drawing_id: str) -> None:
    row = await db.fetch_one(_DRAWING_SQL, drawing_id, project_id)
    if row is None:
        raise HTTPException(404, "DRAWING_NOT_FOUND")


# ── 列出符号标注（低置信优先）────────────────────────────────────────────

@router.get("/{project_id}/drawings/{drawing_id}/symbol-annotations")
async def list_symbol_annotations(
    project_id: str,
    drawing_id: str,
    db=Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """列出某图纸符号标注（模型候选 + 人审状态），按置信度升序（低置信先审）。"""
    await _require_drawing(db, project_id, drawing_id)
    rows = await db.fetch_all(_LIST_SQL, project_id, drawing_id)
    data = [_row_to_annotation(row) for row in rows]
    pending = sum(1 for item in data if item["status"] == "pending")
    return {
        "success": True,
        "data": data,
        "error": None,
        "meta": {
            "count": len(data),
            "pending_count": pending,
            "taxonomy": list(TAXONOMY),
        },
    }


# ── 保存/确认/否定/改类/补框（同时写标注 + 埋点两表）──────────────────────

def _pick(body: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if body.get(key) is not None:
            return body.get(key)
    return None


@router.post("/{project_id}/drawings/{drawing_id}/symbol-annotations")
async def save_symbol_annotation(
    project_id: str,
    drawing_id: str,
    body: dict[str, Any],
    db=Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """保存符号标注并写人审埋点。

    - ``addbox``（无 id）：新增人工框，source=human，status=confirmed（金标签）。
    - ``confirm/reject/reclass/edit``（带 id）：更新既有候选状态/类别/框。
    两类均向 ``model_review_actions`` 追加一条不可变埋点，供 C-17 度量。
    """
    await _require_drawing(db, project_id, drawing_id)

    action_type = str(_pick(body, "actionType", "action_type") or "").strip()
    if action_type not in _VALID_ACTIONS:
        raise HTTPException(400, "INVALID_ACTION_TYPE")

    ctx = _ActionContext(
        project_id=project_id,
        drawing_id=drawing_id,
        action_type=action_type,
        status=_STATUS_BY_ACTION[action_type],
        reviewer_id=str(current_user["id"]),
        body=body,
    )
    if body.get("id") is None:
        saved, old_category, new_category, confidence = await _create_human_box(db, ctx)
    else:
        saved, old_category, new_category, confidence = await _update_candidate(db, ctx)

    # 人审埋点（append-only，供 C-17 返工收敛度量）
    await db.execute(
        _ACTION_SQL,
        project_id,
        drawing_id,
        str(saved["id"]),
        action_type,
        old_category,
        new_category,
        _pick(body, "mepSystem", "mep_system"),
        body.get("discipline"),
        saved.get("source"),
        confidence,
        ctx.reviewer_id,
        body.get("note"),
    )
    return {
        "success": True,
        "data": saved,
        "error": None,
        "meta": {"action": action_type, "status": ctx.status},
    }


@dataclass
class _ActionContext:
    """一次符号人审动作的解析上下文（从请求体派生）。"""

    project_id: str
    drawing_id: str
    action_type: str
    status: str
    reviewer_id: str
    body: dict[str, Any]

    @property
    def category(self) -> str | None:
        value = self.body.get("category")
        return str(value).strip() or None if value else None

    @property
    def mep_system(self) -> Any:
        return _pick(self.body, "mepSystem", "mep_system")


async def _create_human_box(db, ctx: _ActionContext) -> tuple[dict[str, Any], None, str, Any]:
    """补框：新增人工框（source=human），返回 (标注, 旧类别None, 新类别, 置信度)。"""
    if ctx.action_type != "addbox":
        raise HTTPException(400, "ANNOTATION_ID_REQUIRED")
    if not ctx.category:
        raise HTTPException(400, "CATEGORY_REQUIRED")
    bbox = ctx.body.get("bbox")
    if not _valid_bbox(bbox):
        raise HTTPException(400, "BBOX_REQUIRED")

    confidence = ctx.body.get("confidence")
    row = await db.fetch_one(
        _INSERT_SQL,
        ctx.project_id,
        ctx.drawing_id,
        ctx.category,
        ctx.mep_system,
        json.dumps(list(bbox)),
        confidence,
        "human",
        ctx.status,
        _json_or_none(_pick(ctx.body, "primitiveIds", "primitive_ids")),
        ctx.reviewer_id,
        _json_or_none(ctx.body.get("evidence")),
    )
    return _row_to_annotation(row), None, ctx.category, confidence


async def _update_candidate(db, ctx: _ActionContext) -> tuple[dict[str, Any], str | None, str | None, Any]:
    """对既有候选执行 confirm/reject/reclass/edit，并派生埋点用旧/新类别。"""
    annotation_id = ctx.body.get("id")
    existing_row = await db.fetch_one(_GET_ONE_SQL, annotation_id, ctx.project_id, ctx.drawing_id)
    if existing_row is None:
        raise HTTPException(404, "ANNOTATION_NOT_FOUND")
    existing = dict(existing_row)

    bbox = ctx.body.get("bbox")
    old_category = existing.get("category")
    final_category = ctx.category or old_category
    final_bbox = bbox if _valid_bbox(bbox) else _parse_json(existing.get("bbox"), None)
    final_mep = ctx.mep_system if ctx.mep_system is not None else existing.get("mep_system")
    # 审前候选置信度（埋点用），除非调用方显式覆盖
    override = ctx.body.get("confidence")
    audited_confidence = existing.get("confidence") if override is None else override

    row = await db.fetch_one(
        _UPDATE_SQL,
        annotation_id,
        ctx.project_id,
        final_category,
        final_mep,
        json.dumps(final_bbox),
        ctx.status,
        ctx.reviewer_id,
        _json_or_none(ctx.body.get("evidence")),
    )
    saved = _row_to_annotation(row) if row is not None else {**existing, "id": annotation_id}
    # reject 不产生新类别；其余动作记录最终类别
    new_category = None if ctx.action_type == "reject" else final_category
    return saved, old_category, new_category, audited_confidence


# ── 导出金标签（喂 C-09 训练）────────────────────────────────────────────

@router.get("/{project_id}/symbol-annotations/export")
async def export_symbol_annotations(
    project_id: str,
    format: str = Query("coco", description="导出格式，目前支持 coco"),
    db=Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """导出该项目 confirmed 金标签为 COCO-like 训练格式（喂 C-09）。"""
    if format != "coco":
        raise HTTPException(400, "UNSUPPORTED_FORMAT")
    rows = await db.fetch_all(_EXPORT_SQL, project_id)
    dataset = serialize_coco([dict(row) for row in rows], project_id=project_id)
    return {
        "success": True,
        "data": dataset,
        "error": None,
        "meta": {
            "format": "coco",
            "image_count": len(dataset["images"]),
            "annotation_count": len(dataset["annotations"]),
        },
    }
