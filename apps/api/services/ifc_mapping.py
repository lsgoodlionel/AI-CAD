"""scene → IFC 映射器（Phase A · A-02）。

把 ``build_scene`` 产出的 scene（``buildings[] → floors[]``，每层带
``elevation_m`` 与 ``FloorElements`` 构件字典）映射为 ``model_ifc_builder`` 的
不可变输入契约（``IfcProjectInput / IfcBuildingInput / IfcStoryInput``），
调用 ``build_ifc_from_model`` 产出合规 IFC4 字节流。

单体分组直接复用 ``build_scene`` 已写入的 ``scene["buildings"]``
（由 ``model_elements.group_buildings`` 产出），**不重复分组**。

Phase A 边界：楼层层高恒为默认常量、缺失标高按层序估算——二者均在
每个 ``IfcBuildingStorey`` 挂 ``Pset_ModelProvenance`` 显式标注
``IsEstimated=true``（真实标高恢复见 Phase B，此处只标记不实现）。

依赖 ``ifcopenshell``（经 ``model_ifc_builder`` 间接引入）。未安装时导入即抛
ImportError，与建模器一致，属预期行为。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import ifcopenshell
import ifcopenshell.guid

from services.model_ifc_builder import (
    IfcBuildingInput,
    IfcProjectInput,
    IfcStoryInput,
    build_ifc_from_model,
)
from services.model_story import DEFAULT_BASEMENT_HEIGHT_M, DEFAULT_STORY_HEIGHT_M

logger = logging.getLogger(__name__)

_DEFAULT_PROJECT_NAME = "工程模型"
_DEFAULT_SITE_NAME = "默认场地"
_PROVENANCE_PSET = "Pset_ModelProvenance"
_ELEMENT_KINDS = ("columns", "walls", "beams", "slabs", "pipes", "equipment")


# ── 单层标高/层高估算标记 ────────────────────────────────────────


@dataclass(frozen=True)
class _StoryProvenance:
    """单层来源标记：标高/层高是否为估算值。"""

    elevation_estimated: bool
    height_estimated: bool

    @property
    def is_estimated(self) -> bool:
        return self.elevation_estimated or self.height_estimated


def _floor_height(order: int, explicit: Any) -> tuple[float, bool]:
    """返回 (层高_米, 是否估算)；显式 height_m 缺失时按层序取默认常量。"""
    if explicit is not None:
        return float(explicit), False
    default = DEFAULT_BASEMENT_HEIGHT_M if order < 0 else DEFAULT_STORY_HEIGHT_M
    return default, True


def _estimate_elevation(order: int, prev_elev: float | None, prev_height: float) -> float:
    """标高缺失时的层序估算：叠加下层层高；最底层按层序落位。"""
    if prev_elev is not None:
        return round(prev_elev + prev_height, 3)
    return 0.0 if order >= 1 else round(order * prev_height, 3)


# ── scene → 输入契约映射 ─────────────────────────────────────────


def _elements_dict(floor: dict) -> dict[str, list]:
    """楼层 elements 归一为 {kind: [...]}（缺类补空，防御外部脏数据）。"""
    source = floor.get("elements") or {}
    if not isinstance(source, dict):
        return {kind: [] for kind in _ELEMENT_KINDS}
    return {kind: list(source.get(kind) or []) for kind in _ELEMENT_KINDS}


def _story_input(floor: dict, prev_elev: float | None) -> tuple[IfcStoryInput, _StoryProvenance]:
    """单层 floor → (IfcStoryInput, 标高/层高估算标记)。"""
    order = int(floor.get("order") or 0)
    height, height_estimated = _floor_height(order, floor.get("height_m"))
    raw_elev = floor.get("elevation_m")
    elevation_estimated = raw_elev is None
    elevation = (
        _estimate_elevation(order, prev_elev, height)
        if elevation_estimated
        else round(float(raw_elev), 3)
    )
    story = IfcStoryInput(
        story_key=str(floor.get("key") or f"F{order}"),
        display_name=str(floor.get("label") or floor.get("key") or f"{order}层"),
        story_order=order,
        elevation_m=elevation,
        height_m=height,
        elements=_elements_dict(floor),
    )
    return story, _StoryProvenance(elevation_estimated, height_estimated)


def _building_input(
    building: dict,
    provenance: dict[tuple[str, float], _StoryProvenance],
) -> IfcBuildingInput:
    """单体 dict → IfcBuildingInput；同时登记各层估算标记（供 Pset 标注）。"""
    floors = sorted(
        (f for f in (building.get("floors") or []) if isinstance(f, dict)),
        key=lambda f: int(f.get("order") or 0),
    )
    stories: list[IfcStoryInput] = []
    prev_elev: float | None = None
    prev_height = DEFAULT_STORY_HEIGHT_M
    for floor in floors:
        story, mark = _story_input(floor, prev_elev)
        stories.append(story)
        provenance[(story.display_name, round(story.elevation_m, 3))] = mark
        prev_elev, prev_height = story.elevation_m, story.height_m
    return IfcBuildingInput(
        unit_key=str(building.get("key") or "main"),
        display_name=str(building.get("label") or building.get("key") or "主体"),
        stories=tuple(stories),
    )


def _resolve_buildings(scene: dict) -> list[dict]:
    """取 scene 单体分组；缺失时回退为把顶层 floors 包成单一 main 单体。"""
    buildings = scene.get("buildings")
    if isinstance(buildings, list) and buildings:
        return [b for b in buildings if isinstance(b, dict)]
    floors = scene.get("floors")
    if isinstance(floors, list) and floors:
        return [{"key": "main", "label": "主体", "floors": floors}]
    return []


def _map_scene(
    scene: dict, project_name: str | None
) -> tuple[IfcProjectInput, dict[tuple[str, float], _StoryProvenance]]:
    """scene → (IfcProjectInput, 估算标记表)。"""
    project = scene.get("project") if isinstance(scene.get("project"), dict) else {}
    name = (project_name or str(project.get("name") or "")).strip() or _DEFAULT_PROJECT_NAME
    provenance: dict[tuple[str, float], _StoryProvenance] = {}
    buildings = tuple(
        _building_input(building, provenance) for building in _resolve_buildings(scene)
    )
    project_input = IfcProjectInput(
        project_name=name, site_name=_DEFAULT_SITE_NAME, buildings=buildings
    )
    return project_input, provenance


# ── Pset 标注（is_estimated）────────────────────────────────────


def _bool_prop(model: Any, name: str, value: bool) -> Any:
    return model.createIfcPropertySingleValue(name, None, model.createIfcBoolean(bool(value)), None)


def _attach_provenance(
    model: Any, provenance: dict[tuple[str, float], _StoryProvenance]
) -> None:
    """为每个 IfcBuildingStorey 挂 Pset_ModelProvenance，标注估算来源。

    标高缺失映射不到标记时默认 ``IsEstimated=true``（安全侧，绝不伪装成实测）。
    """
    default = _StoryProvenance(True, True)
    for storey in model.by_type("IfcBuildingStorey"):
        key = (storey.Name, round(float(storey.Elevation or 0.0), 3))
        mark = provenance.get(key, default)
        props = [
            _bool_prop(model, "IsEstimated", mark.is_estimated),
            _bool_prop(model, "ElevationEstimated", mark.elevation_estimated),
            _bool_prop(model, "HeightEstimated", mark.height_estimated),
        ]
        pset = model.createIfcPropertySet(
            ifcopenshell.guid.new(), None, _PROVENANCE_PSET, None, props
        )
        model.createIfcRelDefinesByProperties(
            ifcopenshell.guid.new(), None, None, None, [storey], pset
        )


# ── 公共入口 ────────────────────────────────────────────────────


def build_ifc_from_scene(scene: dict, project_name: str | None = None) -> bytes:
    """从 build_scene 的 scene 产出合规 IFC4 字节流（.ifc）。

    Args:
        scene: ``build_scene`` 输出的场景字典（须含 ``buildings`` 或 ``floors``）。
        project_name: 可选项目名覆盖；缺省取 ``scene['project']['name']``。

    Returns:
        IFC4 文本（ISO-10303-21）UTF-8 字节。可被 ``ifcopenshell.open`` /
        BlenderBIM / That Open 打开。楼层标高/层高估算处已在
        ``Pset_ModelProvenance`` 标注 ``IsEstimated=true``。

    Raises:
        TypeError: scene 非 dict。
    """
    if not isinstance(scene, dict):
        raise TypeError(f"scene 必须为 dict，收到 {type(scene).__name__}")

    project_input, provenance = _map_scene(scene, project_name)
    result = build_ifc_from_model(project_input)
    if not result.ifc_bytes:
        raise RuntimeError("build_ifc_from_model 未返回字节流")

    model = ifcopenshell.file.from_string(result.ifc_bytes.decode("utf-8"))
    _attach_provenance(model, provenance)
    logger.info(
        "[IfcMapping] 生成 IFC：单体 %d，构件计数 %s", len(project_input.buildings), result.counts
    )
    return model.to_string().encode("utf-8")
