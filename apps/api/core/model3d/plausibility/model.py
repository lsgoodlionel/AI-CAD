"""场景 JSON → 合理性分析用的规范化视图。

**为什么要一层视图**：`project_models.scene` 是给前端渲染用的（单体→楼层→
按类分组的构件字典），规则却要按「这根柱在哪一层、下面有没有支承、标高是多少」
来想。把这层转换写一次，六族规则就都能只关心构件语义。

**不改也不补数据**。这里只做形状转换与派生量（面积、边长、长度、z 区间），
**缺什么就是缺什么** —— 标高估出来的要带着 `elevation_estimated` 往下传，
规则据此决定是跑还是报 `skipped`。谎报「有数据」比没数据更坏。

构件字段来自 `core/model3d/types.FloorElements`：
- 柱 / 板 / 设备：``outline``（米，闭合环的点列）
- 墙 / 梁 / 管线：``path``（两点线段）+ ``width``
- 板另有 ``thickness``、设备另有 ``height``、板可能有 ``basis``（兜底依据）
"""
from __future__ import annotations

from dataclasses import dataclass, field

from core.model3d.plausibility import geometry as geo

#: 场景里按类分组的键。顺序固定 —— 报告与统计不随字典遍历漂移。
ELEMENT_KINDS = ("columns", "walls", "beams", "slabs", "pipes", "equipment")


@dataclass(frozen=True)
class Element:
    """一个构件（米坐标）。"""
    uid: str
    kind: str
    building_key: str
    floor_key: str
    raw: dict = field(repr=False, default_factory=dict)

    # ── 几何派生量（都可能是 None：缺数据不编）────────────────────
    @property
    def outline(self) -> list[tuple[float, float]]:
        return [(float(p[0]), float(p[1])) for p in (self.raw.get("outline") or [])
                if len(p) >= 2]

    @property
    def path(self) -> list[tuple[float, float]]:
        return [(float(p[0]), float(p[1])) for p in (self.raw.get("path") or [])
                if len(p) >= 2]

    @property
    def width_m(self) -> float | None:
        value = self.raw.get("width")
        return float(value) if value is not None else None

    @property
    def thickness_m(self) -> float | None:
        value = self.raw.get("thickness")
        return float(value) if value is not None else None

    @property
    def height_m(self) -> float | None:
        value = self.raw.get("height")
        return float(value) if value is not None else None

    @property
    def fallback_basis(self) -> str:
        """板的兜底依据（`largest_polygon` 等）。非兜底为空串。"""
        return str(self.raw.get("basis") or "")

    def area_m2(self) -> float | None:
        """轮廓面积（带符号取绝对值）。没有轮廓返回 None。"""
        ring = self.outline
        return geo.polygon_area(ring) if len(ring) >= 3 else None

    def sides_m(self) -> tuple[float, float] | None:
        """最小面积外接矩形的（长边, 短边）。

        **不用轴对齐包围盒** —— 实测标高符号 `∨` 的一条斜笔画按 AABB 量是
        0.52×0.59m 近方形（像柱），按最小外接矩形量是 0.70×0.12m（不像柱）。
        同一个教训不必再踩一次。
        """
        ring = self.outline
        return geo.min_area_rect(ring) if len(ring) >= 3 else None

    def length_m(self) -> float | None:
        """线状构件的长度。"""
        pts = self.path
        return geo.polyline_length(pts) if len(pts) >= 2 else None

    def footprint(self) -> list[tuple[float, float]]:
        """占地多边形：面状取轮廓，线状取按 width 加宽的矩形。"""
        ring = self.outline
        if len(ring) >= 3:
            return ring
        pts = self.path
        if len(pts) >= 2 and self.width_m:
            return geo.thick_segment_ring(pts[0], pts[-1], self.width_m)
        return []


@dataclass(frozen=True)
class Floor:
    """一层。`height_m` 由与上一层的标高差算出，算不出为 None。"""
    key: str
    label: str
    order: int
    building_key: str
    elevation_m: float | None = None
    elevation_estimated: bool = True
    height_m: float | None = None
    elements: tuple[Element, ...] = ()

    def of_kind(self, kind: str) -> tuple[Element, ...]:
        return tuple(e for e in self.elements if e.kind == kind)


@dataclass(frozen=True)
class Building:
    key: str
    label: str
    floors: tuple[Floor, ...] = ()


@dataclass(frozen=True)
class PlausibilityModel:
    """一个工程模型的规范化视图。"""
    buildings: tuple[Building, ...] = ()
    meta: dict = field(default_factory=dict)

    def floors(self):
        for building in self.buildings:
            yield from building.floors

    def elements(self, kind: str | None = None):
        for floor in self.floors():
            for element in floor.elements:
                if kind is None or element.kind == kind:
                    yield element

    def count(self, kind: str | None = None) -> int:
        return sum(1 for _ in self.elements(kind))


def _floor_heights(floors: list[dict]) -> list[float | None]:
    """按标高差算层高；最顶层沿用下一层的层高（没有更上层可减）。

    标高缺失或非递增时该层写 None —— 层高本身就是规则要查的东西
    （`floor.zero_or_negative_height`），这里不能替它把数补圆。
    """
    elevations = [f.get("elevation_m") for f in floors]
    heights: list[float | None] = []
    for index, value in enumerate(elevations):
        if value is None or index + 1 >= len(elevations) or elevations[index + 1] is None:
            heights.append(None)
            continue
        heights.append(float(elevations[index + 1]) - float(value))
    known = [h for h in heights[:-1] if h is not None]
    if heights and heights[-1] is None and known:
        heights[-1] = known[-1]          # 顶层沿用下一层，并非实测
    return heights


def from_scene(scene: dict) -> PlausibilityModel:
    """`project_models.scene` → 规范化视图。结构不符时返回空模型（不抛）。"""
    buildings: list[Building] = []
    if not isinstance(scene, dict):
        return PlausibilityModel()
    for raw_building in scene.get("buildings") or []:
        if not isinstance(raw_building, dict):
            continue
        bkey = str(raw_building.get("key") or "main")
        raw_floors = [f for f in (raw_building.get("floors") or []) if isinstance(f, dict)]
        heights = _floor_heights(raw_floors)
        floors: list[Floor] = []
        for index, raw_floor in enumerate(raw_floors):
            if not isinstance(raw_floor, dict):
                continue
            fkey = str(raw_floor.get("key") or index)
            # 场景是外部数据（一路从 PDF 识别到 jsonb），形状不合约就当没有 ——
            # 这一层**只做转换**，不为畸形数据编内容，也不让它把分析整轮打断
            grouped = raw_floor.get("elements")
            grouped = grouped if isinstance(grouped, dict) else {}
            elements: list[Element] = []
            for kind in ELEMENT_KINDS:
                for i, raw in enumerate(grouped.get(kind) or []):
                    if not isinstance(raw, dict):
                        continue
                    elements.append(Element(
                        uid=f"{bkey}/{fkey}/{kind}/{i}", kind=kind,
                        building_key=bkey, floor_key=fkey, raw=raw))
            elevation = raw_floor.get("elevation_m")
            floors.append(Floor(
                key=fkey, label=str(raw_floor.get("label") or fkey),
                order=int(raw_floor.get("order") or index), building_key=bkey,
                elevation_m=float(elevation) if elevation is not None else None,
                elevation_estimated=bool(raw_floor.get("elevation_estimated", True)),
                height_m=heights[index] if index < len(heights) else None,
                elements=tuple(elements)))
        buildings.append(Building(key=bkey,
                                  label=str(raw_building.get("label") or bkey),
                                  floors=tuple(floors)))
    return PlausibilityModel(buildings=tuple(buildings),
                             meta={"version": (scene or {}).get("version")})
