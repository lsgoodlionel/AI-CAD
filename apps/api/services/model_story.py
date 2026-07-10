from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import re
from typing import Any

from services.floor_parser import parse_floor

MIN_STORY_SPACING_M = 2.8
DEFAULT_STORY_HEIGHT_M = 4.5
DEFAULT_BASEMENT_HEIGHT_M = 4.2
_DEFAULT_UNCLASSIFIED_STORY = ("UNZONED", "未分层", 0)

_DIRECTIONAL_UNIT_KEYS = {
    "南区": "south",
    "北区": "north",
    "东区": "east",
    "西区": "west",
    "中区": "central",
}
_UNIT_PATTERNS: tuple[tuple[re.Pattern[str], str | None], ...] = (
    (re.compile(r"(南区|北区|东区|西区|中区)"), "directional"),
    (re.compile(r"(\d+)\s*#\s*楼"), "tower_number"),
    (re.compile(r"([A-Za-z]\d?)\s*栋", re.IGNORECASE), "building_block"),
    (re.compile(r"([A-Za-z]\d?)\s*座", re.IGNORECASE), "building_block"),
    (re.compile(r"([A-Za-z]\d?)\s*塔楼", re.IGNORECASE), "building_block"),
)
_ELEVATION_RE = re.compile(r"(?:标高|EL|±)\s*([+-]?\d+(?:\.\d+)?)", re.IGNORECASE)
_FIRST_FLOOR_RE = re.compile(r"首层|首层平面|首层图")


@dataclass(frozen=True)
class BuildingUnitMatch:
    unit_key: str
    display_name: str
    confidence: float
    source: str
    candidate_sources: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class StoryCandidate:
    story_key: str | None
    display_name: str | None
    story_order: int | None
    elevation_m: float | None
    confidence: float
    source: str


@dataclass(frozen=True)
class StoryLevel:
    building_unit_key: str
    story_key: str
    display_name: str
    story_order: int
    elevation_m: float
    height_m: float
    source: str
    confidence: float
    display_building_name: str
    # B-04 层高 provenance（与 source/confidence 的「楼层识别」语义分离）：
    # height_source ∈ section|elevation|default；default 时 height_estimated=True + note。
    height_source: str = "default"
    height_confidence: float = 0.55
    height_estimated: bool = True
    height_note: str = ""


@dataclass(frozen=True)
class ModelQualityIssue:
    issue_type: str
    severity: str
    message: str
    drawing_id: str | None = None
    building_unit_key: str | None = None
    story_key: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StoryNormalizationResult:
    stories_by_building: dict[str, list[StoryLevel]]
    drawing_assignments: dict[str, dict[str, Any]]
    unclassified_drawings: list[dict[str, Any]]
    issues: list[ModelQualityIssue]
    building_units: list[dict[str, Any]]


def _text_fragments(drawing: dict[str, Any]) -> list[tuple[str, str]]:
    fragments: list[tuple[str, str]] = []
    for key in ("title", "drawing_no", "file_key", "folder_path", "ocr_text"):
        value = str(drawing.get(key) or "").strip()
        if value:
            fragments.append((key, value))
    ocr_lines = drawing.get("ocr_lines") or []
    if isinstance(ocr_lines, list):
        for item in ocr_lines:
            value = str(item or "").strip()
            if value:
                fragments.append(("ocr_text", value))
    return fragments


def _default_candidate_sources(source: str, value: str, confidence: float) -> list[dict[str, Any]]:
    return [{"source": source, "value": value, "confidence": round(confidence, 4)}]


def _slugify_label(label: str) -> str:
    lowered = label.strip().lower()
    lowered = re.sub(r"[^a-z0-9_-]+", "-", lowered)
    lowered = re.sub(r"-{2,}", "-", lowered).strip("-")
    return lowered


def _hashed_unit_key(label: str) -> str:
    digest = hashlib.md5(label.encode("utf-8")).hexdigest()[:10]
    return f"unit_{digest}"


def _unit_from_match(label: str, kind: str | None) -> tuple[str, str]:
    if kind == "directional":
        return _DIRECTIONAL_UNIT_KEYS[label], label
    if kind == "tower_number":
        digits = re.sub(r"\D", "", label)
        return f"building_{digits}", f"{digits}#楼"
    if kind == "building_block":
        block = re.sub(r"[^A-Za-z0-9]", "", label).lower()
        return f"building_{block}", label.upper()
    slug = _slugify_label(label)
    return (slug or _hashed_unit_key(label), label)


def _match_building_unit(text: str) -> tuple[str, str] | None:
    for pattern, kind in _UNIT_PATTERNS:
        match = pattern.search(text)
        if match is None:
            continue
        label = match.group(0).strip()
        return _unit_from_match(label, kind)
    return None


def _story_elevation(annotation: dict[str, Any], drawing: dict[str, Any]) -> float | None:
    if annotation.get("elevation_m") is not None:
        return float(annotation["elevation_m"])
    for _source, text in _text_fragments(drawing):
        match = _ELEVATION_RE.search(text)
        if match is not None:
            return float(match.group(1))
    return None


def _default_story_height(story_order: int) -> float:
    return DEFAULT_BASEMENT_HEIGHT_M if story_order < 0 else DEFAULT_STORY_HEIGHT_M


def _resolve_story_height(
    story_order: int,
    override: dict[str, Any] | None,
) -> tuple[float, str, float, bool, str]:
    """层高解析优先级链：section/elevation 实测 > default 兜底。

    返回 (height_m, height_source, height_confidence, height_estimated, height_note)。
    有实测覆盖 → 非估算、note 空；无 → 默认层高 + 显式 estimated + 低置信 + note。
    """
    if override is not None:
        measured = round(float(override.get("height_m") or 0.0), 3)
        if measured > 0:
            return (
                measured,
                str(override.get("source") or "section"),
                round(float(override.get("confidence") or 0.0), 4),
                False,
                "",
            )
    height = _default_story_height(story_order)
    note = f"默认层高 {height}m 估算（缺剖面标高证据）"
    return height, "default", 0.55, True, note


def _default_story_elevation(story_order: int, highest_story_order: int) -> float:
    if story_order < 0:
        return round(story_order * DEFAULT_BASEMENT_HEIGHT_M, 3)
    if story_order == 0:
        return 0.0
    if story_order >= 900:
        return round(max(highest_story_order - 1, 0) * DEFAULT_STORY_HEIGHT_M + DEFAULT_STORY_HEIGHT_M, 3)
    return round((story_order - 1) * DEFAULT_STORY_HEIGHT_M, 3)


def detect_building_unit(
    drawing: dict[str, Any],
    annotation: dict[str, Any] | None = None,
) -> BuildingUnitMatch:
    annotation = annotation or {}
    unit_key = str(annotation.get("building_unit_key") or "").strip()
    display_name = str(annotation.get("building_unit_display_name") or "").strip()
    if unit_key and display_name:
        sources = annotation.get("candidate_sources") or _default_candidate_sources("manual", display_name, 1.0)
        return BuildingUnitMatch(
            unit_key=unit_key,
            display_name=display_name,
            confidence=float(annotation.get("confidence") or 1.0),
            source="manual",
            candidate_sources=list(sources),
        )

    matches: dict[str, dict[str, Any]] = {}
    for source, text in _text_fragments(drawing):
        matched = _match_building_unit(text)
        if matched is None:
            continue
        candidate_key, candidate_label = matched
        confidence = 0.86 if source in {"title", "drawing_no"} else 0.72
        entry = matches.setdefault(
            candidate_key,
            {
                "unit_key": candidate_key,
                "display_name": candidate_label,
                "confidence": confidence,
                "source": source,
                "candidate_sources": [],
            },
        )
        entry["confidence"] = max(entry["confidence"], confidence)
        entry["candidate_sources"].append(
            {"source": source, "value": text, "confidence": round(confidence, 4)}
        )

    if matches:
        best = max(
            matches.values(),
            key=lambda item: (item["confidence"], len(item["candidate_sources"]), item["unit_key"]),
        )
        return BuildingUnitMatch(**best)

    return BuildingUnitMatch(
        unit_key="main",
        display_name="主体",
        confidence=0.35,
        source="default",
        candidate_sources=_default_candidate_sources("default", "main", 0.35),
    )


def extract_story_candidate(
    drawing: dict[str, Any],
    annotation: dict[str, Any] | None = None,
) -> StoryCandidate:
    annotation = annotation or {}
    story_key = str(annotation.get("story_key") or "").strip()
    display_name = str(annotation.get("story_display_name") or "").strip()
    if story_key and display_name:
        story_order = annotation.get("story_order")
        if story_order is None:
            parsed = parse_floor(display_name) or parse_floor(story_key)
            story_order = parsed[2] if parsed is not None else 0
        return StoryCandidate(
            story_key=story_key,
            display_name=display_name,
            story_order=int(story_order),
            elevation_m=_story_elevation(annotation, drawing),
            confidence=float(annotation.get("confidence") or 1.0),
            source="manual",
        )

    for source, text in _text_fragments(drawing):
        if _FIRST_FLOOR_RE.search(text):
            return StoryCandidate(
                story_key="F1",
                display_name="1层",
                story_order=1,
                elevation_m=_story_elevation(annotation, drawing),
                confidence=0.76 if source in {"title", "drawing_no"} else 0.58,
                source=source,
            )
        parsed = parse_floor(text)
        if parsed is None:
            continue
        story_key, display_name, story_order = parsed
        return StoryCandidate(
            story_key=story_key,
            display_name=display_name,
            story_order=story_order,
            elevation_m=_story_elevation(annotation, drawing),
            confidence=0.82 if source in {"title", "drawing_no"} else 0.64,
            source=source,
        )

    return StoryCandidate(
        story_key=None,
        display_name=None,
        story_order=None,
        elevation_m=_story_elevation(annotation, drawing),
        confidence=0.0,
        source="unclassified",
    )


def _serialize_candidate_sources(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return []
    return list(value) if isinstance(value, list) else []


def normalize_story_table(
    drawings: list[dict[str, Any]],
    annotations: dict[str, dict[str, Any]] | None = None,
    z_overrides: dict[tuple[str, str], dict[str, Any]] | None = None,
) -> StoryNormalizationResult:
    """归一化楼层表。

    ``z_overrides``：可选跨视图 z 恢复覆盖，键 (building_unit_key, story_key)，
    值含 height_m / elevation_bottom_m / source / confidence。存在时用实测层高与标高，
    否则维持默认——保持向后兼容（不传时行为不变）。
    """
    annotations = annotations or {}
    grouped: dict[str, dict[str, dict[str, Any]]] = {}
    assignments: dict[str, dict[str, Any]] = {}
    building_units: dict[str, dict[str, Any]] = {}
    unclassified_drawings: list[dict[str, Any]] = []
    issues: list[ModelQualityIssue] = []

    for drawing in drawings:
        drawing_id = str(drawing["id"])
        annotation = dict(annotations.get(drawing_id) or {})
        if annotation.get("candidate_sources") is not None:
            annotation["candidate_sources"] = _serialize_candidate_sources(annotation["candidate_sources"])
        unit = detect_building_unit(drawing, annotation)
        story = extract_story_candidate(drawing, annotation)

        building_units.setdefault(
            unit.unit_key,
            {
                "unit_key": unit.unit_key,
                "display_name": unit.display_name,
                "confidence": unit.confidence,
                "candidate_sources": list(unit.candidate_sources),
                "source": unit.source,
            },
        )
        existing_unit = building_units[unit.unit_key]
        existing_unit["confidence"] = max(existing_unit["confidence"], unit.confidence)
        for candidate in unit.candidate_sources:
            if candidate not in existing_unit["candidate_sources"]:
                existing_unit["candidate_sources"].append(candidate)
        if unit.source == "manual":
            existing_unit["source"] = "manual"
            existing_unit["display_name"] = unit.display_name

        assignment = {
            "drawing_id": drawing_id,
            "building_unit_key": unit.unit_key,
            "building_unit_display_name": unit.display_name,
            "building_unit_confidence": unit.confidence,
            "candidate_sources": list(unit.candidate_sources),
            "story_key": story.story_key,
            "story_display_name": story.display_name,
            "story_order": story.story_order,
            "story_confidence": story.confidence,
            "story_source": story.source,
            "assignment_source": "manual" if story.source == "manual" or unit.source == "manual" else "detected",
            "detected_elevation_m": story.elevation_m,
            "normalized_elevation_m": None,
        }
        assignments[drawing_id] = assignment

        if story.story_key is None or story.story_order is None or story.display_name is None:
            assignment["story_key"] = _DEFAULT_UNCLASSIFIED_STORY[0]
            assignment["story_display_name"] = _DEFAULT_UNCLASSIFIED_STORY[1]
            assignment["story_order"] = _DEFAULT_UNCLASSIFIED_STORY[2]
            unclassified_drawings.append(
                {
                    "drawing_id": drawing_id,
                    "drawing_no": str(drawing.get("drawing_no") or ""),
                    "title": str(drawing.get("title") or ""),
                    "building_unit_key": unit.unit_key,
                    "reason": "story_unclassified",
                }
            )
            issues.append(
                ModelQualityIssue(
                    issue_type="story_unclassified",
                    severity="warning",
                    message="图纸未识别出楼层，已进入待人工标注队列",
                    drawing_id=drawing_id,
                    building_unit_key=unit.unit_key,
                )
            )
            continue

        unit_group = grouped.setdefault(unit.unit_key, {})
        story_group = unit_group.setdefault(
            story.story_key,
            {
                "story_key": story.story_key,
                "display_name": story.display_name,
                "story_order": story.story_order,
                "elevations": [],
                "confidence": story.confidence,
                "source": story.source,
                "building_display_name": unit.display_name,
            },
        )
        if story.elevation_m is not None:
            story_group["elevations"].append(float(story.elevation_m))
        story_group["confidence"] = max(story_group["confidence"], story.confidence)
        if story.source == "manual":
            story_group["source"] = "manual"
        if unit.source == "manual":
            story_group["building_display_name"] = unit.display_name

    stories_by_building: dict[str, list[StoryLevel]] = {}
    normalized_elevations: dict[tuple[str, str], float] = {}

    for unit_key, stories in grouped.items():
        highest_order = max((entry["story_order"] for entry in stories.values()), default=1)
        ordered = sorted(stories.values(), key=lambda item: (item["story_order"], item["story_key"]))
        previous: float | None = None
        levels: list[StoryLevel] = []
        for entry in ordered:
            story_order = int(entry["story_order"])
            override = z_overrides.get((unit_key, entry["story_key"])) if z_overrides else None
            height_m, h_source, h_conf, h_estimated, h_note = _resolve_story_height(
                story_order, override
            )
            override_elev = override.get("elevation_bottom_m") if override else None
            if override_elev is not None:
                # 实测标高：直接采用，不走「过近默认校正」（校正仅对估算标高兜底）
                chosen = round(float(override_elev), 3)
            else:
                explicit = sorted(set(round(value, 3) for value in entry["elevations"]))
                chosen = explicit[0] if explicit else _default_story_elevation(story_order, highest_order)
                if previous is not None and chosen - previous < MIN_STORY_SPACING_M:
                    detected_spacing = round(chosen - previous, 3)
                    issues.append(
                        ModelQualityIssue(
                            issue_type="story_spacing_too_small",
                            severity="warning",
                            message="相邻楼层标高过近，已按默认层高校正",
                            building_unit_key=unit_key,
                            story_key=entry["story_key"],
                            payload={
                                "detected_spacing_m": detected_spacing,
                                "min_spacing_m": MIN_STORY_SPACING_M,
                                "previous_elevation_m": previous,
                                "detected_elevation_m": chosen,
                            },
                        )
                    )
                    chosen = round(previous + DEFAULT_STORY_HEIGHT_M, 3)
            normalized_elevations[(unit_key, entry["story_key"])] = chosen
            previous = chosen
            levels.append(
                StoryLevel(
                    building_unit_key=unit_key,
                    story_key=entry["story_key"],
                    display_name=entry["display_name"],
                    story_order=story_order,
                    elevation_m=chosen,
                    height_m=height_m,
                    source=entry["source"],
                    confidence=round(float(entry["confidence"]), 4),
                    display_building_name=entry["building_display_name"],
                    height_source=h_source,
                    height_confidence=h_conf,
                    height_estimated=h_estimated,
                    height_note=h_note,
                )
            )
        stories_by_building[unit_key] = levels

    for assignment in assignments.values():
        unit_key = assignment["building_unit_key"]
        story_key = assignment["story_key"]
        if (unit_key, story_key) in normalized_elevations:
            assignment["normalized_elevation_m"] = normalized_elevations[(unit_key, story_key)]

    ordered_units = sorted(building_units.values(), key=lambda item: (item["unit_key"] != "main", item["unit_key"]))
    return StoryNormalizationResult(
        stories_by_building=stories_by_building,
        drawing_assignments=assignments,
        unclassified_drawings=unclassified_drawings,
        issues=issues,
        building_units=ordered_units,
    )
