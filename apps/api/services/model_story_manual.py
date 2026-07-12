"""楼层标高人工录入/校正仓储(Task 3)。

自动识别打底,人工在此录入/校正真实层高;建模时作为最高优先级 z_override
(source='manual')覆盖自动估算,消除均匀默认层高。

- ``fetch_manual_overrides``:读出 → normalize_story_table 消费的 z_overrides 形状
  {(scope_key, story_key): {height_m, elevation_bottom_m?, source:'manual', confidence:1.0}}
- ``fetch_manual_rows``:读出原始行(前端展示已录入值)
- ``save_manual_heights``:UPSERT 人工层高
"""
from __future__ import annotations

from typing import Any

_FETCH_SQL = """
SELECT scope_key, story_key, story_order, height_m, elevation_bottom_m, note, updated_by, updated_at
FROM model_story_manual_heights
WHERE project_id = :project_id
ORDER BY scope_key, story_order
"""

_UPSERT_SQL = """
INSERT INTO model_story_manual_heights
    (project_id, scope_key, story_key, story_order, height_m, elevation_bottom_m, note, updated_by, updated_at)
VALUES
    (:project_id, :scope_key, :story_key, :story_order, :height_m, :elevation_bottom_m, :note, :updated_by, now())
ON CONFLICT (project_id, scope_key, story_key) DO UPDATE SET
    story_order = EXCLUDED.story_order,
    height_m = EXCLUDED.height_m,
    elevation_bottom_m = EXCLUDED.elevation_bottom_m,
    note = EXCLUDED.note,
    updated_by = EXCLUDED.updated_by,
    updated_at = now()
"""

_DELETE_SQL = """
DELETE FROM model_story_manual_heights
WHERE project_id = :project_id AND scope_key = :scope_key AND story_key = :story_key
"""


async def fetch_manual_rows(db, project_id: str) -> list[dict[str, Any]]:
    """读出人工层高原始行(供前端展示已录入值)。表缺失时返回空。"""
    try:
        rows = await db.fetch_all(_FETCH_SQL, {"project_id": project_id})
    except Exception:  # noqa: BLE001 — 表未部署等 → 优雅降级为空
        return []
    return [dict(row) for row in rows]


async def fetch_manual_overrides(
    db, project_id: str,
) -> dict[tuple[str, str], dict[str, Any]]:
    """读出为 z_overrides 形状,供 normalize_story_table 消费(source='manual')。"""
    overrides: dict[tuple[str, str], dict[str, Any]] = {}
    for row in await fetch_manual_rows(db, project_id):
        override: dict[str, Any] = {
            "height_m": float(row["height_m"]),
            "source": "manual",
            "confidence": 1.0,
        }
        if row.get("elevation_bottom_m") is not None:
            override["elevation_bottom_m"] = float(row["elevation_bottom_m"])
        overrides[(str(row["scope_key"]), str(row["story_key"]))] = override
    return overrides


async def save_manual_heights(
    db, project_id: str, items: list[dict[str, Any]], updated_by: str | None = None,
) -> int:
    """UPSERT 人工层高。height_m<=0 视为删除该录入(恢复自动)。返回处理条数。"""
    count = 0
    for item in items:
        scope_key = str(item.get("scope_key") or "main")
        story_key = str(item.get("story_key") or "")
        if not story_key:
            continue
        height = item.get("height_m")
        if height is None or float(height) <= 0:
            await db.execute(
                _DELETE_SQL,
                {"project_id": project_id, "scope_key": scope_key, "story_key": story_key},
            )
            count += 1
            continue
        await db.execute(
            _UPSERT_SQL,
            {
                "project_id": project_id,
                "scope_key": scope_key,
                "story_key": story_key,
                "story_order": int(item.get("story_order") or 0),
                "height_m": round(float(height), 3),
                "elevation_bottom_m": (
                    round(float(item["elevation_bottom_m"]), 3)
                    if item.get("elevation_bottom_m") is not None
                    else None
                ),
                "note": item.get("note"),
                "updated_by": updated_by,
            },
        )
        count += 1
    return count
