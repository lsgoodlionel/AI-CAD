"""按工程坐标摆放构件:把每张图的构件从「本图局部米坐标」搬到工程坐标系。

**为什么需要**:识别出的构件坐标是**以该图自己的轴网原点为原点**的米坐标
(`drawing_transform.pt_to_meter` 的口径)。各图原点不同、朝向可能不同,直接堆叠
就会互相错位——这正是「多张图纸拼接位置错误」的根子。

**怎么摆正**:人在图上标的交叉点带工程坐标 XYZ,于是有了成对的
「本图米坐标 ↔ 工程坐标」,**≥2 对**即可解出相似变换(见 `drawing_anchor`),
把整张图的构件一次性搬到工程坐标系。

**坐标空间必须一致**(这是最容易错的地方):交叉点存的是**归一化页面坐标**,
而构件是**本图米坐标**。所以解算前必须先把交叉点用同一个 `drawing_transform`
换算到米,否则解出来的变换作用在构件上是错的。

无解时**保持原样**(不猜),并在 provenance 里标明该图未定位。
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

#: 构件里可能承载坐标的键(与 elementsBuilder / model_elements 保持一致)
GEOMETRY_KEYS = ("outline", "path", "points")


def intersections_to_meter(
    points: list[dict], transform: Any,
) -> list[dict]:
    """交叉点(归一化页面坐标)→ 本图米坐标,保留其工程坐标。

    归一化坐标是「同除页高」,故先乘 page_h 还原成点,再走 pt_to_meter,
    与构件坐标口径完全一致。
    """
    from services.drawing_transform import pt_to_meter

    page_h = float(getattr(transform, "page_h", 0) or 0)
    if page_h <= 0:
        return []
    out: list[dict] = []
    for p in points:
        if p.get("world_x") is None or p.get("world_y") is None:
            continue
        x_pt = float(p["x_norm"]) * page_h
        y_pt = float(p["y_norm"]) * page_h
        x_m, y_m = pt_to_meter(x_pt, y_pt, transform)
        out.append({
            "x_norm": x_m, "y_norm": y_m,          # 复用同名键喂给求解器
            "world_x": p["world_x"], "world_y": p["world_y"],
            "world_z": p.get("world_z"),
            "label_x": p.get("label_x"), "label_y": p.get("label_y"),
        })
    return out


def solve_placement(points: list[dict], transform: Any) -> dict | None:
    """该图的「本图米坐标 → 工程坐标」相似变换;点不足/无变换 → None。"""
    from services.drawing_anchor import solve_world_transform

    meter_points = intersections_to_meter(points, transform)
    return solve_world_transform(meter_points)


def place_point(
    x: float, y: float, placement: dict,
) -> tuple[float, float]:
    from services.drawing_anchor import apply_similarity
    return apply_similarity((x, y), placement)


def place_element(element: dict, placement: dict) -> dict:
    """把一个构件的所有坐标搬到工程坐标系(返回新对象,不改原值)。"""
    out = dict(element)
    for key in GEOMETRY_KEYS:
        pts = element.get(key)
        if not isinstance(pts, list) or not pts:
            continue
        moved = []
        for p in pts:
            if isinstance(p, (list, tuple)) and len(p) >= 2:
                x, y = place_point(float(p[0]), float(p[1]), placement)
                moved.append([round(x, 3), round(y, 3), *list(p)[2:]])
            else:
                moved.append(p)
        out[key] = moved
    out["placed"] = True                 # provenance:这个构件已按工程坐标摆放
    return out


def place_elements(
    elements: dict[str, list], placement: dict | None,
) -> dict[str, list]:
    """整层构件按工程坐标摆放。placement 为 None 时**原样返回**(不猜)。"""
    if not placement:
        return elements
    return {
        kind: [place_element(e, placement) for e in items]
        for kind, items in elements.items()
    }


async def placements_for_project(
    db: Any, project_id: str, transforms: dict,
) -> dict[str, dict]:
    """全项目每图的摆放变换 {drawing_id: placement}。

    只对「有 drawing_transform + 有 ≥2 个带工程坐标交叉点」的图求解;
    其余图不在结果里,建模时保持原样(诚实降级,不是静默套一个错变换)。
    """
    from services.axis_intersection_repo import fetch_project_intersections

    by_drawing = await fetch_project_intersections(db, project_id)
    out: dict[str, dict] = {}
    for drawing_id, points in by_drawing.items():
        transform = transforms.get(drawing_id)
        if transform is None:
            continue
        placement = solve_placement(points, transform)
        if placement is None:
            continue
        if placement.get("suspect"):
            # 残差过大 = 交叉点配错或轴号重名。宁可不摆也不摆错位置。
            logger.warning(
                "[placement] 图 %s 残差 %.3fm 过大,跳过摆放",
                drawing_id, placement.get("rmse_m", 0))
            continue
        out[drawing_id] = placement
    return out
