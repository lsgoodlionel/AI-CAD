from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

DEFAULT_STORY_HEIGHT_M = 4.5
DEFAULT_FALLBACK_WIDTH_M = 18.0
DEFAULT_FALLBACK_DEPTH_M = 12.0


def build_initial_lod_volumes(
    *,
    stories_by_building: Mapping[str, Iterable[Mapping[str, Any]]] | None,
    building_units: Iterable[Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Build LOD100-style massing records for arbitrary building units."""
    stories_map = {
        str(unit_key): [dict(story) for story in stories]
        for unit_key, stories in (stories_by_building or {}).items()
    }
    unit_index = _index_building_units(building_units)
    unit_keys = sorted(set(stories_map) | set(unit_index))
    volumes: list[dict[str, Any]] = []

    for unit_key in unit_keys:
        stories = stories_map.get(unit_key, [])
        unit = unit_index.get(unit_key, {"unit_key": unit_key})
        geometry, geometry_confidence, geometry_notes = _resolve_geometry(unit, stories)
        height_m, height_confidence, height_notes = _resolve_height(stories)
        notes = [*geometry_notes, *height_notes]
        volumes.append(
            {
                "unit_key": unit_key,
                "display_name": unit.get("display_name") or unit_key,
                "height_m": round(height_m, 3),
                "story_count": len(stories),
                "confidence": _combine_confidence(geometry_confidence, height_confidence),
                "geometry": geometry,
                "stories": [
                    {
                        "story_key": story.get("story_key"),
                        "story_order": story.get("story_order"),
                        "height_m": story.get("height_m"),
                    }
                    for story in sorted(stories, key=_story_sort_key)
                ],
                "notes": notes,
                "lod": 100,
            }
        )
    return volumes


def _index_building_units(
    building_units: Iterable[Mapping[str, Any]] | None,
) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for unit in building_units or []:
        unit_key = unit.get("unit_key") or unit.get("key")
        if unit_key:
            indexed[str(unit_key)] = dict(unit)
    return indexed


def _resolve_height(stories: list[dict[str, Any]]) -> tuple[float, float, list[str]]:
    if not stories:
        return DEFAULT_STORY_HEIGHT_M, 0.2, ["No story records provided; using default single-story height."]

    known_heights = [
        float(story["height_m"])
        for story in stories
        if story.get("height_m") not in (None, "")
    ]
    missing_count = len(stories) - len(known_heights)
    total_height = sum(known_heights) + missing_count * DEFAULT_STORY_HEIGHT_M
    if missing_count:
        return (
            total_height,
            0.55,
            [f"{missing_count} stories used default story height {DEFAULT_STORY_HEIGHT_M}m."],
        )
    return total_height, 0.95, []


def _resolve_geometry(
    unit: Mapping[str, Any], stories: list[dict[str, Any]]
) -> tuple[dict[str, Any], float, list[str]]:
    source_bounds = unit.get("source_bounds")
    if isinstance(source_bounds, Mapping):
        width_m = float(source_bounds["max_x"]) - float(source_bounds["min_x"])
        depth_m = float(source_bounds["max_y"]) - float(source_bounds["min_y"])
        return (
            {
                "source": "source_bounds",
                "min_x": float(source_bounds["min_x"]),
                "min_y": float(source_bounds["min_y"]),
                "max_x": float(source_bounds["max_x"]),
                "max_y": float(source_bounds["max_y"]),
                "width_m": round(abs(width_m), 3),
                "depth_m": round(abs(depth_m), 3),
            },
            0.95,
            [],
        )

    footprint = unit.get("footprint")
    if footprint:
        points = [tuple(point[:2]) for point in footprint if len(point) >= 2]
        xs = [float(point[0]) for point in points]
        ys = [float(point[1]) for point in points]
        if xs and ys:
            return (
                {
                    "source": "footprint",
                    "footprint": [[float(x), float(y)] for x, y in points],
                    "min_x": min(xs),
                    "min_y": min(ys),
                    "max_x": max(xs),
                    "max_y": max(ys),
                    "width_m": round(max(xs) - min(xs), 3),
                    "depth_m": round(max(ys) - min(ys), 3),
                },
                0.9,
                [],
            )

    story_count = max(len(stories), 1)
    width_m = DEFAULT_FALLBACK_WIDTH_M + min(story_count - 1, 4) * 3.0
    depth_m = DEFAULT_FALLBACK_DEPTH_M + min(story_count - 1, 4) * 2.0
    return (
        {
            "source": "fallback",
            "heuristic": "story-count envelope",
            "width_m": round(width_m, 3),
            "depth_m": round(depth_m, 3),
        },
        0.35,
        ["Geometry missing source bounds or footprint; using explainable story-count fallback."],
    )


def _story_sort_key(story: Mapping[str, Any]) -> tuple[int, str]:
    order = story.get("story_order")
    try:
        numeric_order = int(order)
    except (TypeError, ValueError):
        numeric_order = 0
    return numeric_order, str(story.get("story_key") or "")


def _combine_confidence(geometry_confidence: float, height_confidence: float) -> float:
    confidence = min(geometry_confidence, height_confidence)
    if geometry_confidence < 0.9 and height_confidence < 0.9:
        confidence -= 0.1
    return round(max(confidence, 0.1), 3)
