"""程序化 IFC 建模器（PoC，蓝图 MODEL_PRECISION_BLUEPRINT 标准化第一步）。

把构件识别输出（``FloorElements``，米坐标）转成合规 IFC4 文件：
``IfcProject → IfcSite → IfcBuilding → IfcBuildingStorey``，柱/墙/梁/板/管线/设备
以 ``IfcExtrudedAreaSolid`` 拉伸体表达，并挂 ``Qto_*BaseQuantities`` 量集。

坐标约定（与 ``FloorElements`` 一致）：单位米，原点在最小轴号交点，y 已翻转为数学系。
本模块不做静默降级：单个构件几何非法（点数不足/长度为零）会被跳过并记 warning，
但整体建模失败会显式抛出，交由上层处理。

依赖 ``ifcopenshell``（见 requirements.txt）。未安装时导入本模块即抛 ImportError，
属预期行为——本模块为可选 PoC，未接入 main.py 启动链路。
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Sequence

import ifcopenshell
import ifcopenshell.api.aggregate
import ifcopenshell.api.context
import ifcopenshell.api.project
import ifcopenshell.api.root
import ifcopenshell.api.spatial
import ifcopenshell.api.unit
import ifcopenshell.guid

from core.model3d.types import FloorElements

logger = logging.getLogger(__name__)

IFC_SCHEMA = "IFC4"
_MIN_POLYGON_POINTS = 3
_MIN_SEGMENT_LEN_M = 0.05
_MIN_PIPE_DIA_M = 0.02
_DEFAULT_WALL_WIDTH_M = 0.2
_DEFAULT_BEAM_WIDTH_M = 0.3
_DEFAULT_BEAM_DEPTH_M = 0.6
_DEFAULT_SLAB_THICKNESS_M = 0.12
_DEFAULT_EQUIPMENT_HEIGHT_M = 1.5
_Z_UP = (0.0, 0.0, 1.0)

_ELEMENT_KINDS = ("columns", "walls", "beams", "slabs", "pipes", "equipment")


# ── 输入契约（不可变 DTO）────────────────────────────────────────


@dataclass(frozen=True)
class IfcStoryInput:
    """单层输入：楼层元数据 + 该层识别出的构件集合。"""

    story_key: str
    display_name: str
    story_order: int
    elevation_m: float
    height_m: float
    elements: FloorElements | dict[str, Any]


@dataclass(frozen=True)
class IfcBuildingInput:
    """单体输入：一栋楼及其楼层序列。"""

    unit_key: str
    display_name: str
    stories: tuple[IfcStoryInput, ...]


@dataclass(frozen=True)
class IfcProjectInput:
    """项目输入：项目名 + 场地名 + 若干单体。"""

    project_name: str
    site_name: str = "默认场地"
    buildings: tuple[IfcBuildingInput, ...] = ()


@dataclass(frozen=True)
class IfcBuildResult:
    """建模结果：文件路径或字节流 + 构件计数。"""

    path: str | None
    ifc_bytes: bytes | None
    counts: dict[str, int] = field(default_factory=dict)


# ── 输入归一化 ──────────────────────────────────────────────────


def _as_elements_dict(elements: FloorElements | dict[str, Any]) -> dict[str, list[dict]]:
    """把 FloorElements 或原始 dict 归一为 {kind: [构件dict]}。"""
    source = elements.as_dict() if isinstance(elements, FloorElements) else dict(elements or {})
    return {kind: list(source.get(kind) or []) for kind in _ELEMENT_KINDS}


def _points_2d(raw: Sequence[Any]) -> list[tuple[float, float]]:
    """把 [[x,y],...] 清洗为浮点二元组列表，跳过非法项。"""
    points: list[tuple[float, float]] = []
    for item in raw or []:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            points.append((float(item[0]), float(item[1])))
    return points


# ── 底层几何 / 放置 helper（纯 model.createIfc*，无版本漂移）──────


def _point3(model: Any, x: float, y: float, z: float) -> Any:
    return model.createIfcCartesianPoint((float(x), float(y), float(z)))


def _local_placement(model: Any, relative_to: Any, x: float = 0.0, y: float = 0.0, z: float = 0.0) -> Any:
    axis = model.createIfcAxis2Placement3D(_point3(model, x, y, z), None, None)
    return model.createIfcLocalPlacement(relative_to, axis)


def _polygon_profile(model: Any, points: list[tuple[float, float]]) -> Any | None:
    """任意闭合多边形截面（柱/板/设备轮廓）。"""
    if len(points) < _MIN_POLYGON_POINTS:
        return None
    ring = points if points[0] == points[-1] else [*points, points[0]]
    poly = model.createIfcPolyline([model.createIfcCartesianPoint((px, py)) for px, py in ring])
    return model.createIfcArbitraryClosedProfileDef("AREA", None, poly)


def _oriented_box_profile(model: Any, path: list[tuple[float, float]], width: float) -> tuple[Any, float] | None:
    """沿 path 首末点方向的定向矩形截面（墙/梁）；返回 (profile, 长度)。"""
    if len(path) < 2:
        return None
    (x0, y0), (x1, y1) = path[0], path[-1]
    dx, dy = x1 - x0, y1 - y0
    length = math.hypot(dx, dy)
    if length < _MIN_SEGMENT_LEN_M:
        return None
    mid = model.createIfcCartesianPoint(((x0 + x1) / 2.0, (y0 + y1) / 2.0))
    ref_dir = model.createIfcDirection((dx / length, dy / length))
    position = model.createIfcAxis2Placement2D(mid, ref_dir)
    profile = model.createIfcRectangleProfileDef("AREA", None, position, length, max(width, _DEFAULT_WALL_WIDTH_M))
    return profile, length


def _vertical_solid(model: Any, profile: Any, height: float, z_base: float = 0.0) -> Any:
    """沿 +Z 拉伸的实体（柱/墙/梁/板/设备）。"""
    position = model.createIfcAxis2Placement3D(_point3(model, 0.0, 0.0, z_base), None, None)
    direction = model.createIfcDirection(_Z_UP)
    return model.createIfcExtrudedAreaSolid(profile, position, direction, float(height))


def _pipe_solid(model: Any, path: list[tuple[float, float]], dia: float, z_base: float) -> Any | None:
    """沿 path 水平方向拉伸的圆截面实体（管线）。"""
    if len(path) < 2:
        return None
    (x0, y0), (x1, y1) = path[0], path[-1]
    dx, dy = x1 - x0, y1 - y0
    length = math.hypot(dx, dy)
    if length < _MIN_SEGMENT_LEN_M:
        return None
    axis = model.createIfcDirection((dx / length, dy / length, 0.0))
    position = model.createIfcAxis2Placement3D(_point3(model, x0, y0, z_base), axis, None)
    profile = model.createIfcCircleProfileDef("AREA", None, None, max(dia, _MIN_PIPE_DIA_M) / 2.0)
    return model.createIfcExtrudedAreaSolid(profile, position, model.createIfcDirection(_Z_UP), length)


def _shape(model: Any, body_ctx: Any, solid: Any) -> Any:
    """实体 → IfcProductDefinitionShape。"""
    rep = model.createIfcShapeRepresentation(body_ctx, "Body", "SweptSolid", [solid])
    return model.createIfcProductDefinitionShape(None, None, [rep])


# ── 量集（Qto_*BaseQuantities）──────────────────────────────────


def _add_base_quantities(
    model: Any,
    product: Any,
    name: str,
    *,
    lengths: dict[str, float] | None = None,
    areas: dict[str, float] | None = None,
    volumes: dict[str, float] | None = None,
) -> None:
    """手工挂量集（版本稳定，避免高层 pset API 漂移）。"""
    quantities: list[Any] = []
    for qname, value in (lengths or {}).items():
        quantities.append(model.createIfcQuantityLength(qname, None, None, float(value), None))
    for qname, value in (areas or {}).items():
        quantities.append(model.createIfcQuantityArea(qname, None, None, float(value), None))
    for qname, value in (volumes or {}).items():
        quantities.append(model.createIfcQuantityVolume(qname, None, None, float(value), None))
    if not quantities:
        return
    qto = model.createIfcElementQuantity(ifcopenshell.guid.new(), None, name, None, None, quantities)
    model.createIfcRelDefinesByProperties(ifcopenshell.guid.new(), None, None, None, [product], qto)


def _polygon_area(points: list[tuple[float, float]]) -> float:
    """鞋带公式求多边形面积（绝对值）。"""
    if len(points) < _MIN_POLYGON_POINTS:
        return 0.0
    total = 0.0
    for index in range(len(points)):
        x0, y0 = points[index]
        x1, y1 = points[(index + 1) % len(points)]
        total += x0 * y1 - x1 * y0
    return abs(total) / 2.0


# ── 构件挂载 helper ─────────────────────────────────────────────


@dataclass(frozen=True)
class _StoreyContext:
    """建楼层构件时的上下文（避免长参数列表）。"""

    model: Any
    body_ctx: Any
    placement: Any
    height_m: float


def _create_element(ctx: _StoreyContext, ifc_class: str, name: str, solid: Any) -> Any:
    """创建构件实体并挂放置 + 几何表达。"""
    element = ifcopenshell.api.root.create_entity(ctx.model, ifc_class=ifc_class, name=name)
    element.ObjectPlacement = _local_placement(ctx.model, ctx.placement)
    element.Representation = _shape(ctx.model, ctx.body_ctx, solid)
    return element


def _add_column(ctx: _StoreyContext, spec: dict) -> Any | None:
    points = _points_2d(spec.get("outline"))
    profile = _polygon_profile(ctx.model, points)
    if profile is None:
        return None
    solid = _vertical_solid(ctx.model, profile, ctx.height_m)
    column = _create_element(ctx, "IfcColumn", "柱", solid)
    area = _polygon_area(points)
    _add_base_quantities(
        ctx.model, column, "Qto_ColumnBaseQuantities",
        lengths={"Length": ctx.height_m}, areas={"CrossSectionArea": area},
        volumes={"NetVolume": area * ctx.height_m},
    )
    return column


def _add_wall(ctx: _StoreyContext, spec: dict) -> Any | None:
    path = _points_2d(spec.get("path"))
    width = float(spec.get("width") or _DEFAULT_WALL_WIDTH_M)
    box = _oriented_box_profile(ctx.model, path, width)
    if box is None:
        return None
    profile, length = box
    solid = _vertical_solid(ctx.model, profile, ctx.height_m)
    wall = _create_element(ctx, "IfcWall", "墙", solid)
    _add_base_quantities(
        ctx.model, wall, "Qto_WallBaseQuantities",
        lengths={"Length": length, "Height": ctx.height_m, "Width": width},
        volumes={"NetVolume": length * width * ctx.height_m},
    )
    return wall


def _add_beam(ctx: _StoreyContext, spec: dict) -> Any | None:
    path = _points_2d(spec.get("path"))
    width = float(spec.get("width") or _DEFAULT_BEAM_WIDTH_M)
    depth = float(spec.get("depth") or _DEFAULT_BEAM_DEPTH_M)
    box = _oriented_box_profile(ctx.model, path, width)
    if box is None:
        return None
    profile, length = box
    z_base = max(ctx.height_m - depth, 0.0)
    solid = _vertical_solid(ctx.model, profile, depth, z_base=z_base)
    beam = _create_element(ctx, "IfcBeam", "梁", solid)
    _add_base_quantities(
        ctx.model, beam, "Qto_BeamBaseQuantities",
        lengths={"Length": length}, areas={"CrossSectionArea": width * depth},
        volumes={"NetVolume": length * width * depth},
    )
    return beam


def _add_slab(ctx: _StoreyContext, spec: dict) -> Any | None:
    points = _points_2d(spec.get("outline"))
    thickness = float(spec.get("thickness") or _DEFAULT_SLAB_THICKNESS_M)
    profile = _polygon_profile(ctx.model, points)
    if profile is None:
        return None
    solid = _vertical_solid(ctx.model, profile, thickness)
    slab = _create_element(ctx, "IfcSlab", "板", solid)
    area = _polygon_area(points)
    _add_base_quantities(
        ctx.model, slab, "Qto_SlabBaseQuantities",
        lengths={"Width": thickness}, areas={"GrossArea": area},
        volumes={"NetVolume": area * thickness},
    )
    return slab


def _add_pipe(ctx: _StoreyContext, spec: dict) -> Any | None:
    path = _points_2d(spec.get("path"))
    dia = float(spec.get("dia") or _MIN_PIPE_DIA_M)
    z_base = max(ctx.height_m - 0.5, 0.0)
    solid = _pipe_solid(ctx.model, path, dia, z_base)
    if solid is None:
        return None
    system = str(spec.get("system") or "管线")
    pipe = _create_element(ctx, "IfcFlowSegment", system, solid)
    return pipe


def _add_equipment(ctx: _StoreyContext, spec: dict) -> Any | None:
    points = _points_2d(spec.get("outline"))
    height = float(spec.get("height") or _DEFAULT_EQUIPMENT_HEIGHT_M)
    profile = _polygon_profile(ctx.model, points)
    if profile is None:
        return None
    solid = _vertical_solid(ctx.model, profile, height)
    label = str(spec.get("label") or "设备")
    return _create_element(ctx, "IfcBuildingElementProxy", label, solid)


_ADDERS = {
    "columns": _add_column,
    "walls": _add_wall,
    "beams": _add_beam,
    "slabs": _add_slab,
    "pipes": _add_pipe,
    "equipment": _add_equipment,
}


# ── 楼层 / 空间层级组装 ─────────────────────────────────────────


def _build_storey(
    model: Any,
    body_ctx: Any,
    building: Any,
    building_placement: Any,
    story: IfcStoryInput,
    counts: dict[str, int],
) -> None:
    """创建楼层 + 挂构件 + 空间归属。"""
    storey = ifcopenshell.api.root.create_entity(model, ifc_class="IfcBuildingStorey", name=story.display_name)
    storey.Elevation = float(story.elevation_m)
    storey.ObjectPlacement = _local_placement(model, building_placement, z=story.elevation_m)
    ifcopenshell.api.aggregate.assign_object(model, products=[storey], relating_object=building)

    ctx = _StoreyContext(model=model, body_ctx=body_ctx, placement=storey.ObjectPlacement, height_m=story.height_m)
    elements = _as_elements_dict(story.elements)
    products: list[Any] = []
    for kind in _ELEMENT_KINDS:
        adder = _ADDERS[kind]
        for spec in elements[kind]:
            product = adder(ctx, spec)
            if product is None:
                logger.warning("跳过非法 %s 构件：%s", kind, spec)
                continue
            products.append(product)
            counts[kind] = counts.get(kind, 0) + 1
    if products:
        ifcopenshell.api.spatial.assign_container(model, products=products, relating_structure=storey)


def _build_building(
    model: Any,
    body_ctx: Any,
    site: Any,
    site_placement: Any,
    unit: IfcBuildingInput,
    counts: dict[str, int],
) -> None:
    """创建单体 + 其全部楼层（按 story_order 升序）。"""
    building = ifcopenshell.api.root.create_entity(model, ifc_class="IfcBuilding", name=unit.display_name)
    building.ObjectPlacement = _local_placement(model, site_placement)
    ifcopenshell.api.aggregate.assign_object(model, products=[building], relating_object=site)
    for story in sorted(unit.stories, key=lambda item: item.story_order):
        _build_storey(model, body_ctx, building, building.ObjectPlacement, story, counts)


def _init_project(model: Any, project_name: str) -> tuple[Any, Any]:
    """创建 IfcProject + 单位（米）+ Body 几何上下文，返回 (project, body_ctx)。"""
    project = ifcopenshell.api.root.create_entity(model, ifc_class="IfcProject", name=project_name)
    length_unit = ifcopenshell.api.unit.add_si_unit(model, unit_type="LENGTHUNIT")
    ifcopenshell.api.unit.assign_unit(model, units=[length_unit])
    model_ctx = ifcopenshell.api.context.add_context(model, context_type="Model")
    body_ctx = ifcopenshell.api.context.add_context(
        model, context_type="Model", context_identifier="Body",
        target_view="MODEL_VIEW", parent=model_ctx,
    )
    return project, body_ctx


# ── 公共入口 ────────────────────────────────────────────────────


def build_ifc_from_model(project_input: IfcProjectInput, output_path: str | None = None) -> IfcBuildResult:
    """从构件识别结果构建 IFC4 文件。

    Args:
        project_input: 项目 / 单体 / 楼层 / 构件的完整输入契约。
        output_path: 若给定则写入该 .ifc 路径并返回 path；否则返回 bytes。

    Returns:
        IfcBuildResult：path 或 ifc_bytes 二选一，附各类构件计数。
    """
    if not project_input.project_name:
        raise ValueError("project_name 不能为空")

    model = ifcopenshell.api.project.create_file(version=IFC_SCHEMA)
    project, body_ctx = _init_project(model, project_input.project_name)

    site = ifcopenshell.api.root.create_entity(model, ifc_class="IfcSite", name=project_input.site_name)
    site.ObjectPlacement = _local_placement(model, None)
    ifcopenshell.api.aggregate.assign_object(model, products=[site], relating_object=project)

    counts: dict[str, int] = {}
    for unit in project_input.buildings:
        _build_building(model, body_ctx, site, site.ObjectPlacement, unit, counts)

    if output_path:
        model.write(output_path)
        return IfcBuildResult(path=output_path, ifc_bytes=None, counts=counts)
    return IfcBuildResult(path=None, ifc_bytes=model.to_string().encode("utf-8"), counts=counts)
