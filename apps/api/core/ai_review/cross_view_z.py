"""跨视图 z 恢复统一入口（B-10）。

按 CROSS_VIEW_Z_RECOVERY_DESIGN §4.1，与 cross_drawing.analyze_batch（纯平面 SQL 聚合）
并列——不污染其单一职责，另立本模块承载「z 恢复流水线」：
    B-01 判图种 → B-08 轴网 → B-02 剖面标高 → B-06 立面洞口 → B-09 三视图配准 → B-07 截面表。

`recover_z_from_geometries` 为纯函数（吃已抽取的几何，可确定性测试）；
`recover_z` 为异步壳（拉图纸 + 取字节 + 提几何后委托纯函数）。
纯平面批次向后兼容：无剖面/立面时不崩、不出强证据。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from core.model3d.elevation_opening_extractor import extract_elevation_openings
from core.model3d.grid_anchor_extractor import extract_grid_anchors
from core.model3d.section_level_extractor import extract_section_levels
from core.model3d.types import DrawingGeometry
from services.cross_view_registration import (
    ElevationView,
    SectionView,
    ZRegistration,
    register_views,
)
from services.drawing_view_classifier import classify_view_type
from services.model_component_sections import Section, build_component_sections

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ZRecoveryResult:
    view_classification: dict[str, str] = field(default_factory=dict)
    registration: ZRegistration = field(default_factory=ZRegistration)
    component_sections: dict[str, Section] = field(default_factory=dict)
    levels: tuple[dict, ...] = ()
    matched: bool = False


def recover_z_from_geometries(
    items: list[tuple[dict, DrawingGeometry]],
) -> ZRecoveryResult:
    """纯函数：由 (图纸, 几何) 列表跑完整 z 恢复流水线。"""
    view_classification: dict[str, str] = {}
    plan_grid = None
    sections: list[SectionView] = []
    elevations: list[ElevationView] = []
    section_texts: list[str] = []

    for drawing, geom in items:
        drawing_id = str(drawing.get("id") or "")
        view_type = classify_view_type(drawing).view_type
        view_classification[drawing_id] = view_type

        if view_type == "plan" and plan_grid is None:
            plan_grid = extract_grid_anchors(geom)
        elif view_type == "section":
            sections.append(
                SectionView(
                    drawing_id=drawing_id,
                    grid=extract_grid_anchors(geom),
                    levels=extract_section_levels(geom),
                )
            )
            section_texts.extend(_geom_texts(geom))
        elif view_type == "elevation":
            elevations.append(
                ElevationView(
                    drawing_id=drawing_id,
                    grid=extract_grid_anchors(geom),
                    openings=extract_elevation_openings(geom),
                )
            )
        elif view_type == "detail":
            section_texts.extend(_geom_texts(geom))

    registration = register_views(plan_grid, sections, elevations)
    component_sections = build_component_sections(section_texts)
    return ZRecoveryResult(
        view_classification=view_classification,
        registration=registration,
        component_sections=component_sections,
        levels=registration.levels,
        matched=registration.matched,
    )


async def recover_z(
    db,
    project_id: str,
    file_getter: Callable[[str], bytes],
    *,
    drawings: list[dict] | None = None,
) -> ZRecoveryResult:
    """异步入口：拉图纸 → 取字节提几何 → 委托纯函数。任何单图失败跳过。"""
    rows = drawings if drawings is not None else await _fetch_drawings(db, project_id)
    items: list[tuple[dict, DrawingGeometry]] = []
    for drawing in rows:
        geom = _extract_geometry(drawing, file_getter)
        if geom is not None:
            items.append((drawing, geom))
    return recover_z_from_geometries(items)


async def _fetch_drawings(db, project_id: str) -> list[dict]:
    rows = await db.fetch_all(
        "SELECT id, title, drawing_no, file_key, discipline "
        "FROM drawings WHERE project_id = :project_id",
        {"project_id": project_id},
    )
    return [dict(row) for row in rows or []]


def _extract_geometry(drawing: dict, file_getter: Callable[[str], bytes]) -> DrawingGeometry | None:
    file_key = drawing.get("file_key") or ""
    ext = file_key.rsplit(".", 1)[-1].lower() if "." in file_key else ""
    if not file_key or ext not in ("pdf", "dxf", "dwg"):
        return None
    try:
        from core.model3d import geometry_extractor

        data = file_getter(file_key)
        if ext == "pdf":
            return geometry_extractor.extract_pdf_geometry(data)
        return geometry_extractor.extract_dxf_geometry(data)
    except Exception as exc:  # noqa: BLE001 — 单图几何提取失败跳过
        logger.warning("[cross_view_z] 几何提取跳过 %s: %s", drawing.get("id"), exc)
        return None


def _geom_texts(geom: DrawingGeometry) -> list[str]:
    return [t[2] for t in geom.texts if len(t) >= 3 and isinstance(t[2], str)]
