"""Pipeline 事件消费处理器（D-08）。

Celery 任务（tasks/pipeline.py）拿到一条 ``pipeline_events`` 记录后调用
``dispatch()`` 按事件类型路由到这里的处理器。每个处理器：
  1. 检查项目级/全局开关（缺省开，见 core/pipeline/config.py）
  2. 用**已有模块的公开函数**计算影响面/工程量（不改动它们的实现）
  3. 超阈值时 upsert 一条 pipeline_suggestions「建议待办」

硬约束（不可绕过）：本文件绝不触发三审签字、模型重建、创效提案创建等
有副作用的硬动作——一律只生成建议，由人工在 routers/pipeline.py 采纳后
自行调用既有的 POST /model/rebuild、POST /model/quantities/to-proposal 等
端点完成实际动作。
"""
from __future__ import annotations

import json
import logging
from typing import Any

from core.pipeline import config, events
from services import model_qto_summary

logger = logging.getLogger(__name__)

SUGGESTION_REBUILD_MODEL = "rebuild_model"
SUGGESTION_CREATE_PROPOSAL = "create_proposal"

_QTO_SCOPE_KEY = "project"  # 与 routers/project_models.py get_model_quantities 的项目级汇总 scope 对齐


def _parse_jsonb(value: Any, default: Any = None) -> Any:
    """JSONB 字段经驱动可能返回 str，安全解析（对齐 routers/project_models.py 同名工具）。"""
    if value is None:
        return default
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return default
    return value


_UPSERT_SUGGESTION_SQL = """
INSERT INTO pipeline_suggestions
    (project_id, event_id, suggestion_type, status, title, summary, payload)
VALUES
    (:project_id, :event_id, :suggestion_type, 'open', :title, :summary, CAST(:payload AS jsonb))
ON CONFLICT (project_id, suggestion_type) WHERE status = 'open'
DO UPDATE SET
    event_id = EXCLUDED.event_id,
    title = EXCLUDED.title,
    summary = EXCLUDED.summary,
    payload = EXCLUDED.payload,
    created_at = now()
RETURNING id
"""


async def _upsert_suggestion(
    db,
    *,
    project_id: str,
    event_id: str | None,
    suggestion_type: str,
    title: str,
    summary: str,
    payload: dict[str, Any],
) -> str:
    row = await db.fetch_one(
        _UPSERT_SUGGESTION_SQL,
        {
            "project_id": project_id,
            "event_id": event_id,
            "suggestion_type": suggestion_type,
            "title": title,
            "summary": summary,
            "payload": json.dumps(payload, ensure_ascii=False, default=str),
        },
    )
    return str(row["id"])


# ── ai_review.completed → rebuild-impact 建议 ────────────────────

async def _compute_rebuild_impact(db, project_id: str) -> dict[str, Any]:
    """自上次建模以来「审图已完成」的图纸数——只读 SELECT，不改动 project_models/drawings。"""
    model_row = await db.fetch_one(
        "SELECT version, built_at FROM project_models WHERE project_id=:project_id",
        {"project_id": project_id},
    )
    built_at = model_row["built_at"] if model_row else None
    model_version = model_row["version"] if model_row else None

    if built_at is not None:
        changed_rows = await db.fetch_all(
            """
            SELECT id, drawing_no FROM drawings
            WHERE project_id=:project_id AND status='ai_done' AND updated_at > :built_at
            ORDER BY updated_at DESC
            """,
            {"project_id": project_id, "built_at": built_at},
        )
    else:
        changed_rows = await db.fetch_all(
            """
            SELECT id, drawing_no FROM drawings
            WHERE project_id=:project_id AND status='ai_done'
            ORDER BY updated_at DESC
            """,
            {"project_id": project_id},
        )

    changed = list(changed_rows)
    return {
        "changed_drawing_count": len(changed),
        "changed_drawing_ids": [str(r["id"]) for r in changed[:50]],
        "model_version": model_version,
        "model_built_at": built_at.isoformat() if hasattr(built_at, "isoformat") else built_at,
        "model_ever_built": built_at is not None,
    }


async def handle_ai_review_completed(db, event: dict[str, Any]) -> dict[str, Any]:
    project_id = str(event["project_id"])

    if not await config.is_step_enabled(db, project_id, config.STEP_AI_REVIEW_TO_REBUILD_SUGGESTION):
        return {"skipped": "disabled"}

    impact = await _compute_rebuild_impact(db, project_id)
    threshold = await config.get_rebuild_impact_min_drawings(db)

    if impact["changed_drawing_count"] < threshold:
        return {"suggested": False, "threshold": threshold, **impact}

    verb = "建模" if impact["model_ever_built"] else "首次建模"
    summary = (
        f"自上次{verb}以来，已有 {impact['changed_drawing_count']} 张图纸完成 AI 审图"
        f"（阈值 {threshold}），当前模型可能已过期，建议重新构建。"
    )
    suggestion_id = await _upsert_suggestion(
        db,
        project_id=project_id,
        event_id=str(event.get("id")) if event.get("id") else None,
        suggestion_type=SUGGESTION_REBUILD_MODEL,
        title="建议重新构建工程模型",
        summary=summary,
        payload={**impact, "threshold": threshold},
    )
    logger.info("pipeline: 生成重建模型建议 project_id=%s suggestion_id=%s", project_id, suggestion_id)
    return {"suggested": True, "suggestion_id": suggestion_id, "threshold": threshold, **impact}


# ── model.built → QTO 刷新 + 创效建议 ─────────────────────────────

async def handle_model_built(db, event: dict[str, Any]) -> dict[str, Any]:
    project_id = str(event["project_id"])

    row = await db.fetch_one(
        "SELECT scene FROM project_models WHERE project_id=:project_id",
        {"project_id": project_id},
    )
    scene = _parse_jsonb(row["scene"]) if row else None
    if not scene:
        return {"skipped": "no_scene"}

    # 建模前的上一版持久化汇总（用于对比钢筋量变化）——不影响本次刷新写入。
    previous = await model_qto_summary.fetch_quantity_summary(db, project_id, _QTO_SCOPE_KEY)
    previous_rebar_kg = None
    if previous:
        previous_payload = previous.get("payload") or {}
        previous_rebar_kg = ((previous_payload.get("rebar") or {}).get("total_kg"))

    # ── 刷新 QTO：用既有公开函数重算并持久化项目级汇总 ──────────────
    quantities = model_qto_summary.build_scene_quantities(scene)
    project_summary = quantities["project"]
    await model_qto_summary.save_quantity_summary(db, project_id, _QTO_SCOPE_KEY, project_summary)

    if not await config.is_step_enabled(db, project_id, config.STEP_MODEL_BUILT_TO_PROPOSAL_SUGGESTION):
        return {"skipped": "disabled", "qto_refreshed": True}

    current_rebar = project_summary.get("rebar") or {}
    current_rebar_kg = current_rebar.get("total_kg")

    estimated_saving_yuan = None
    if (
        previous_rebar_kg is not None
        and current_rebar_kg is not None
        and previous_rebar_kg > current_rebar_kg
    ):
        steel_price_per_ton = await config.get_steel_price_per_ton(db)
        estimated_saving_yuan = round(
            (previous_rebar_kg - current_rebar_kg) / 1000.0 * steel_price_per_ton, 2
        )

    result: dict[str, Any] = {
        "qto_refreshed": True,
        "previous_rebar_kg": previous_rebar_kg,
        "current_rebar_kg": current_rebar_kg,
        "estimated_saving_yuan": estimated_saving_yuan,
    }

    if estimated_saving_yuan is None:
        return {"suggested": False, **result}

    threshold = await config.get_qto_saving_threshold_yuan(db)
    if estimated_saving_yuan <= threshold:
        return {"suggested": False, "threshold": threshold, **result}

    steel_price_per_ton = await config.get_steel_price_per_ton(db)
    summary = (
        f"模型重建后钢筋量由 {previous_rebar_kg} kg 降至 {current_rebar_kg} kg，"
        f"按 {steel_price_per_ton} 元/吨估算预计净节约约 {estimated_saving_yuan} 元"
        f"（超过阈值 {threshold} 元，估算值待经济师复核后再创建正式创效提案）。"
    )
    suggestion_id = await _upsert_suggestion(
        db,
        project_id=project_id,
        event_id=str(event.get("id")) if event.get("id") else None,
        suggestion_type=SUGGESTION_CREATE_PROPOSAL,
        title="建议创建创效提案",
        summary=summary,
        payload={**result, "threshold": threshold, "steel_price_per_ton": steel_price_per_ton},
    )
    logger.info("pipeline: 生成创效提案建议 project_id=%s suggestion_id=%s", project_id, suggestion_id)
    return {"suggested": True, "suggestion_id": suggestion_id, "threshold": threshold, **result}


# ── model.built 事件发射点（留给 tasks/model_build.py 集成） ──────

async def emit_model_built_event(db, *, project_id: str, version: int | None = None) -> str | None:
    """供 tasks/model_build.py 在模型构建成功落库后调用的发射函数。

    本工作块的文件边界不包含 tasks/model_build.py，因此接线点暂不落地；
    集成方式：在该文件模型构建成功（``project_models.status='ready'`` 或等价
    成功分支）落库后，调用：

        from core.pipeline.handlers import emit_model_built_event
        await emit_model_built_event(db, project_id=project_id, version=version)

    内部只是 events.emit_event 的一层薄封装，统一 payload 形状，避免各调用点
    自行拼 event_type 字符串。
    """
    return await events.emit_event(
        db,
        event_type=events.EVENT_MODEL_BUILT,
        project_id=project_id,
        payload={"version": version} if version is not None else {},
    )


# ── 分发 ──────────────────────────────────────────────────────────

_HANDLERS = {
    events.EVENT_AI_REVIEW_COMPLETED: handle_ai_review_completed,
    events.EVENT_MODEL_BUILT: handle_model_built,
}


async def dispatch(db, event: dict[str, Any]) -> dict[str, Any]:
    """按 event_type 路由到具体处理器；未知类型直接跳过（不视为失败）。"""
    event_type = event.get("event_type")
    handler = _HANDLERS.get(event_type)
    if handler is None:
        return {"skipped": "no_handler", "event_type": event_type}
    return await handler(db, event)
