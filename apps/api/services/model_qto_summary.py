"""QTO 汇总（B-19）：构件量 → 项目/单体/楼层级工程量汇总 + 持久化。

聚合混凝土（m³）/ 模板（m²）/ 钢筋（t），带实测/估算标记与「未覆盖」显式计数
（量集缺失构件不静默漏量）。分楼层 / 分单体可下钻。含 migration 022 读写。
"""
from __future__ import annotations

import json
from typing import Any

from services.model_qto import (
    ElementQuantity,
    compute_quantities,
    compute_rebar_quantities,
)

_DEFAULT_STORY_HEIGHT_M = 4.5


def summarize(quantities: list[ElementQuantity]) -> dict[str, Any]:
    """把一组构件量汇总为混凝土/模板合计 + 分类型 + 实测/估算/未覆盖计数。"""
    by_type: dict[str, dict[str, Any]] = {}
    gross = net = contact = free = 0.0
    measured = estimated = uncovered = 0

    for quantity in quantities:
        gross += quantity.gross_volume_m3
        net += quantity.net_volume_m3
        contact += quantity.formwork_contact_m2
        free += quantity.formwork_free_m2
        if quantity.estimated:
            estimated += 1
        else:
            measured += 1
        if quantity.gross_volume_m3 <= 0:
            uncovered += 1

        bucket = by_type.setdefault(
            quantity.element_type,
            {"count": 0, "gross_m3": 0.0, "net_m3": 0.0, "formwork_contact_m2": 0.0},
        )
        bucket["count"] += 1
        bucket["gross_m3"] = round(bucket["gross_m3"] + quantity.gross_volume_m3, 4)
        bucket["net_m3"] = round(bucket["net_m3"] + quantity.net_volume_m3, 4)
        bucket["formwork_contact_m2"] = round(
            bucket["formwork_contact_m2"] + quantity.formwork_contact_m2, 4
        )

    return {
        "concrete": {"gross_m3": round(gross, 4), "net_m3": round(net, 4)},
        "formwork": {"contact_m2": round(contact, 4), "free_m2": round(free, 4)},
        "by_type": by_type,
        "element_count": len(quantities),
        "measured_count": measured,
        "estimated_count": estimated,
        "uncovered_count": uncovered,
    }


def build_scene_quantities(
    scene: dict,
    *,
    rebar_inputs: list[dict] | None = None,
    rebar_params: dict | None = None,
) -> dict[str, Any]:
    """从 scene 分楼层算量 → 项目/楼层/单体汇总 + 项目级钢筋。"""
    floors = scene.get("floors") or []
    height_by_story = _story_heights(scene)

    all_quantities: list[ElementQuantity] = []
    by_floor: list[dict[str, Any]] = []
    by_building_q: dict[str, list[ElementQuantity]] = {}

    for floor in floors:
        elements = floor.get("elements") or {}
        height = float(height_by_story.get(floor.get("key"), _DEFAULT_STORY_HEIGHT_M))
        quantities = compute_quantities(elements, story_height_m=height)
        all_quantities.extend(quantities)
        by_floor.append({
            "floor_key": floor.get("key"),
            "floor_label": floor.get("label"),
            **summarize(quantities),
        })
        for unit in floor.get("building_units") or ["main"]:
            by_building_q.setdefault(unit, []).extend(quantities)

    project = summarize(all_quantities)
    project["rebar"] = _rebar_block(compute_rebar_quantities(rebar_inputs or [], rebar_params))

    by_building = [
        {"building_key": unit, **summarize(quantities)}
        for unit, quantities in sorted(by_building_q.items())
    ]
    return {"project": project, "by_floor": by_floor, "by_building": by_building}


def _rebar_block(rebar: dict) -> dict[str, Any]:
    if rebar.get("rebar_missing"):
        return {"missing": True, "total_kg": None, "total_t": None}
    total_kg = float(rebar.get("total_steel_kg") or 0.0)
    return {"missing": False, "total_kg": round(total_kg, 2), "total_t": round(total_kg / 1000.0, 4)}


def _story_heights(scene: dict) -> dict[str, float]:
    heights: dict[str, float] = {}
    story_tables = ((scene.get("quality") or {}).get("story_tables")) or {}
    for levels in story_tables.values():
        for level in levels or []:
            key = level.get("story_key")
            if key is not None and level.get("height_m") is not None:
                heights[key] = float(level["height_m"])
    return heights


# ── 持久化仓储 ─────────────────────────────────────────────────

_UPSERT_SQL = """
INSERT INTO model_quantities (
    project_id, scope_key, concrete_net_m3, concrete_gross_m3,
    formwork_contact_m2, rebar_kg, estimated_ratio, payload
)
VALUES (
    :project_id, :scope_key, :concrete_net_m3, :concrete_gross_m3,
    :formwork_contact_m2, :rebar_kg, :estimated_ratio, CAST(:payload AS jsonb)
)
ON CONFLICT (project_id, scope_key)
DO UPDATE SET
    concrete_net_m3 = EXCLUDED.concrete_net_m3,
    concrete_gross_m3 = EXCLUDED.concrete_gross_m3,
    formwork_contact_m2 = EXCLUDED.formwork_contact_m2,
    rebar_kg = EXCLUDED.rebar_kg,
    estimated_ratio = EXCLUDED.estimated_ratio,
    payload = EXCLUDED.payload,
    generated_at = now()
"""

_SELECT_SQL = """
SELECT scope_key, concrete_net_m3, concrete_gross_m3, formwork_contact_m2,
       rebar_kg, estimated_ratio, payload
FROM model_quantities
WHERE project_id = :project_id AND scope_key = :scope_key
"""


async def save_quantity_summary(db, project_id: str, scope_key: str, project_summary: dict) -> None:
    element_count = int(project_summary.get("element_count") or 0)
    estimated = int(project_summary.get("estimated_count") or 0)
    rebar = project_summary.get("rebar") or {}
    await db.execute(_UPSERT_SQL, {
        "project_id": project_id,
        "scope_key": scope_key,
        "concrete_net_m3": project_summary["concrete"]["net_m3"],
        "concrete_gross_m3": project_summary["concrete"]["gross_m3"],
        "formwork_contact_m2": project_summary["formwork"]["contact_m2"],
        "rebar_kg": rebar.get("total_kg"),
        "estimated_ratio": round(estimated / element_count, 4) if element_count else 0.0,
        "payload": json.dumps(project_summary, ensure_ascii=False),
    })


async def fetch_quantity_summary(db, project_id: str, scope_key: str) -> dict[str, Any] | None:
    row = await db.fetch_one(_SELECT_SQL, {"project_id": project_id, "scope_key": scope_key})
    if row is None:
        return None
    record = dict(row)
    record["payload"] = _parse_payload(record.get("payload"))
    return record


def _parse_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return {}
    return dict(value) if isinstance(value, dict) else {}
