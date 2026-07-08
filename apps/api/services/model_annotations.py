from __future__ import annotations

import json
import re
from typing import Any

_UNIT_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

_UPSERT_DRAWING_ANNOTATION_SQL = """
INSERT INTO drawing_model_annotations (
    project_id,
    drawing_id,
    building_unit_key,
    building_unit_display_name,
    story_key,
    story_display_name,
    drawing_type,
    candidate_sources,
    confidence,
    annotated_by,
    annotation_source
)
VALUES (
    :project_id,
    :drawing_id,
    :building_unit_key,
    :building_unit_display_name,
    :story_key,
    :story_display_name,
    :drawing_type,
    CAST(:candidate_sources AS jsonb),
    :confidence,
    :annotated_by,
    'manual'
)
ON CONFLICT (project_id, drawing_id)
DO UPDATE SET
    building_unit_key = EXCLUDED.building_unit_key,
    building_unit_display_name = EXCLUDED.building_unit_display_name,
    story_key = EXCLUDED.story_key,
    story_display_name = EXCLUDED.story_display_name,
    drawing_type = EXCLUDED.drawing_type,
    candidate_sources = EXCLUDED.candidate_sources,
    confidence = EXCLUDED.confidence,
    annotated_by = EXCLUDED.annotated_by,
    annotation_source = 'manual',
    updated_at = CURRENT_TIMESTAMP
RETURNING
    project_id,
    drawing_id,
    building_unit_key,
    building_unit_display_name,
    story_key,
    story_display_name,
    drawing_type,
    candidate_sources,
    confidence
"""

_SELECT_DRAWING_ANNOTATIONS_SQL = """
SELECT
    drawing_id,
    building_unit_key,
    building_unit_display_name,
    story_key,
    story_display_name,
    drawing_type,
    candidate_sources,
    confidence,
    elevation_m
FROM drawing_model_annotations
WHERE project_id = :project_id
"""


def _candidate_sources(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return []
    return list(value) if isinstance(value, list) else []


def build_manual_annotation_payload(
    *,
    project_id: str,
    drawing_id: str,
    building_unit_key: str,
    building_unit_display_name: str,
    story_key: str | None = None,
    story_display_name: str | None = None,
    drawing_type: str | None = None,
    candidate_sources: list[dict[str, Any]] | None = None,
    confidence: float = 1.0,
    annotated_by: str | None = None,
) -> dict[str, Any]:
    unit_key = str(building_unit_key or "").strip().lower()
    if not _UNIT_KEY_RE.match(unit_key):
        raise ValueError("building_unit_key must match ^[a-z0-9][a-z0-9_-]{0,63}$")

    display_name = str(building_unit_display_name or "").strip()
    if not display_name:
        raise ValueError("building_unit_display_name is required")

    sources = candidate_sources or [{"source": "manual", "value": display_name}]
    return {
        "project_id": project_id,
        "drawing_id": drawing_id,
        "building_unit_key": unit_key,
        "building_unit_display_name": display_name,
        "story_key": str(story_key or "").strip() or None,
        "story_display_name": str(story_display_name or "").strip() or None,
        "drawing_type": str(drawing_type or "").strip() or None,
        "candidate_sources": list(sources),
        "confidence": float(confidence),
        "annotated_by": annotated_by,
    }


def list_building_unit_options(
    *,
    detected_units: list[dict[str, Any]],
    annotations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}

    for item in detected_units:
        unit_key = str(item.get("unit_key") or "").strip()
        if not unit_key:
            continue
        merged[unit_key] = {
            "unit_key": unit_key,
            "display_name": str(item.get("display_name") or unit_key),
            "confidence": float(item.get("confidence") or 0.0),
            "candidate_sources": _candidate_sources(item.get("candidate_sources")),
            "source": str(item.get("source") or "detected"),
        }

    for item in annotations:
        unit_key = str(item.get("building_unit_key") or "").strip()
        if not unit_key:
            continue
        merged[unit_key] = {
            "unit_key": unit_key,
            "display_name": str(item.get("building_unit_display_name") or item.get("display_name") or unit_key),
            "confidence": float(item.get("confidence") or 1.0),
            "candidate_sources": _candidate_sources(item.get("candidate_sources")),
            "source": "manual",
        }

    return sorted(merged.values(), key=lambda item: (item["unit_key"] != "main", item["unit_key"]))


async def save_drawing_annotation(
    db,
    *,
    project_id: str,
    drawing_id: str,
    payload: dict[str, Any],
    annotated_by: str | None,
) -> dict[str, Any]:
    record = build_manual_annotation_payload(
        project_id=project_id,
        drawing_id=drawing_id,
        building_unit_key=payload.get("building_unit_key") or "",
        building_unit_display_name=(
            payload.get("building_unit_display_name")
            or payload.get("building_unit_name")
            or payload.get("display_name")
            or ""
        ),
        story_key=payload.get("story_key"),
        story_display_name=payload.get("story_display_name") or payload.get("story_name"),
        drawing_type=payload.get("drawing_type"),
        candidate_sources=_candidate_sources(payload.get("candidate_sources")),
        confidence=float(payload.get("confidence") or 1.0),
        annotated_by=annotated_by,
    )
    params = {
        **record,
        "candidate_sources": json.dumps(record["candidate_sources"], ensure_ascii=False),
    }
    row = await db.fetch_one(_UPSERT_DRAWING_ANNOTATION_SQL, params)
    return dict(row) if row is not None else record


async def load_annotation_overrides(db, project_id: str) -> dict[str, dict[str, Any]]:
    rows = await db.fetch_all(_SELECT_DRAWING_ANNOTATIONS_SQL, {"project_id": project_id})
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        record = dict(row)
        record["candidate_sources"] = _candidate_sources(record.get("candidate_sources"))
        result[str(record["drawing_id"])] = record
    return result
