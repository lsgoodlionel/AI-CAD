"""语义审校队列 + 审校动作 API（Phase C · 泳道 D · C-15）。

把 3D 重建结果作为待审成果接入三审文化：机器出初模 + 置信度，人审拓扑闭合
（墙首尾相接 / 梁柱支承 / 板梁托承 / 门窗归墙）、构件命名、规范符合性。低置信 +
规则-模型冲突项优先排队；人审动作回流为修正标签（写 ``model_review_actions`` 埋点，
喂 C-06 数据闭环）并全部写 ``audit_logs``。队列由 ``scene`` / 融合结果确定性生成
（融合契约见 ``core/model3d/fusion``），只纳入带识别信号（confidence/source/拓扑标志）的对象。

端点（统一信封 ``{success, data, error, meta}``）：
- GET  /projects/{project_id}/model/review-queue    生成审校队列（低置信/冲突优先）
- POST /projects/{project_id}/model/review-actions   提交审校动作 → 埋点 + 审计
"""
from __future__ import annotations

import json
import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from dependencies import get_current_user, get_db
from services.audit import write_audit

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/projects", tags=["model-review"])

# 审校对象类别 / 动作类别（对齐迁移 024_review_actions.sql 与 modelReview.ts 契约）
TARGET_KINDS: frozenset[str] = frozenset(
    {"topology", "naming", "compliance", "element", "symbol"}
)
ACTION_TYPES: frozenset[str] = frozenset(
    {"confirm", "reject", "reclass", "addbox", "edit"}
)

# 低置信阈值（对齐前端 confidenceColor 红区）：< 0.5 视为低置信，优先审
LOW_CONFIDENCE = 0.5
# 冲突项优先权重：确保「规则-模型冲突 / 拓扑未闭合」永远排在纯低置信之前
_CONFLICT_WEIGHT = 1000.0
_UNKNOWN_CONFIDENCE = 0.5  # 缺置信度视为中性不确定

_AUDIT_ACTION = "model_semantic_review"

# 9 类 taxonomy 分组 → 单构件类别（floors[].elements 的分组键为复数）
_GROUP_CATEGORY = {
    "columns": "column",
    "walls": "wall",
    "beams": "beam",
    "slabs": "slab",
    "pipes": "pipe",
    "equipment": "equipment",
}


# ── 工具 ────────────────────────────────────────────────────────

def _parse_scene(value: Any) -> dict | None:
    """JSONB scene 经驱动可能返回 str，安全解析为 dict。"""
    if value is None:
        return None
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return None
        return parsed if isinstance(parsed, dict) else None
    return value if isinstance(value, dict) else None


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _priority(conflict: bool, confidence: float | None) -> float:
    """优先分：冲突项加 1000 权重；越低置信越优先（1-conf 映射）。"""
    base = _CONFLICT_WEIGHT if conflict else 0.0
    conf = confidence if confidence is not None else _UNKNOWN_CONFIDENCE
    return round(base + 100.0 * (1.0 - conf), 4)


def _item(
    *, kind: str, target_id: str, title: str, detail: str,
    confidence: float | None = None, source: str | None = None,
    conflict: bool = False, category: str | None = None,
    suggested_category: str | None = None, discipline: str | None = None,
    mep_system: str | None = None, drawing_id: str | None = None,
) -> dict:
    return {
        "id": target_id, "target_kind": kind, "title": title, "detail": detail,
        "confidence": confidence, "source": source, "conflict": conflict,
        "category": category, "suggested_category": suggested_category,
        "discipline": discipline, "mep_system": mep_system, "drawing_id": drawing_id,
        "priority": _priority(conflict, confidence),
    }


# ── 队列生成：拓扑闭合 ──────────────────────────────────────────

def _topology_items(scene: dict) -> list[dict]:
    """拓扑待审：门窗归墙 / 梁柱支承 / 板梁托承 / 墙链闭合。orphan/未闭合 = 冲突。"""
    topo = scene.get("topology")
    if not isinstance(topo, dict):
        return []
    out: list[dict] = []

    def add(prefix: str, rel: dict, key: str, title: str, conflict: bool,
            detail: str, category: str, discipline: str | None) -> None:
        tid = str(rel.get(key) or rel.get("id") or "").strip()
        if not tid:
            return
        out.append(_item(
            kind="topology", target_id=f"{prefix}:{tid}", title=title.format(tid=tid),
            detail=detail, confidence=_as_float(rel.get("confidence")),
            source=rel.get("source") or "rule", conflict=conflict,
            category=category, discipline=discipline,
        ))

    for rel in topo.get("host_rels") or []:
        orphan = bool(rel.get("orphan"))
        add("host", rel, "opening_id", "洞口 {tid} 归属墙体", orphan,
            "门窗未找到归属墙体，拓扑未闭合" if orphan
            else f"门窗归属墙体 {rel.get('wall_id')}，待确认", "opening_host", "建筑")

    for rel in topo.get("beam_supports") or []:
        unsupported = not (rel.get("column_id") or rel.get("column_ids"))
        add("beam", rel, "beam_id", "梁 {tid} 柱支承", unsupported,
            "梁未找到支承柱，拓扑未闭合" if unsupported else "梁由柱支承，待确认",
            "beam_support", "结构")

    for rel in topo.get("slab_supports") or []:
        beams = rel.get("beam_ids") or []
        add("slab", rel, "slab_id", "板 {tid} 梁托承", len(beams) == 0,
            "板未找到托承梁，拓扑未闭合" if not beams
            else f"板由 {len(beams)} 根梁托承，待确认", "slab_support", "结构")

    # 通用关系（墙链闭合等）：closed is False → 未闭合冲突
    for rel in topo.get("relations") or []:
        conflict = rel.get("closed") is False
        add("rel", rel, "id", str(rel.get("title") or "拓扑关系 {tid}"), conflict,
            str(rel.get("detail") or ("拓扑未闭合" if conflict else "拓扑关系待确认")),
            rel.get("subtype"), rel.get("discipline"))
    return out


# ── 队列生成：显式候选（融合/规范写入的干净契约）────────────────

def _explicit_conflict(raw: dict) -> bool:
    if bool(raw.get("conflict")):
        return True
    # 融合裁决：规则/模型分歧 = 规则-模型冲突
    rule_c = raw.get("rule_category")
    model_c = raw.get("model_category")
    return (
        raw.get("source") == "fused"
        and rule_c is not None
        and model_c is not None
        and rule_c != model_c
    )


def _explicit_items(scene: dict) -> list[dict]:
    """``scene['review_candidates']``：融合/规范符合性层写入的显式待审项。"""
    out: list[dict] = []
    for idx, raw in enumerate(scene.get("review_candidates") or []):
        if not isinstance(raw, dict):
            continue
        kind = raw.get("target_kind") or "element"
        if kind not in TARGET_KINDS:
            continue
        tid = str(raw.get("id") or raw.get("target_id") or f"cand:{idx}")
        out.append(_item(
            kind=kind, target_id=tid,
            title=str(raw.get("title") or tid),
            detail=str(raw.get("detail") or ""),
            confidence=_as_float(raw.get("confidence")),
            source=raw.get("source"),
            conflict=_explicit_conflict(raw),
            category=raw.get("category"),
            suggested_category=raw.get("suggested_category") or raw.get("model_category"),
            discipline=raw.get("discipline"),
            mep_system=raw.get("mep_system"),
            drawing_id=str(raw["drawing_id"]) if raw.get("drawing_id") else None,
        ))
    return out


# ── 队列生成：从 floors 构件派生（命名/构件）────────────────────

def _element_items(scene: dict) -> list[dict]:
    """floors[].elements 中带识别信号的构件 → 命名/构件待审项。

    纯几何构件（无 confidence/source）不纳入——无审校信号，避免噪声。
    """
    out: list[dict] = []
    for floor in scene.get("floors") or []:
        if not isinstance(floor, dict):
            continue
        floor_key = str(floor.get("key") or floor.get("label") or "")
        elements = floor.get("elements")
        if not isinstance(elements, dict):
            continue
        for group, items in elements.items():
            category = _GROUP_CATEGORY.get(group, group)
            for pos, el in enumerate(items or []):
                if not isinstance(el, dict):
                    continue
                conf = _as_float(el.get("confidence"))
                src = el.get("source")
                if conf is None and src is None:
                    continue  # 几何-only 构件，跳过
                name = el.get("name") or el.get("label")
                kind = "element" if name else "naming"
                out.append(_item(
                    kind=kind, target_id=str(el.get("id") or f"{floor_key}:{group}:{pos}"),
                    title=str(name or f"{category} @ {floor_key}"),
                    detail="构件缺少命名，待人工命名" if kind == "naming"
                    else f"构件 {name} 识别置信 {conf}，待确认",
                    confidence=conf, source=str(src) if src else None,
                    conflict=bool(el.get("conflict")), category=category,
                    suggested_category=el.get("suggested_category"),
                    discipline=el.get("discipline"), mep_system=el.get("mep_system"),
                    drawing_id=str(el["drawing_id"]) if el.get("drawing_id") else None,
                ))
    return out


def build_review_queue(
    scene: dict | None,
    *,
    target_kind: str | None = None,
    discipline: str | None = None,
    only_conflicts: bool = False,
    limit: int | None = None,
) -> tuple[list[dict], dict]:
    """由 scene 生成语义审校队列（低置信 + 规则-模型冲突优先），返回 (items, summary)。"""
    if not isinstance(scene, dict):
        return [], {"total": 0, "conflict_count": 0, "low_confidence_count": 0, "by_kind": {}}

    raw_items = _topology_items(scene) + _explicit_items(scene) + _element_items(scene)

    # 去重（同 kind + id 保留首现）
    seen: set[tuple[str, str]] = set()
    items: list[dict] = []
    for it in raw_items:
        key = (it["target_kind"], it["id"])
        if key in seen:
            continue
        seen.add(key)
        items.append(it)

    if target_kind:
        items = [it for it in items if it["target_kind"] == target_kind]
    if discipline:
        items = [it for it in items if it.get("discipline") == discipline]
    if only_conflicts:
        items = [it for it in items if it["conflict"]]

    # 冲突优先（priority 已含冲突权重）→ 同层低置信优先 → id 稳定序
    items.sort(key=lambda it: (
        -it["priority"],
        it["confidence"] if it["confidence"] is not None else 1.0,
        it["id"],
    ))

    by_kind: dict[str, int] = {}
    for it in items:
        by_kind[it["target_kind"]] = by_kind.get(it["target_kind"], 0) + 1
    summary = {
        "total": len(items),
        "conflict_count": sum(1 for it in items if it["conflict"]),
        "low_confidence_count": sum(
            1 for it in items
            if it["confidence"] is not None and it["confidence"] < LOW_CONFIDENCE
        ),
        "by_kind": by_kind,
    }

    if limit is not None and limit >= 0:
        items = items[:limit]
    return items, summary


# ── 端点：审校队列 ──────────────────────────────────────────────

@router.get("/{project_id}/model/review-queue")
async def get_review_queue(
    project_id: str,
    db=Depends(get_db),
    current_user: dict = Depends(get_current_user),
    target_kind: str | None = Query(None),
    discipline: str | None = Query(None),
    only_conflicts: bool = Query(False),
    limit: int | None = Query(None, ge=0, le=1000),
):
    """语义审校队列：拓扑/命名/规范符合性待审项，低置信 + 规则-模型冲突优先。"""
    if target_kind is not None and target_kind not in TARGET_KINDS:
        raise HTTPException(400, "INVALID_TARGET_KIND")

    row = await db.fetch_one(
        "SELECT scene FROM project_models WHERE project_id=$1", project_id
    )
    if row is None:
        raise HTTPException(404, "MODEL_NOT_BUILT")

    scene = _parse_scene(dict(row).get("scene"))
    items, summary = build_review_queue(
        scene,
        target_kind=target_kind,
        discipline=discipline,
        only_conflicts=only_conflicts,
        limit=limit,
    )
    return {
        "success": True,
        "data": {"items": items, "summary": summary},
        "error": None,
        "meta": {"project_id": project_id, "returned": len(items)},
    }


# ── 端点：审校动作 ──────────────────────────────────────────────

class ReviewActionBody(BaseModel):
    target_kind: Literal["topology", "naming", "compliance", "element", "symbol"]
    action_type: Literal["confirm", "reject", "reclass", "addbox", "edit"]
    target_id: str | None = None
    drawing_id: str | None = None
    old_category: str | None = None
    new_category: str | None = None
    mep_system: str | None = None
    discipline: str | None = None
    source: str | None = None
    confidence: float | None = Field(None, ge=0.0, le=1.0)
    note: str | None = None


_INSERT_ACTION_SQL = """
INSERT INTO model_review_actions
    (project_id, drawing_id, target_kind, target_id, action_type,
     old_category, new_category, mep_system, discipline, source, confidence,
     reviewer_id, note)
VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
RETURNING id, created_at
"""


@router.post("/{project_id}/model/review-actions", status_code=201)
async def submit_review_action(
    project_id: str,
    body: ReviewActionBody,
    request: Request,
    db=Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """提交语义审校动作：写 ``model_review_actions`` 埋点 + ``audit_logs`` 审计。"""
    project = await db.fetch_one("SELECT id FROM projects WHERE id=$1", project_id)
    if project is None:
        raise HTTPException(404, "PROJECT_NOT_FOUND")

    # 改类必须给出新类别，否则埋点无意义（喂训练标签需明确改后类别）
    if body.action_type == "reclass" and not (body.new_category or "").strip():
        raise HTTPException(400, "REVIEW_NEW_CATEGORY_REQUIRED")

    reviewer_id = current_user["id"]
    inserted = await db.fetch_one(
        _INSERT_ACTION_SQL,
        project_id, body.drawing_id, body.target_kind, body.target_id,
        body.action_type, body.old_category, body.new_category, body.mep_system,
        body.discipline, body.source, body.confidence, reviewer_id, body.note,
    )
    action_id = str(dict(inserted)["id"]) if inserted is not None else None

    await write_audit(
        db, user_id=reviewer_id, action=_AUDIT_ACTION,
        resource="model_review_action", resource_id=action_id,
        new_state={
            "project_id": project_id, "target_kind": body.target_kind,
            "target_id": body.target_id, "action_type": body.action_type,
            "old_category": body.old_category, "new_category": body.new_category,
            "source": body.source, "confidence": body.confidence,
        },
        ip_address=request.client.host if request.client else None,
    )
    return {
        "success": True,
        "data": {"id": action_id, "target_kind": body.target_kind,
                 "action_type": body.action_type},
        "error": None,
        "meta": {"project_id": project_id, "audited": True},
    }
