from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field
from typing import Any

DEFAULT_STORY_HEIGHT_M = 4.5
DEFAULT_FALLBACK_WIDTH_M = 18.0
DEFAULT_FALLBACK_DEPTH_M = 12.0
LOD200_GATES = ("plan_boundary", "story_order", "scale_or_coordinates")
LOD300_GATES = (
    "scale",
    "registered_grid",
    "dimensions",
    "cross_view_match",
    "stable_component_boundaries",
    "geometry_consistent",
)


@dataclass(frozen=True)
class ModelScopeEvidence:
    scope_key: str
    scope_label: str | None = None
    has_plan_boundary: bool = False
    has_story_order: bool = False
    has_scale: bool = False
    has_coordinates: bool = False
    has_registered_grid: bool = False
    has_dimensions: bool = False
    has_cross_view_match: bool = False
    has_stable_component_boundaries: bool = False
    geometry_consistent: bool = False
    reference_images: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class LodCapability:
    level: int
    enabled_modes: dict[str, bool]
    passed_gates: list[str]
    missing_evidence: list[str]
    confidence: float
    provenance: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_lod_capability(scope: ModelScopeEvidence) -> LodCapability:
    gates = (*LOD200_GATES, *LOD300_GATES)
    passed_gates = [gate for gate in gates if _gate_passed(scope, gate)]
    missing_evidence = [gate for gate in gates if gate not in passed_gates]
    lod200_ready = all(_gate_passed(scope, gate) for gate in LOD200_GATES)
    lod300_ready = lod200_ready and all(_gate_passed(scope, gate) for gate in LOD300_GATES)

    if lod300_ready:
        level = 300
    elif lod200_ready:
        level = 200
    else:
        level = 100

    return LodCapability(
        level=level,
        enabled_modes={
            "review_skeleton": True,
            "architectural_massing": level >= 200,
            "realistic_proxy": level >= 200,
        },
        passed_gates=passed_gates,
        missing_evidence=missing_evidence,
        confidence=_confidence_for(level, passed_gates),
        provenance=_build_provenance(scope),
    )


def aggregate_lod_modes(
    lod_capabilities: Mapping[str, LodCapability | Mapping[str, Any]]
) -> dict[str, dict[str, Any]]:
    capabilities = [_coerce_capability(capability) for capability in lod_capabilities.values()]
    has_lod200 = any(capability.level >= 200 for capability in capabilities)
    all_lod300 = bool(capabilities) and all(capability.level >= 300 for capability in capabilities)

    return {
        "review_skeleton": {
            "enabled": True,
            "label": "审图骨架",
        },
        "architectural_massing": {
            "enabled": has_lod200,
            "label": "建筑体量",
            "reason": None if has_lod200 else "需要平面范围、楼层顺序和比例或坐标证据。",
        },
        "realistic_proxy": {
            "enabled": has_lod200,
            "label": "实景近似" if all_lod300 else "实景近似（近似）",
            "reason": _realistic_proxy_reason(has_lod200, all_lod300),
        },
    }


def _coerce_capability(capability: LodCapability | Mapping[str, Any]) -> LodCapability:
    if isinstance(capability, LodCapability):
        return capability
    return LodCapability(
        level=int(capability.get("level") or 100),
        enabled_modes=dict(capability.get("enabled_modes") or {}),
        passed_gates=[str(gate) for gate in capability.get("passed_gates") or []],
        missing_evidence=[str(gate) for gate in capability.get("missing_evidence") or []],
        confidence=float(capability.get("confidence") or 0.0),
        provenance=dict(capability.get("provenance") or {}),
    )


def _realistic_proxy_reason(has_lod200: bool, all_lod300: bool) -> str | None:
    if not has_lod200:
        return "需要 LOD200 范围证据。"
    if not all_lod300:
        return "当前仅为近似代理；细部几何需全部 LOD300 证据通过。"
    return None


def _gate_passed(scope: ModelScopeEvidence, gate: str) -> bool:
    if gate == "plan_boundary":
        return scope.has_plan_boundary
    if gate == "story_order":
        return scope.has_story_order
    if gate == "scale_or_coordinates":
        return scope.has_scale or scope.has_coordinates
    if gate == "scale":
        return scope.has_scale
    if gate == "registered_grid":
        return scope.has_registered_grid
    if gate == "dimensions":
        return scope.has_dimensions
    if gate == "cross_view_match":
        return scope.has_cross_view_match
    if gate == "stable_component_boundaries":
        return scope.has_stable_component_boundaries
    if gate == "geometry_consistent":
        return scope.geometry_consistent
    raise ValueError(f"Unsupported LOD gate: {gate}")


def _confidence_for(level: int, passed_gates: list[str]) -> float:
    coverage = len(passed_gates) / float(len(LOD200_GATES) + len(LOD300_GATES))
    baseline = {100: 0.25, 200: 0.7, 300: 0.95}[level]
    return round(max(baseline, coverage), 3)


def _build_provenance(scope: ModelScopeEvidence) -> dict[str, Any]:
    provenance = {
        "scope_key": scope.scope_key,
        "scope_label": scope.scope_label or scope.scope_key,
        "gate_inputs": {
            "plan_boundary": scope.has_plan_boundary,
            "story_order": scope.has_story_order,
            "scale": scope.has_scale,
            "coordinates": scope.has_coordinates,
            "registered_grid": scope.has_registered_grid,
            "dimensions": scope.has_dimensions,
            "cross_view_match": scope.has_cross_view_match,
            "stable_component_boundaries": scope.has_stable_component_boundaries,
            "geometry_consistent": scope.geometry_consistent,
        },
    }
    if scope.reference_images:
        provenance["reference_images"] = {
            "count": len(scope.reference_images),
            "usages": sorted(
                str(reference.get("usage") or "visual_calibration_only")
                for reference in scope.reference_images
            ),
            "note": "Reference imagery is calibration-only and never satisfies geometry gates.",
        }
    return provenance


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
