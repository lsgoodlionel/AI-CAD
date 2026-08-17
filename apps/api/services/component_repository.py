"""Phase H(H1 仓储 / H3 持久化):ComponentInstance 集 ↔ migration 033 两表。

persist_instances:装配产物写入 component_instances + component_observations
(每实体一行 + 其观测多行,instance_id 外键关联)。
fetch_instances:读回某次建模的实体 + 观测(供追溯/前端消费)。
replace_instances:按 (project_id, model_version) 幂等重建(先删后写)。
"""
from __future__ import annotations

import json
import uuid
from typing import Any


def _is_uuid(value: Any) -> bool:
    """drawing_id 是否真实 UUID。合成来源(如 'piles-envelope' 推断桩)非 UUID,
    无法满足 component_observations.drawing_id 外键 → 该观测跳过(实体仍保留)。"""
    try:
        uuid.UUID(str(value))
        return True
    except (ValueError, TypeError, AttributeError):
        return False

_INSERT_INSTANCE_SQL = """
INSERT INTO component_instances (
    project_id, model_version, building_key, semantic_key, type, grid_ref,
    outline_m, z_bottom_m, z_top_m, z_source, section_json, type_label,
    review_state, confidence
) VALUES (
    :project_id, :model_version, :building_key, :semantic_key, :type, :grid_ref,
    CAST(:outline_m AS jsonb), :z_bottom_m, :z_top_m, :z_source,
    CAST(:section_json AS jsonb), :type_label, :review_state, :confidence
)
RETURNING id
"""

_INSERT_OBS_SQL = """
INSERT INTO component_observations (
    instance_id, drawing_id, view_type, engine, grid_cell,
    local_coord, world_coord, archive_ref, confidence
) VALUES (
    :instance_id, :drawing_id, :view_type, :engine, :grid_cell,
    CAST(:local_coord AS jsonb), CAST(:world_coord AS jsonb), :archive_ref, :confidence
)
"""

_DELETE_INSTANCES_SQL = (
    "DELETE FROM component_instances "
    "WHERE project_id = :project_id AND model_version = :model_version"
)


def _dumps(value: Any) -> str | None:
    return json.dumps(value, ensure_ascii=False) if value is not None else None


def _instance_params(inst: dict, project_id: str, model_version: int) -> dict:
    return {
        "project_id": project_id,
        "model_version": model_version,
        "building_key": inst.get("building_key") or "",
        "semantic_key": inst.get("semantic_key"),
        "type": inst["type"],
        "grid_ref": inst.get("grid_ref"),
        "outline_m": _dumps(inst.get("outline_m")),
        "z_bottom_m": inst.get("z_bottom_m"),
        "z_top_m": inst.get("z_top_m"),
        "z_source": inst.get("z_source"),
        "section_json": _dumps(inst.get("section_json")),
        "type_label": inst.get("type_label"),
        "review_state": inst.get("review_state") or "auto",
        "confidence": float(inst.get("confidence") or 0.0),
    }


def _observation_params(obs: dict, instance_id: Any) -> dict:
    return {
        "instance_id": instance_id,
        "drawing_id": obs["drawing_id"],
        "view_type": obs.get("view_type"),
        "engine": obs.get("engine") or "rule",
        "grid_cell": obs.get("grid_cell"),
        "local_coord": _dumps(obs.get("local_coord")),
        "world_coord": _dumps(obs.get("world_coord")),
        "archive_ref": obs.get("archive_ref"),
        "confidence": float(obs.get("confidence") or 0.0),
    }


async def persist_instances(
    db: Any, project_id: str, model_version: int, instances: list[dict],
) -> int:
    """写入实体 + 观测;返回写入的实体数。"""
    written = 0
    for inst in instances:
        row = await db.fetch_one(
            _INSERT_INSTANCE_SQL, _instance_params(inst, project_id, model_version)
        )
        instance_id = row["id"] if row is not None else None
        if instance_id is None:
            continue
        for obs in inst.get("observations") or []:
            # 观测的 drawing_id 须为真实 UUID(FK);合成来源(推断桩等)跳过,实体仍留
            if not _is_uuid(obs.get("drawing_id")):
                continue
            await db.execute(_INSERT_OBS_SQL, _observation_params(obs, instance_id))
        written += 1
    return written


async def replace_instances(
    db: Any, project_id: str, model_version: int, instances: list[dict],
) -> int:
    """幂等重建:先删该次建模的旧实体(观测随 CASCADE 删),再写新的。"""
    await db.execute(
        _DELETE_INSTANCES_SQL,
        {"project_id": project_id, "model_version": model_version},
    )
    return await persist_instances(db, project_id, model_version, instances)


_FETCH_INSTANCES_SQL = """
SELECT id, building_key, semantic_key, type, grid_ref, outline_m,
       z_bottom_m, z_top_m, z_source, section_json, type_label,
       review_state, confidence
FROM component_instances
WHERE project_id = :project_id AND model_version = :model_version
ORDER BY type, grid_ref
"""

_FETCH_OBS_SQL = """
SELECT co.instance_id, co.drawing_id, co.view_type, co.engine, co.grid_cell,
       co.world_coord, co.archive_ref, co.confidence
FROM component_observations co
JOIN component_instances ci ON ci.id = co.instance_id
WHERE ci.project_id = :project_id AND ci.model_version = :model_version
"""


_SUMMARY_SQL = """
SELECT
    count(*) AS total,
    count(*) FILTER (WHERE z_source IS NOT NULL
                     AND z_source <> 'story_default')     AS with_z,
    count(*) FILTER (WHERE grid_ref IS NOT NULL)          AS with_grid,
    count(*) FILTER (WHERE review_state = 'conflict')     AS conflict,
    count(*) FILTER (WHERE review_state = 'confirmed')    AS confirmed,
    count(*) FILTER (WHERE review_state = 'auto')         AS auto
FROM component_instances
WHERE project_id = :project_id AND model_version = :model_version
"""

_BY_TYPE_SQL = """
SELECT type, count(*) AS n
FROM component_instances
WHERE project_id = :project_id AND model_version = :model_version
GROUP BY type ORDER BY n DESC
"""


async def fetch_instances_summary(
    db: Any, project_id: str, model_version: int,
) -> dict:
    """装配实体汇总(不拉全量):总数 + Z/轴网覆盖 + 审核态 + 按类型计数。"""
    params = {"project_id": project_id, "model_version": model_version}
    total = await db.fetch_one(_SUMMARY_SQL, params)
    by_type = await db.fetch_all(_BY_TYPE_SQL, params)
    total = dict(total) if total else {}
    return {
        "total": int(total.get("total") or 0),
        "with_z": int(total.get("with_z") or 0),
        "with_grid": int(total.get("with_grid") or 0),
        "conflict": int(total.get("conflict") or 0),
        "confirmed": int(total.get("confirmed") or 0),
        "auto": int(total.get("auto") or 0),
        "by_type": {r["type"]: int(r["n"]) for r in by_type},
    }


_REVIEW_QUEUE_SQL = """
SELECT ci.id, ci.type, ci.grid_ref, ci.type_label, ci.confidence, ci.building_key,
       array_agg(DISTINCT d.drawing_no) FILTER (WHERE d.drawing_no IS NOT NULL) AS source_drawings,
       array_agg(DISTINCT co.engine) FILTER (WHERE co.engine IS NOT NULL) AS engines,
       count(co.id) AS obs_count
FROM component_instances ci
LEFT JOIN component_observations co ON co.instance_id = ci.id
LEFT JOIN drawings d ON d.id = co.drawing_id
WHERE ci.project_id = :project_id AND ci.model_version = :model_version
  AND ci.review_state = 'conflict'
GROUP BY ci.id, ci.type, ci.grid_ref, ci.type_label, ci.confidence, ci.building_key
ORDER BY ci.confidence ASC
LIMIT :limit
"""

# 审校动作 → 目标 review_state
_REVIEW_STATE = {"confirm": "confirmed", "reject": "rejected", "reclass": "confirmed"}


async def fetch_review_queue(
    db: Any, project_id: str, model_version: int, limit: int = 50,
) -> list[dict]:
    """低置信(conflict)构件人审队列:按置信升序,带来源图纸/识别途径/观测数。"""
    rows = await db.fetch_all(_REVIEW_QUEUE_SQL, {
        "project_id": project_id, "model_version": model_version, "limit": limit,
    })
    out: list[dict] = []
    for r in rows:
        d = dict(r)
        d["id"] = str(d["id"])
        d["source_drawings"] = list(d.get("source_drawings") or [])
        d["engines"] = list(d.get("engines") or [])
        d["obs_count"] = int(d.get("obs_count") or 0)
        out.append(d)
    return out


_OVERLAY_TRANSFORM_SQL = (
    "SELECT scale_m_pt, origin_x, origin_y, page_h "
    "FROM drawing_transform WHERE drawing_id = :drawing_id"
)
_OVERLAY_SQL = """
SELECT ci.id, ci.type, ci.review_state, co.world_coord
FROM component_observations co
JOIN component_instances ci ON ci.id = co.instance_id
WHERE ci.project_id = :project_id AND ci.model_version = :model_version
  AND co.drawing_id = :drawing_id AND co.world_coord IS NOT NULL
LIMIT :limit
"""


async def fetch_overlay(
    db: Any, project_id: str, model_version: int, drawing_id: str, limit: int = 2000,
) -> dict:
    """H4+ 回投:某图纸贡献的构件 → 归一化页面坐标标记(供前端叠加到图纸预览核对)。

    米坐标经 drawing_transform 逆变换回 pt,再按 page_h 归一化(x/y 同除 page_h,
    前端按图片显示高度换算像素,无需 page_w)。无变换 → available=False(无法回投)。
    """
    t = await db.fetch_one(_OVERLAY_TRANSFORM_SQL, {"drawing_id": drawing_id})
    if t is None:
        return {"available": False, "markers": []}
    t = dict(t)
    scale, ox, oy, ph = t["scale_m_pt"], t["origin_x"], t["origin_y"], t["page_h"]
    if not scale or not ph:
        return {"available": False, "markers": []}
    rows = await db.fetch_all(_OVERLAY_SQL, {
        "project_id": project_id, "model_version": model_version,
        "drawing_id": drawing_id, "limit": limit,
    })
    markers: list[dict] = []
    for r in rows:
        wc = r["world_coord"]
        if isinstance(wc, str):
            try:
                wc = json.loads(wc)
            except (ValueError, TypeError):
                continue
        if not wc or len(wc) < 2:
            continue
        x_pt = float(wc[0]) / scale + ox
        y_pt = ph - (float(wc[1]) / scale + oy)   # y 翻转(与正变换一致)
        markers.append({
            "id": str(r["id"]), "type": r["type"], "review_state": r["review_state"],
            "x": round(x_pt / ph, 4), "y": round(y_pt / ph, 4),
        })
    return {"available": True, "markers": markers, "page_h": ph}


_BY_SOURCE_SQL = """
SELECT ci.id, ci.grid_ref, ci.review_state, ci.confidence, ci.z_source, ci.type_label
FROM component_instances ci
WHERE ci.project_id = :project_id AND ci.model_version = :model_version
  AND ci.type = :comp_type
  AND EXISTS (
      SELECT 1 FROM component_observations co
      WHERE co.instance_id = ci.id AND co.drawing_id = :drawing_id)
ORDER BY ci.confidence ASC
LIMIT :limit
"""


async def fetch_instances_by_source(
    db: Any, project_id: str, model_version: int,
    drawing_id: str, comp_type: str, limit: int = 200,
) -> dict:
    """某图纸贡献的某类装配实体 + 审核态统计(H6:3D 点击 → 实体层证据)。"""
    rows = await db.fetch_all(_BY_SOURCE_SQL, {
        "project_id": project_id, "model_version": model_version,
        "comp_type": comp_type, "drawing_id": drawing_id, "limit": limit,
    })
    insts = [{**dict(r), "id": str(r["id"])} for r in rows]
    return {
        "total": len(insts),
        "confirmed": sum(1 for i in insts if i["review_state"] == "confirmed"),
        "conflict": sum(1 for i in insts if i["review_state"] == "conflict"),
        "with_z": sum(1 for i in insts if i.get("z_source")),
        "instances": insts[:20],
    }


_GOLD_LABELS_SQL = """
SELECT co.drawing_id, ci.type AS category, co.local_coord,
       dt.scale_m_pt, dt.origin_x, dt.origin_y, dt.page_h
FROM component_instances ci
JOIN component_observations co ON co.instance_id = ci.id
JOIN drawing_transform dt ON dt.drawing_id = co.drawing_id
WHERE ci.project_id = :project_id AND ci.model_version = :model_version
  AND ci.review_state = 'confirmed' AND co.local_coord IS NOT NULL
LIMIT :limit
"""


async def fetch_component_gold_labels(
    db: Any, project_id: str, model_version: int, limit: int = 5000,
) -> list[dict]:
    """人审 confirmed 构件金标签 → COCO 行 {drawing_id, category, bbox(归一化)}。

    仅取有 drawing_transform 的图(需逆变换算 bbox);无变换的图暂无法导出(如实略过)。
    """
    from services.component_coco import outline_to_norm_bbox
    rows = await db.fetch_all(_GOLD_LABELS_SQL, {
        "project_id": project_id, "model_version": model_version, "limit": limit,
    })
    out: list[dict] = []
    for r in rows:
        d = dict(r)
        outline = d.get("local_coord")
        if isinstance(outline, str):
            try:
                outline = json.loads(outline)
            except (ValueError, TypeError):
                continue
        bbox = outline_to_norm_bbox(
            outline, d["scale_m_pt"], d["origin_x"], d["origin_y"], d["page_h"])
        if bbox is None:
            continue
        out.append({
            "drawing_id": str(d["drawing_id"]), "category": d["category"], "bbox": bbox,
        })
    return out


_REVIEW_ACTION_COUNTS_SQL = """
SELECT action_type, count(*) AS n
FROM model_review_actions
WHERE project_id = :project_id AND target_kind = 'element'
GROUP BY action_type
"""


async def fetch_review_action_counts(db: Any, project_id: str) -> dict[str, int]:
    """构件人审动作计数(H7:人审工作量 by action)。"""
    rows = await db.fetch_all(_REVIEW_ACTION_COUNTS_SQL, {"project_id": project_id})
    return {r["action_type"]: int(r["n"]) for r in rows}


_INSTANCE_FOR_REVIEW_SQL = """
SELECT ci.id, ci.type, ci.grid_ref, ci.type_label, ci.confidence, ci.building_key,
       array_agg(DISTINCT d.drawing_no) FILTER (WHERE d.drawing_no IS NOT NULL) AS source_drawings,
       array_agg(DISTINCT co.engine) FILTER (WHERE co.engine IS NOT NULL) AS engines,
       count(co.id) AS obs_count
FROM component_instances ci
LEFT JOIN component_observations co ON co.instance_id = ci.id
LEFT JOIN drawings d ON d.id = co.drawing_id
WHERE ci.id = :instance_id AND ci.project_id = :project_id
GROUP BY ci.id, ci.type, ci.grid_ref, ci.type_label, ci.confidence, ci.building_key
"""


async def fetch_instance_for_review(
    db: Any, project_id: str, instance_id: str,
) -> dict | None:
    """单个构件 + provenance(供 H5 大模型复核)。不存在返回 None。"""
    row = await db.fetch_one(_INSTANCE_FOR_REVIEW_SQL, {
        "instance_id": instance_id, "project_id": project_id,
    })
    if row is None:
        return None
    d = dict(row)
    d["id"] = str(d["id"])
    d["source_drawings"] = list(d.get("source_drawings") or [])
    d["engines"] = list(d.get("engines") or [])
    d["obs_count"] = int(d.get("obs_count") or 0)
    return d


_APPLY_REVIEW_SQL = """
UPDATE component_instances
SET review_state = :review_state,
    type = COALESCE(:new_type, type),
    reviewed_by = :reviewer_id, reviewed_at = now()
WHERE id = :instance_id AND project_id = :project_id
RETURNING id, type, review_state
"""


async def apply_component_review(
    db: Any, project_id: str, instance_id: str, action: str,
    reviewer_id: str | None, new_type: str | None = None,
) -> dict | None:
    """人审动作 → 翻转实体 review_state(confirm/reject/reclass)。返回更新后行或 None。

    reclass 时用 new_type 改类型;confirm/reject 不改类型(new_type=None)。
    """
    review_state = _REVIEW_STATE.get(action)
    if review_state is None:
        return None
    row = await db.fetch_one(_APPLY_REVIEW_SQL, {
        "review_state": review_state,
        "new_type": new_type if action == "reclass" else None,
        "reviewer_id": reviewer_id,
        "instance_id": instance_id,
        "project_id": project_id,
    })
    return dict(row) if row is not None else None


async def fetch_instances(
    db: Any, project_id: str, model_version: int,
) -> list[dict]:
    """读回实体 + 各自观测(observations 挂在实体下,供追溯/前端)。"""
    rows = await db.fetch_all(
        _FETCH_INSTANCES_SQL,
        {"project_id": project_id, "model_version": model_version},
    )
    instances = {str(r["id"]): {**dict(r), "id": str(r["id"]), "observations": []}
                 for r in rows}
    obs_rows = await db.fetch_all(
        _FETCH_OBS_SQL,
        {"project_id": project_id, "model_version": model_version},
    )
    for obs in obs_rows:
        inst = instances.get(str(obs["instance_id"]))
        if inst is not None:
            inst["observations"].append(dict(obs))
    return list(instances.values())
