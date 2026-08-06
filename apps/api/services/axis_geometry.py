"""轴线几何:斜向轴线、交叉点、过点生成轴线。

原先轴线只认横平竖直(`manual_axis.axis_position` 对斜线返回 None)。实际工程图里
斜向轴线很常见(放射状布置、异形平面、扇形柱网),不能一律判为无效。

本模块用**角度**统一描述轴线,不再假定轴对齐:
- `orientation()` —— 竖向 / 横向 / 斜向三分,斜向也是合法轴线;
- `line_through_point()` —— 给定点 + 角度,生成一条贯穿整图的轴线(选点定轴用);
- `intersect()` —— 两条轴线交点,是「交叉点定位」的基础。

坐标口径与全链路一致:归一化页面坐标(同除页高),y 向下为正。
"""
from __future__ import annotations

import math

#: 与坐标轴的夹角小于此值即视为轴对齐(度)。3° 容差:人手描/OCR 都有抖动,
#: 但真正的斜向轴线通常远超 3°。
AXIS_ALIGN_TOLERANCE_DEG = 3.0

#: 生成的贯穿线半长(归一化):足够覆盖整页(页高为 1,宽可能到 1.5)
_SPAN_HALF = 2.0

#: 两线接近平行时不求交点(夹角小于此值)
_PARALLEL_TOLERANCE_DEG = 0.5


def line_angle_deg(ref: dict) -> float:
    """轴线与 x 轴的夹角(度),归一到 [0,180)。竖线 90°,横线 0°。"""
    dx = float(ref["x2_norm"]) - float(ref["x1_norm"])
    dy = float(ref["y2_norm"]) - float(ref["y1_norm"])
    if dx == 0 and dy == 0:
        return 0.0
    return math.degrees(math.atan2(dy, dx)) % 180.0


def orientation(ref: dict, tol_deg: float = AXIS_ALIGN_TOLERANCE_DEG) -> str:
    """轴线朝向 → 'x'(竖向)| 'y'(横向)| 'skew'(斜向,同样是合法轴线)。"""
    angle = line_angle_deg(ref)
    if abs(angle - 90.0) <= tol_deg:
        return "x"
    if angle <= tol_deg or angle >= 180.0 - tol_deg:
        return "y"
    return "skew"


def line_through_point(
    x: float, y: float, angle_deg: float, half_span: float = _SPAN_HALF,
) -> dict:
    """过点 (x,y) 按给定角度生成一条贯穿整图的轴线端点。

    「在图上点一个点,直接生成竖向 + 横向轴线」就靠这个:
    竖向传 90、横向传 0,斜向传实际角度。
    """
    rad = math.radians(angle_deg)
    dx, dy = math.cos(rad) * half_span, math.sin(rad) * half_span
    return {
        "x1_norm": round(x - dx, 6), "y1_norm": round(y - dy, 6),
        "x2_norm": round(x + dx, 6), "y2_norm": round(y + dy, 6),
    }


def intersect(a: dict, b: dict) -> tuple[float, float] | None:
    """两条轴线(按无限长直线)的交点;接近平行 → None。"""
    ax1, ay1 = float(a["x1_norm"]), float(a["y1_norm"])
    ax2, ay2 = float(a["x2_norm"]), float(a["y2_norm"])
    bx1, by1 = float(b["x1_norm"]), float(b["y1_norm"])
    bx2, by2 = float(b["x2_norm"]), float(b["y2_norm"])

    diff = abs(line_angle_deg(a) - line_angle_deg(b)) % 180.0
    if min(diff, 180.0 - diff) < _PARALLEL_TOLERANCE_DEG:
        return None

    r_x, r_y = ax2 - ax1, ay2 - ay1
    s_x, s_y = bx2 - bx1, by2 - by1
    denom = r_x * s_y - r_y * s_x
    if abs(denom) < 1e-12:
        return None
    t = ((bx1 - ax1) * s_y - (by1 - ay1) * s_x) / denom
    return (round(ax1 + t * r_x, 6), round(ay1 + t * r_y, 6))


def translate(ref: dict, dx: float, dy: float) -> dict:
    """平移一条轴线(返回新对象,不改原值)——人工微调轴线位置用。"""
    return {
        **ref,
        "x1_norm": round(float(ref["x1_norm"]) + dx, 6),
        "y1_norm": round(float(ref["y1_norm"]) + dy, 6),
        "x2_norm": round(float(ref["x2_norm"]) + dx, 6),
        "y2_norm": round(float(ref["y2_norm"]) + dy, 6),
    }


def move_to(ref: dict, x: float, y: float) -> dict:
    """把轴线平移到穿过 (x,y),保持角度不变。

    拖动轴线时用:算出的是「拖到哪」,而不是「拖了多远」,避免累积误差。
    """
    cx = (float(ref["x1_norm"]) + float(ref["x2_norm"])) / 2
    cy = (float(ref["y1_norm"]) + float(ref["y2_norm"])) / 2
    return translate(ref, x - cx, y - cy)


def axis_offset(ref: dict) -> float:
    """轴线到原点的有向距离(法线式),用于同向轴线排序——斜向也适用。

    竖向轴线退化为 x 坐标、横向退化为 y 坐标,与既有 `axis_position` 一致。
    """
    # 公式的唯一实现在 core.model3d.axis_normal —— 它的符号曾错过一次
    # (用 (sinθ,cosθ) 而非 (-sinθ,cosθ)),当时要同时改三处才修干净。
    # 偏移带符号,仅在同方向内可比。
    from core.model3d.axis_normal import normal_offset

    return round(normal_offset(float(ref["x1_norm"]), float(ref["y1_norm"]),
                               line_angle_deg(ref)), 6)
