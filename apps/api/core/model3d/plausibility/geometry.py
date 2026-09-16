"""合理性分析的几何核 —— 只放**纯数学**，不含任何建筑语义。

**复用优先**：多边形面积、自交判定、环修复在 `core.model3d.ring_order`，
凸包与最小面积外接矩形在 `core.model3d.true_extent`。这里只做转发与补齐，
**不重写** —— 面积公式此前在三处各写一遍，改一处就漂移，是本仓库付过的学费。

新增的几件（这一层才需要）：
- `polygon_overlap_area`：两多边形的相交面积（判构件互相穿模、重复计数）
- `point_in_polygon`：射线法（判支承关系「上层构件是否落在下层构件上」）
- `thick_segment_ring`：线状构件按宽度加厚成矩形（墙梁管才有占地）
- `solidity`：面积 ÷ 凸包面积（判轮廓是否被撕成异形）
"""
from __future__ import annotations

import math

from core.model3d.ring_order import is_self_intersecting as _is_self_intersecting
from core.model3d.ring_order import polygon_area as _signed_area
from core.model3d.true_extent import convex_hull as _convex_hull
from core.model3d.true_extent import min_area_rect as _min_area_rect

Point = tuple[float, float]
Ring = list[Point]


def polygon_area(ring: Ring) -> float:
    """多边形面积（绝对值，平方米）。鞋带公式，见 `ring_order.polygon_area`。"""
    return abs(_signed_area(list(ring)))


def signed_area(ring: Ring) -> float:
    """带符号面积。正 = 逆时针。**自交环的正负会相消**，这正是判据之一。"""
    return _signed_area(list(ring))


def is_self_intersecting(ring: Ring) -> bool:
    """环是否自交（「蝴蝶结」）。"""
    return _is_self_intersecting(list(ring))


def convex_hull(points: list) -> list:
    return _convex_hull(list(points))


def min_area_rect(points: list) -> tuple[float, float] | None:
    """→（长边, 短边）。退化返回 None。"""
    return _min_area_rect(list(points))


def bbox(points: list) -> tuple[float, float, float, float] | None:
    """轴对齐包围盒 (x0, y0, x1, y1)。"""
    pts = [p for p in points if len(p) >= 2]
    if not pts:
        return None
    xs = [float(p[0]) for p in pts]
    ys = [float(p[1]) for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def polyline_length(points: list) -> float:
    """折线长度（米）。"""
    total = 0.0
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        total += math.hypot(float(x1) - float(x0), float(y1) - float(y0))
    return total


def centroid(ring: Ring) -> Point | None:
    """多边形形心（面积加权）。面积为零时退回顶点平均。"""
    pts = [(float(p[0]), float(p[1])) for p in ring if len(p) >= 2]
    if len(pts) < 3:
        return None
    a = _signed_area(pts)
    if abs(a) < 1e-12:
        return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))
    cx = cy = 0.0
    for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1]):
        cross = x0 * y1 - x1 * y0
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
    return (cx / (6 * a), cy / (6 * a))


def solidity(ring: Ring) -> float | None:
    """面积 ÷ 凸包面积 ∈ (0, 1]。凸多边形为 1；越小说明轮廓越「破」。"""
    pts = [(float(p[0]), float(p[1])) for p in ring if len(p) >= 2]
    if len(pts) < 3:
        return None
    hull = _convex_hull(pts)
    hull_area = abs(_signed_area(list(hull))) if len(hull) >= 3 else 0.0
    if hull_area <= 0:
        return None
    return polygon_area(pts) / hull_area


def point_in_polygon(point: Point, ring: Ring) -> bool:
    """射线法。边界上算在内（支承判定宁可偏保守，不去否定贴边的支承）。"""
    x, y = float(point[0]), float(point[1])
    pts = [(float(p[0]), float(p[1])) for p in ring if len(p) >= 2]
    if len(pts) < 3:
        return False
    inside = False
    for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1]):
        if min(y0, y1) - 1e-12 <= y <= max(y0, y1) + 1e-12 and abs(y1 - y0) > 1e-12:
            xx = x0 + (y - y0) * (x1 - x0) / (y1 - y0)
            if abs(xx - x) <= 1e-9:
                return True                      # 落在边上
            if xx > x:
                inside = not inside
    return inside


def thick_segment_ring(a: Point, b: Point, width_m: float) -> Ring:
    """线段按宽度加厚成矩形环（墙、梁、管线的占地）。"""
    (x0, y0), (x1, y1) = (float(a[0]), float(a[1])), (float(b[0]), float(b[1]))
    dx, dy = x1 - x0, y1 - y0
    length = math.hypot(dx, dy)
    if length <= 0:
        return []
    half = max(float(width_m), 0.0) / 2.0
    nx, ny = -dy / length * half, dx / length * half
    return [(x0 + nx, y0 + ny), (x1 + nx, y1 + ny), (x1 - nx, y1 - ny), (x0 - nx, y0 - ny)]


def _clip(subject: Ring, clipper: Ring) -> Ring:
    """Sutherland–Hodgman：用**凸**多边形 `clipper` 裁 `subject`。"""
    output = list(subject)
    for (cx0, cy0), (cx1, cy1) in zip(clipper, clipper[1:] + clipper[:1]):
        if not output:
            return []
        buf, prev = [], output[-1]

        def side(p, x0=cx0, y0=cy0, x1=cx1, y1=cy1):
            return (x1 - x0) * (p[1] - y0) - (y1 - y0) * (p[0] - x0)

        for current in output:
            s_cur, s_prev = side(current), side(prev)
            if s_cur >= 0:
                if s_prev < 0:
                    buf.append(_edge_point(prev, current, (cx0, cy0), (cx1, cy1)))
                buf.append(current)
            elif s_prev >= 0:
                buf.append(_edge_point(prev, current, (cx0, cy0), (cx1, cy1)))
            prev = current
        output = buf
    return output


def _edge_point(p0: Point, p1: Point, a: Point, b: Point) -> Point:
    """线段 p0p1 与直线 ab 的交点。"""
    x1, y1, x2, y2 = p0[0], p0[1], p1[0], p1[1]
    x3, y3, x4, y4 = a[0], a[1], b[0], b[1]
    den = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if abs(den) < 1e-12:
        return p1
    t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / den
    return (x1 + t * (x2 - x1), y1 + t * (y2 - y1))


def polygon_overlap_area(ring_a: Ring, ring_b: Ring) -> float:
    """两多边形的相交面积（平方米）。

    **按凸包裁剪**：Sutherland–Hodgman 只对凸裁剪多边形成立，而构件轮廓
    偶有凹形与自交。用凸包会**高估**重叠 —— 这一层的判据是「重叠大到不可能」，
    高估会让结论偏保守（更容易报），所以宁可如此，也不引入一个几何库。
    调用方据此把阈值定在明显的量级上，不要拿它做精确体积。
    """
    a = _convex_hull([(float(p[0]), float(p[1])) for p in ring_a if len(p) >= 2])
    b = _convex_hull([(float(p[0]), float(p[1])) for p in ring_b if len(p) >= 2])
    if len(a) < 3 or len(b) < 3:
        return 0.0
    if _signed_area(list(a)) < 0:
        a = list(reversed(a))
    if _signed_area(list(b)) < 0:
        b = list(reversed(b))
    clipped = _clip(list(a), list(b))
    return polygon_area(clipped) if len(clipped) >= 3 else 0.0


def boxes_overlap(box_a, box_b) -> bool:
    """包围盒粗筛 —— 精确相交前先用它，O(n²) 的两两比较全靠它压住常数。"""
    return not (box_a[2] < box_b[0] or box_b[2] < box_a[0]
                or box_a[3] < box_b[1] or box_b[3] < box_a[1])
