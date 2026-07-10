"""构件截面表（B-07）：剖面/详图标注 → 构件真实截面，替换硬编码默认。

从剖面/详图文本抽取「宽×高 / 板厚 / 墙厚 / 柱截面 / 管径」→ 按构件类型取代表值（中位数），
缺证据回落默认并显式 estimated（provenance 贯穿，绝不把默认伪装成实测）。
同类多截面差异化（不同跨梁高不同）留 Phase C，MVP 按类型取代表值。

含持久化仓储（migration 020 model_component_sections，Repository Pattern）。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

# 硬编码默认（与 element_recognizer 一致：梁 depth 0.6 / 板厚 0.12 / 管径 0.1）
DEFAULT_BEAM_DEPTH_M = 0.6
DEFAULT_BEAM_WIDTH_M = 0.3
DEFAULT_COLUMN_SIZE_M = 0.5
DEFAULT_SLAB_THICKNESS_M = 0.12
DEFAULT_WALL_THICKNESS_M = 0.2
DEFAULT_PIPE_DIAMETER_M = 0.1

COMPONENT_TYPES = ("beam", "column", "slab", "wall", "pipe")

# 标注正则（mm）
_BH_RE = re.compile(r"(\d{2,4})\s*[×xX\*]\s*(\d{2,4})")
_SLAB_RE = re.compile(r"板厚\s*[:：=]?\s*(\d{2,4})")
_THICK_RE = re.compile(r"厚\s*[:：=]?\s*(\d{2,4})")
_WALL_RE = re.compile(r"墙厚?\s*[:：=]?\s*(\d{2,4})")
_PIPE_RE = re.compile(r"(?:DN|De|Φ|φ|Ø|⌀)\s*(\d{2,4})", re.I)

# 合理范围（mm）
_SECTION_MM_RANGE = (50, 3000)
_SLAB_MM_RANGE = (60, 600)
_WALL_MM_RANGE = (60, 800)
_PIPE_MM_RANGE = (15, 2000)

_MEASURED_CONF = 0.85
_DEFAULT_CONF = 0.3


@dataclass(frozen=True)
class Section:
    """单构件类型的截面（实测或默认）。"""
    component_type: str
    h_m: float | None = None
    w_m: float | None = None
    thickness_m: float | None = None
    diameter_m: float | None = None
    source: str = "default"          # section | detail | default
    confidence: float = _DEFAULT_CONF
    estimated: bool = True
    evidence: dict = field(default_factory=dict)


def build_component_sections(
    texts: list[str], *, source: str = "section"
) -> dict[str, Section]:
    """从标注文本构建构件截面表。source 标注实测来源（section/detail）。"""
    bh_beams: list[tuple[float, float]] = []
    bh_columns: list[tuple[float, float]] = []
    slabs: list[float] = []
    walls: list[float] = []
    pipes: list[float] = []

    for text in texts or []:
        if not isinstance(text, str):
            continue
        is_column = "柱" in text
        for match in _BH_RE.finditer(text):
            pair = _mm_pair(match.group(1), match.group(2), _SECTION_MM_RANGE)
            if pair is not None:
                (bh_columns if is_column else bh_beams).append(pair)
        walls.extend(_mm_values(_WALL_RE, text, _WALL_MM_RANGE))
        slabs.extend(_slab_thicknesses(text))
        pipes.extend(_mm_values(_PIPE_RE, text, _PIPE_MM_RANGE))

    return {
        "beam": _bh_section("beam", bh_beams, DEFAULT_BEAM_WIDTH_M, DEFAULT_BEAM_DEPTH_M, source),
        "column": _bh_section("column", bh_columns, DEFAULT_COLUMN_SIZE_M, DEFAULT_COLUMN_SIZE_M, source),
        "slab": _dim_section("slab", "thickness_m", slabs, DEFAULT_SLAB_THICKNESS_M, source),
        "wall": _dim_section("wall", "thickness_m", walls, DEFAULT_WALL_THICKNESS_M, source),
        "pipe": _dim_section("pipe", "diameter_m", pipes, DEFAULT_PIPE_DIAMETER_M, source),
    }


def apply_component_sections(floors: list[dict], sections: dict[str, Section]) -> None:
    """把实测截面注入楼层构件（覆盖 depth/thickness/dia），默认截面不动（无回归）。"""
    beam = sections.get("beam")
    slab = sections.get("slab")
    pipe = sections.get("pipe")
    for floor in floors:
        elements = floor.get("elements") or {}
        if beam and not beam.estimated and beam.h_m:
            _override(elements.get("beams"), "depth", beam.h_m)
        if slab and not slab.estimated and slab.thickness_m:
            _override(elements.get("slabs"), "thickness", slab.thickness_m)
        if pipe and not pipe.estimated and pipe.diameter_m:
            _override(elements.get("pipes"), "dia", pipe.diameter_m)


def _override(items: list | None, key: str, value: float) -> None:
    for item in items or []:
        if isinstance(item, dict):
            item[key] = round(value, 3)
            item["z_source"] = "measured"


# ── 解析辅助 ────────────────────────────────────────────────────

def _mm_pair(a: str, b: str, rng: tuple[int, int]) -> tuple[float, float] | None:
    wa, wb = int(a), int(b)
    if rng[0] <= wa <= rng[1] and rng[0] <= wb <= rng[1]:
        return wa / 1000.0, wb / 1000.0
    return None


def _mm_values(pattern: re.Pattern[str], text: str, rng: tuple[int, int]) -> list[float]:
    values: list[float] = []
    for match in pattern.finditer(text):
        value = int(match.group(1))
        if rng[0] <= value <= rng[1]:
            values.append(value / 1000.0)
    return values


def _slab_thicknesses(text: str) -> list[float]:
    explicit = _mm_values(_SLAB_RE, text, _SLAB_MM_RANGE)
    if explicit:
        return explicit
    # 「厚120」兜底，排除墙厚（避免与 wall 重复计数）
    if "墙" in text:
        return []
    return _mm_values(_THICK_RE, text, _SLAB_MM_RANGE)


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return round((ordered[mid - 1] + ordered[mid]) / 2.0, 4)


def _bh_section(
    component_type: str,
    pairs: list[tuple[float, float]],
    default_w: float,
    default_h: float,
    source: str,
) -> Section:
    if pairs:
        return Section(
            component_type=component_type,
            w_m=round(_median([p[0] for p in pairs]), 3),
            h_m=round(_median([p[1] for p in pairs]), 3),
            source=source,
            confidence=_MEASURED_CONF,
            estimated=False,
            evidence={"samples": len(pairs)},
        )
    return Section(
        component_type=component_type,
        w_m=default_w,
        h_m=default_h,
        source="default",
        confidence=_DEFAULT_CONF,
        estimated=True,
        evidence={"note": "缺截面标注，回落默认"},
    )


def _dim_section(
    component_type: str,
    dim_key: str,
    values: list[float],
    default_value: float,
    source: str,
) -> Section:
    if values:
        return Section(
            component_type=component_type,
            source=source,
            confidence=_MEASURED_CONF,
            estimated=False,
            evidence={"samples": len(values)},
            **{dim_key: round(_median(values), 3)},
        )
    return Section(
        component_type=component_type,
        source="default",
        confidence=_DEFAULT_CONF,
        estimated=True,
        evidence={"note": "缺截面标注，回落默认"},
        **{dim_key: default_value},
    )


# ── 持久化仓储 ─────────────────────────────────────────────────

_UPSERT_SQL = """
INSERT INTO model_component_sections (
    project_id, scope_key, component_type,
    h_m, w_m, thickness_m, diameter_m, source, confidence, estimated, evidence_ref
)
VALUES (
    :project_id, :scope_key, :component_type,
    :h_m, :w_m, :thickness_m, :diameter_m, :source, :confidence, :estimated,
    CAST(:evidence_ref AS jsonb)
)
ON CONFLICT (project_id, scope_key, component_type)
DO UPDATE SET
    h_m = EXCLUDED.h_m,
    w_m = EXCLUDED.w_m,
    thickness_m = EXCLUDED.thickness_m,
    diameter_m = EXCLUDED.diameter_m,
    source = EXCLUDED.source,
    confidence = EXCLUDED.confidence,
    estimated = EXCLUDED.estimated,
    evidence_ref = EXCLUDED.evidence_ref,
    updated_at = now()
"""

_SELECT_SQL = """
SELECT component_type, h_m, w_m, thickness_m, diameter_m,
       source, confidence, estimated, evidence_ref
FROM model_component_sections
WHERE project_id = :project_id AND scope_key = :scope_key
"""


async def upsert_component_sections(
    db, project_id: str, scope_key: str, sections: dict[str, Section]
) -> int:
    written = 0
    for section in sections.values():
        await db.execute(_UPSERT_SQL, _section_params(project_id, scope_key, section))
        written += 1
    return written


async def fetch_component_sections(db, project_id: str, scope_key: str) -> dict[str, Section]:
    rows = await db.fetch_all(_SELECT_SQL, {"project_id": project_id, "scope_key": scope_key})
    result: dict[str, Section] = {}
    for row in rows or []:
        record = dict(row)
        result[str(record["component_type"])] = Section(
            component_type=str(record["component_type"]),
            h_m=_opt_float(record.get("h_m")),
            w_m=_opt_float(record.get("w_m")),
            thickness_m=_opt_float(record.get("thickness_m")),
            diameter_m=_opt_float(record.get("diameter_m")),
            source=str(record.get("source") or "default"),
            confidence=float(record.get("confidence") or 0.0),
            estimated=bool(record.get("estimated")),
            evidence=_parse_evidence(record.get("evidence_ref")),
        )
    return result


def _section_params(project_id: str, scope_key: str, section: Section) -> dict[str, Any]:
    return {
        "project_id": project_id,
        "scope_key": scope_key,
        "component_type": section.component_type,
        "h_m": section.h_m,
        "w_m": section.w_m,
        "thickness_m": section.thickness_m,
        "diameter_m": section.diameter_m,
        "source": section.source,
        "confidence": round(float(section.confidence), 4),
        "estimated": bool(section.estimated),
        "evidence_ref": json.dumps(section.evidence or {}, ensure_ascii=False),
    }


def _opt_float(value: Any) -> float | None:
    return None if value is None else float(value)


def _parse_evidence(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return {}
    return dict(value) if isinstance(value, dict) else {}
