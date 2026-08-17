"""Phase H2:各引擎识别产物 → 观测(component_observations)。纯函数,不改引擎。

把 model_elements 已产出的构件记录(带 src/source/outline|path/type)转成实体装配
所需的**观测候选**:每条精确记录来自哪张图(drawing_id)、哪个引擎(engine)、
落点(world_coord 质心 + local_coord 轮廓)、类型与置信。供 H3 装配器按
「轴网格 + 类型」关联到 ComponentInstance。

观测候选字段 = component_observations 列(drawing_id/view_type/engine/grid_cell/
local_coord/world_coord/archive_ref/confidence)+ 建实体所需提示(type/type_label/label)。
grid_cell / archive_ref 在 H2 留空,H3 用 axis_map / 档案回填。
"""
from __future__ import annotations

from typing import Any

# 构件 kind → 实体 type(与 migration 033 component_instances.type 对齐)
KIND_TO_TYPE: dict[str, str] = {
    "columns": "column",
    "walls": "wall",
    "beams": "beam",
    "slabs": "slab",
    "pipes": "pipe",
    "equipment": "equipment",
}


def _points_of(item: dict) -> list[list[float]]:
    """构件平面点:columns/slabs/equipment 用 outline;walls/beams/pipes 用 path。"""
    return item.get("outline") or item.get("path") or []


def _centroid(points: list) -> list[float] | None:
    """点集质心(米);无有效点返回 None。"""
    pts = [p for p in points if isinstance(p, (list, tuple)) and len(p) >= 2]
    if not pts:
        return None
    n = len(pts)
    return [
        round(sum(float(p[0]) for p in pts) / n, 3),
        round(sum(float(p[1]) for p in pts) / n, 3),
    ]


def _nearest_label(coord: float, entries: list) -> str | None:
    """在一组轴线 [{label,coord}] 里找离 coord 最近的轴号标签。"""
    best: str | None = None
    best_dist: float | None = None
    for entry in entries or []:
        try:
            dist = abs(float(entry["coord"]) - coord)
        except (KeyError, TypeError, ValueError):
            continue
        if best_dist is None or dist < best_dist:
            best_dist = dist
            best = str(entry.get("label") or "")
    return best or None


def locate_in_grid(world_coord: list | None, axes: dict | None) -> str | None:
    """构件世界坐标质心 → 最近轴网格标签 "{x轴}-{y轴}"(H3 数据关联主键)。

    axes = {"x":[{label,coord}], "y":[{label,coord}]}(米,与构件同坐标系,
    即 model_elements `_axes_scene_payload` 产物)。缺坐标/缺轴网返回 None。
    """
    if not world_coord or not axes:
        return None
    x_label = _nearest_label(float(world_coord[0]), axes.get("x") or [])
    y_label = _nearest_label(float(world_coord[1]), axes.get("y") or [])
    if x_label is None and y_label is None:
        return None
    # 双轴命中 → 完整网格键 "X-Y"(可作合并主键);单轴命中 → 部分键 "X-?"/"?-Y"
    # (仅供显示/配准覆盖,**装配器不以带 ? 的部分键作合并键**,走米坐标兜底,
    # 避免"X-?"把沿该轴所有构件狂并——实测 pipe 曾并 802 个)。
    return f"{x_label or '?'}-{y_label or '?'}"


def is_full_grid(grid_cell: str | None) -> bool:
    """完整网格键(双轴都命中,可作数据关联合并主键);部分键含 ? 不可作合并键。"""
    return bool(grid_cell) and "?" not in grid_cell


def observation_from_element(
    item: dict, kind: str, view_type: str | None, axes: dict | None = None,
) -> dict | None:
    """单个构件记录 → 一条观测候选。无来源图纸(src)则返回 None(观测必须可溯源)。

    axes 存在时,按质心定位轴网格填 grid_cell(H3 关联主键)。
    """
    src = item.get("src")
    if not src:
        return None
    points = _points_of(item)
    world_coord = _centroid(points)
    return {
        # ── component_observations 列 ──
        "drawing_id": str(src),
        "view_type": view_type,
        "engine": item.get("source") or "rule",
        "grid_cell": locate_in_grid(world_coord, axes),
        "local_coord": points or None,
        "world_coord": world_coord,
        "archive_ref": None,               # 未来接档案条目 id
        "confidence": float(item.get("confidence") or 0.0),
        # ── 建实体提示(H3 用)──
        "type": KIND_TO_TYPE.get(kind, kind),
        "type_label": item.get("type_text") or item.get("type_label"),
        "label": item.get("label"),
    }


def region_to_world(bbox: list, transform: dict) -> list[float] | None:
    """VLM 分区区域(图像归一化 bbox [x,y,w,h])中心 → 米坐标(正变换,与几何观测同帧)。

    transform 需含 scale/origin_x/origin_y/page_h/page_w(page_w 由调用方按图像宽高比算)。
    x_pt=cx_frac*page_w;y_pt=cy_frac*page_h;x_m=(x_pt-ox)*scale;y_m=((page_h-y_pt)-oy)*scale。
    """
    if not bbox or len(bbox) != 4 or not transform:
        return None
    scale = transform.get("scale")
    page_h = transform.get("page_h")
    page_w = transform.get("page_w")
    if not scale or not page_h or not page_w:
        return None
    x, y, w, h = (float(v) for v in bbox)
    cx_pt = (x + w / 2) * page_w
    cy_pt = (y + h / 2) * page_h
    x_m = (cx_pt - transform.get("origin_x", 0)) * scale
    y_m = ((page_h - cy_pt) - transform.get("origin_y", 0)) * scale
    return [round(x_m, 3), round(y_m, 3)]


def regions_to_observations(
    regions: list[dict], drawing_id: str, transform: dict | None,
    view_type: str | None = "plan",
) -> list[dict]:
    """VLM 语义分区区域 → 观测候选(engine='vlm'),补几何引擎空洞(H5 职责 C 接线)。

    与 observations_from_elements 同格式,装配器直接消费。无 transform → world_coord=None
    (仍产观测,装配走无坐标兜底/人审)。
    """
    out: list[dict] = []
    for r in regions:
        rtype = r.get("type")
        if not rtype:
            continue
        out.append({
            "drawing_id": str(drawing_id),
            "view_type": view_type,
            "engine": "vlm",
            "grid_cell": None,
            "local_coord": None,
            "world_coord": region_to_world(r.get("bbox"), transform) if transform else None,
            "archive_ref": None,
            "confidence": float(r.get("confidence") or 0.5),
            "type": rtype,
            "type_label": None,
            "label": None,
        })
    return out


def observations_from_elements(
    elements: dict[str, list[dict]],
    view_type: str | None = None,
    axes: dict | None = None,
) -> list[dict]:
    """一层(可跨图合并)的构件集合 → 观测候选列表。

    遍历 KIND_TO_TYPE 覆盖的构件种类,每个构件产一条观测(无 src 的跳过)。
    axes 存在时填 grid_cell。纯函数、无 IO,离线可测。
    """
    out: list[dict] = []
    for kind in KIND_TO_TYPE:
        for item in elements.get(kind) or []:
            obs = observation_from_element(item, kind, view_type, axes)
            if obs is not None:
                out.append(obs)
    return out
