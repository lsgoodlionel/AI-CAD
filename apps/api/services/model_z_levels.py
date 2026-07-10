"""跨视图 z 恢复标高表仓储（B-03，Repository Pattern）。

读写 migration 019 的 model_z_recovery_levels 表：跨视图恢复的真实标高/层高及其溯源
（source ∈ section|elevation|estimated、confidence、evidence_ref 证据链）。

与 model_story.py（纯归一化逻辑）职责分离：本模块只做持久化，不含识别算法。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

VALID_Z_SOURCES = {"section", "elevation", "estimated"}

_UPSERT_SQL = """
INSERT INTO model_z_recovery_levels (
    project_id, scope_key, story_key, story_order,
    elevation_bottom_m, story_height_m, source, confidence, evidence_ref
)
VALUES (
    :project_id, :scope_key, :story_key, :story_order,
    :elevation_bottom_m, :story_height_m, :source, :confidence,
    CAST(:evidence_ref AS jsonb)
)
ON CONFLICT (project_id, scope_key, story_key)
DO UPDATE SET
    story_order = EXCLUDED.story_order,
    elevation_bottom_m = EXCLUDED.elevation_bottom_m,
    story_height_m = EXCLUDED.story_height_m,
    source = EXCLUDED.source,
    confidence = EXCLUDED.confidence,
    evidence_ref = EXCLUDED.evidence_ref,
    updated_at = now()
"""

_SELECT_SQL = """
SELECT
    scope_key, story_key, story_order,
    elevation_bottom_m, story_height_m, source, confidence, evidence_ref
FROM model_z_recovery_levels
WHERE project_id = :project_id
ORDER BY scope_key, story_order
"""


@dataclass(frozen=True)
class ZLevelEntry:
    """单层跨视图 z 恢复结果（落库前的值对象）。"""
    scope_key: str
    story_key: str
    story_order: int
    elevation_bottom_m: float
    story_height_m: float
    source: str
    confidence: float
    evidence_ref: dict[str, Any] = field(default_factory=dict)


def build_z_level_params(project_id: str, entry: ZLevelEntry) -> dict[str, Any]:
    """构造 upsert 绑定参数（边界校验 + evidence_ref 序列化）。"""
    if entry.source not in VALID_Z_SOURCES:
        raise ValueError(
            f"invalid z-level source {entry.source!r}; expected one of {sorted(VALID_Z_SOURCES)}"
        )
    return {
        "project_id": project_id,
        "scope_key": entry.scope_key,
        "story_key": entry.story_key,
        "story_order": int(entry.story_order),
        "elevation_bottom_m": round(float(entry.elevation_bottom_m), 3),
        "story_height_m": round(float(entry.story_height_m), 3),
        "source": entry.source,
        "confidence": round(float(entry.confidence), 4),
        "evidence_ref": json.dumps(entry.evidence_ref or {}, ensure_ascii=False),
    }


async def upsert_z_levels(db, project_id: str, entries: list[ZLevelEntry]) -> int:
    """批量 upsert 标高表，返回写入条数。空列表为 no-op。"""
    written = 0
    for entry in entries or []:
        await db.execute(_UPSERT_SQL, build_z_level_params(project_id, entry))
        written += 1
    return written


async def fetch_z_levels(db, project_id: str) -> list[dict[str, Any]]:
    """读取项目全部 z 恢复标高，evidence_ref 从 jsonb 解析为 dict。"""
    rows = await db.fetch_all(_SELECT_SQL, {"project_id": project_id})
    result: list[dict[str, Any]] = []
    for row in rows or []:
        record = dict(row)
        record["evidence_ref"] = _parse_evidence_ref(record.get("evidence_ref"))
        result.append(record)
    return result


def to_height_overrides(rows: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    """把标高行映射为 (scope_key, story_key) → 层高覆盖，供 B-04 注入 normalize_story_table。"""
    overrides: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows or []:
        scope_key = str(row.get("scope_key") or "")
        story_key = str(row.get("story_key") or "")
        if not scope_key or not story_key:
            continue
        overrides[(scope_key, story_key)] = {
            "height_m": float(row.get("story_height_m") or 0.0),
            "elevation_bottom_m": float(row.get("elevation_bottom_m") or 0.0),
            "source": str(row.get("source") or "estimated"),
            "confidence": float(row.get("confidence") or 0.0),
        }
    return overrides


def _parse_evidence_ref(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return {}
    return dict(value) if isinstance(value, dict) else {}
