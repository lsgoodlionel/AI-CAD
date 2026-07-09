from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping


EXPLICIT_BUILDING_RE = re.compile(
    r"(?:\d+\s*#\s*楼|[A-Za-z]\d?\s*(?:栋|座|塔楼)|[^，。；]{1,20}单体)"
)
SUB_ZONE_RE = re.compile(r"(?<!\d)([A-Za-z]\d?|D\d+)\s*区")
SUB_ZONE_LIST_RE = re.compile(r"([A-Za-z]\d?(?:、[A-Za-z]\d?)+)\s*区")
FUNCTIONAL_SPACE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"([\u4e00-\u9fffA-Za-z0-9]{1,16}车间)"),
    re.compile(r"([\u4e00-\u9fffA-Za-z0-9]{1,16}车库)"),
    re.compile(r"([\u4e00-\u9fffA-Za-z0-9]{1,16}(?:厅|室|房|舞台|机房|厂房|库房))"),
)
CONSTRUCTION_CONTEXT_RE = re.compile(r"(?:围护|基坑|施工|工区|标段|联通道|连通道)")
CONSTRUCTION_ZONE_RE = re.compile(
    r"(\d+(?:-\d+)?区|[一二三四五六七八九十\d]+工区|[一二三四五六七八九十\d]+标段)"
)
DIRECTIONAL_GROUP_RE = re.compile(r"([东南西北中][区侧段岸区])")
BRIDGE_SEGMENT_RE = re.compile(r"(\d+号桥)")


@dataclass(frozen=True)
class SemanticCandidate:
    node_type: str
    label: str
    normalized_key: str
    confidence: float
    source: str
    source_value: str
    context: dict[str, Any]


def extract_semantic_candidates(drawing: Mapping[str, Any]) -> list[SemanticCandidate]:
    seen: set[tuple[str, str]] = set()
    candidates: list[SemanticCandidate] = []

    for source, source_value in _iter_sources(drawing):
        if not source_value:
            continue

        construction_context = bool(CONSTRUCTION_CONTEXT_RE.search(source_value))

        candidates.extend(
            _dedupe_candidates(
                seen,
                _extract_buildings(source, source_value),
            )
        )
        candidates.extend(
            _dedupe_candidates(
                seen,
                _extract_sub_zones(source, source_value, construction_context),
            )
        )
        candidates.extend(
            _dedupe_candidates(
                seen,
                _extract_directional_groups(source, source_value, construction_context),
            )
        )
        candidates.extend(
            _dedupe_candidates(
                seen,
                _extract_construction_zones(source, source_value, construction_context),
            )
        )
        candidates.extend(
            _dedupe_candidates(
                seen,
                _extract_functional_spaces(source, source_value),
            )
        )
        candidates.extend(
            _dedupe_candidates(
                seen,
                _extract_infrastructure_segments(source, source_value),
            )
        )

    return candidates


def _iter_sources(drawing: Mapping[str, Any]) -> Iterable[tuple[str, str]]:
    for key in ("title", "folder_path", "filename", "drawing_no"):
        value = drawing.get(key)
        if isinstance(value, str):
            yield key, value


def _extract_buildings(source: str, source_value: str) -> Iterable[SemanticCandidate]:
    for match in EXPLICIT_BUILDING_RE.finditer(source_value):
        label = re.sub(r"\s+", "", match.group(0))
        yield _make_candidate(
            node_type="building_unit",
            label=label,
            confidence=0.95,
            source=source,
            source_value=source_value,
            match_reason="explicit_building",
            span=match.span(),
        )


def _extract_sub_zones(
    source: str,
    source_value: str,
    construction_context: bool,
) -> Iterable[SemanticCandidate]:
    if construction_context:
        return

    for match in SUB_ZONE_LIST_RE.finditer(source_value):
        for part in match.group(1).split("、"):
            label = f"{part}区"
            yield _make_candidate(
                node_type="sub_zone",
                label=label,
                confidence=0.84,
                source=source,
                source_value=source_value,
                match_reason="sub_zone_list",
                span=match.span(),
            )

    for match in SUB_ZONE_RE.finditer(source_value):
        label = f"{match.group(1)}区"
        yield _make_candidate(
            node_type="sub_zone",
            label=label,
            confidence=0.82,
            source=source,
            source_value=source_value,
            match_reason="sub_zone",
            span=match.span(),
        )


def _extract_directional_groups(
    source: str,
    source_value: str,
    construction_context: bool,
) -> Iterable[SemanticCandidate]:
    for match in DIRECTIONAL_GROUP_RE.finditer(source_value):
        yield _make_candidate(
            node_type="directional_group",
            label=match.group(1),
            confidence=0.58 if construction_context else 0.68,
            source=source,
            source_value=source_value,
            match_reason="directional_group",
            span=match.span(),
        )


def _extract_construction_zones(
    source: str,
    source_value: str,
    construction_context: bool,
) -> Iterable[SemanticCandidate]:
    if not construction_context:
        return

    for match in CONSTRUCTION_ZONE_RE.finditer(source_value):
        yield _make_candidate(
            node_type="construction_zone",
            label=match.group(1),
            confidence=0.9,
            source=source,
            source_value=source_value,
            match_reason="construction_zone",
            span=match.span(),
        )


def _extract_functional_spaces(source: str, source_value: str) -> Iterable[SemanticCandidate]:
    for pattern in FUNCTIONAL_SPACE_PATTERNS:
        for match in pattern.finditer(source_value):
            yield _make_candidate(
                node_type="functional_space",
                label=match.group(1),
                confidence=0.8,
                source=source,
                source_value=source_value,
                match_reason="functional_space",
                span=match.span(),
            )


def _extract_infrastructure_segments(
    source: str,
    source_value: str,
) -> Iterable[SemanticCandidate]:
    for match in BRIDGE_SEGMENT_RE.finditer(source_value):
        yield _make_candidate(
            node_type="infrastructure_segment",
            label=match.group(1),
            confidence=0.86,
            source=source,
            source_value=source_value,
            match_reason="bridge_segment",
            span=match.span(),
        )


def _make_candidate(
    *,
    node_type: str,
    label: str,
    confidence: float,
    source: str,
    source_value: str,
    match_reason: str,
    span: tuple[int, int],
) -> SemanticCandidate:
    return SemanticCandidate(
        node_type=node_type,
        label=label,
        normalized_key=_normalize_key(label),
        confidence=confidence,
        source=source,
        source_value=source_value,
        context={
            "match_reason": match_reason,
            "span": span,
        },
    )


def _normalize_key(label: str) -> str:
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", label).lower()


def _dedupe_candidates(
    seen: set[tuple[str, str]],
    candidates: Iterable[SemanticCandidate] | None,
) -> list[SemanticCandidate]:
    if not candidates:
        return []

    result: list[SemanticCandidate] = []
    for candidate in candidates:
        key = (candidate.node_type, candidate.normalized_key)
        if key in seen:
            continue
        seen.add(key)
        result.append(candidate)
    return result
