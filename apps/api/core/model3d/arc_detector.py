"""圆弧检测:把**真正的弧**从碎段里分离出来。

**为什么要单独做**:外轮廓(实测是右侧一条平滑长弧)与同心圆定心,卡在同一个
子问题上——A-01-03A 有 8 万余条碎段,里面既有同心弧,也有大量文字笔画和
微弯的直线。此前两次失败:

1. 「顶点到中心等距」→ 全命中**离中心远的短线段**(半径 2000pt 时 2% 容差
   就是 40pt,天然满足);
2. 「连续三点外接圆心一致」用**绝对**容差 20pt → 半径 5pt 的文字笔画必然通过,
   中位数被噪声主导,定心偏 **373pt**。

两次是**同一类错**:尺度相关的量用了绝对容差。所以这里的一致性判据是
**`圆心离散度 ÷ 半径`**,而不是圆心离散度本身;并且额外要求:

- **转向同号**(弧一路往一边弯;文字笔画忽左忽右);
- **扫掠角**够大(一小段的圆心极不稳定);
- 顶点数够多。
"""
from __future__ import annotations

import math

#: 圆心离散度相对半径的上限。**必须是比值**——绝对容差会放行小半径噪声
CENTER_SPREAD_RATIO = 0.05

#: 构成一段弧所需的最少顶点
MIN_ARC_POINTS = 6

#: 最小扫掠角(度)。太平的一小段定不出稳定圆心
MIN_SWEEP_DEG = 5.0

#: 相邻顶点去重距离(pt)
MIN_VERTEX_GAP_PT = 0.5

#: 转向同号的最低比例。留一点余量给数值抖动
MIN_TURN_CONSISTENCY = 0.9

#: 同心族判定:各弧圆心到中位圆心的距离上限 = 中位半径 × 该比例
CONCENTRIC_TOLERANCE_RATIO = 0.05

#: 构成同心族的最少弧数
MIN_CONCENTRIC_ARCS = 3


def circumcenter(a: tuple[float, float], b: tuple[float, float],
                 c: tuple[float, float]) -> tuple[float, float] | None:
    """三点外接圆心;共线返回 None。"""
    d = 2 * (a[0] * (b[1] - c[1]) + b[0] * (c[1] - a[1]) + c[0] * (a[1] - b[1]))
    if abs(d) < 1e-9:
        return None
    sa, sb, sc = (a[0] ** 2 + a[1] ** 2, b[0] ** 2 + b[1] ** 2,
                  c[0] ** 2 + c[1] ** 2)
    return ((sa * (b[1] - c[1]) + sb * (c[1] - a[1]) + sc * (a[1] - b[1])) / d,
            (sa * (c[0] - b[0]) + sb * (a[0] - c[0]) + sc * (b[0] - a[0])) / d)


def _dedupe(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    if not points:
        return []
    out = [tuple(points[0])]
    for p in points[1:]:
        if math.dist(p, out[-1]) > MIN_VERTEX_GAP_PT:
            out.append(tuple(p))
    return out


#: 拟合前抽稀成多少个采样点。
#: **不抽稀就拟合不出大半径弧**:半径 900pt 的弧上相邻 2pt 碎段的真实转角只有
#: 0.13°,而坐标精度是 0.1pt —— 噪声完全淹没曲率,转向符号随机翻转、
#: 三点外接圆心剧烈抖动。实测 83401 条碎段追出 1116 段的长链,却一条弧都拟合不出。
#: 抽稀后弦长拉开,曲率信号才盖过噪声。
RESAMPLE_POINTS = 24


def _resample(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """按索引均匀抽稀到 RESAMPLE_POINTS 个点(点数本就不多则原样返回)。"""
    if len(points) <= RESAMPLE_POINTS:
        return points
    step = (len(points) - 1) / (RESAMPLE_POINTS - 1)
    return [points[round(i * step)] for i in range(RESAMPLE_POINTS)]


def _turn_consistency(points: list[tuple[float, float]]) -> float:
    """转向同号的比例。弧一路往一边弯;文字笔画忽左忽右。"""
    signs = []
    for i in range(len(points) - 2):
        (ax, ay), (bx, by), (cx, cy) = points[i], points[i + 1], points[i + 2]
        cross = (bx - ax) * (cy - by) - (by - ay) * (cx - bx)
        if abs(cross) > 1e-9:
            signs.append(1 if cross > 0 else -1)
    if not signs:
        return 0.0
    return max(signs.count(1), signs.count(-1)) / len(signs)


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[len(ordered) // 2]


def fit_arc(points: list[tuple[float, float]]) -> dict | None:
    """一条折线 → 圆弧(不是弧则返回 None)。

    判据:顶点够多 + 转向同号 + **圆心离散度 ÷ 半径**够小 + 扫掠角够大。
    """
    pts = _resample(_dedupe(list(points or [])))
    if len(pts) < MIN_ARC_POINTS:
        return None
    if _turn_consistency(pts) < MIN_TURN_CONSISTENCY:
        return None                       # 忽左忽右 → 不是弧

    centers = []
    for i in range(len(pts) - 2):
        got = circumcenter(pts[i], pts[i + 1], pts[i + 2])
        if got is not None:
            centers.append(got)
    if len(centers) < 2:
        return None                       # 近乎共线 → 直线

    cx = _median([c[0] for c in centers])
    cy = _median([c[1] for c in centers])
    radius = _median([math.dist(p, (cx, cy)) for p in pts])
    if radius <= 0:
        return None
    spread = _median([math.dist(c, (cx, cy)) for c in centers])
    if spread / radius > CENTER_SPREAD_RATIO:
        return None                       # **相对**离散度过大 → 不是同一段弧

    angles = [math.degrees(math.atan2(p[1] - cy, p[0] - cx)) % 360.0 for p in pts]
    sweep = _sweep(angles)
    if sweep < MIN_SWEEP_DEG:
        return None                       # 太短的一小段,圆心不可信

    return {
        "center": (round(cx, 3), round(cy, 3)),
        "radius": round(radius, 3),
        "sweep_deg": round(sweep, 2),
        "points": len(pts),
        "spread_ratio": round(spread / radius, 5),
    }


def _sweep(angles: list[float]) -> float:
    """一组角度覆盖的扫掠角(度),跨 0 也对。"""
    ordered = sorted(angles)
    gaps = [ordered[i + 1] - ordered[i] for i in range(len(ordered) - 1)]
    gaps.append(360.0 - ordered[-1] + ordered[0])
    return 360.0 - max(gaps)


def detect_arcs(paths: list[list[tuple[float, float]]],
                min_radius: float = 0.0) -> list[dict]:
    """多条折线 → 弧列表(不改入参)。`min_radius` 用于滤掉文字笔画级小弧。"""
    out = []
    for path in paths or []:
        arc = fit_arc(path)
        if arc and arc["radius"] >= min_radius:
            out.append(arc)
    return out


def concentric_center(arcs: list[dict]) -> dict | None:
    """一组弧 → 同心族的公共圆心;不成族返回 None(**不取平均硬给一个**)。

    容差按**中位半径**相对化,与 `fit_arc` 同一条原则。
    """
    if len(arcs) < MIN_CONCENTRIC_ARCS:
        return None
    cx = _median([a["center"][0] for a in arcs])
    cy = _median([a["center"][1] for a in arcs])
    radius = _median([a["radius"] for a in arcs])
    if radius <= 0:
        return None
    tol = radius * CONCENTRIC_TOLERANCE_RATIO
    members = [a for a in arcs if math.dist(a["center"], (cx, cy)) <= tol]
    if len(members) < MIN_CONCENTRIC_ARCS:
        return None
    # 只用成员重算一次,去掉离群弧的拉扯
    fx = _median([a["center"][0] for a in members])
    fy = _median([a["center"][1] for a in members])
    return {
        "center": (round(fx, 3), round(fy, 3)),
        "arcs": len(members),
        "radii": sorted(round(a["radius"], 2) for a in members),
    }


# ── 跨 path 平滑追链 ──────────────────────────────────────────────

#: 追链时允许的最大转角(度)。弧的相邻段几乎同向;轴线以大角度穿过
MAX_TURN_DEG = 12.0

#: 端点视为相接的容差(pt)
JOIN_TOLERANCE_PT = 1.2

#: 端点哈希网格边长(pt)
_GRID_PT = 3.0


def _seg_ends(seg):
    return (seg[0], seg[1]), (seg[2], seg[3])


def _direction(seg, from_point):
    """从 from_point 出发沿该段的单位方向,以及另一端点。"""
    a, b = _seg_ends(seg)
    far = b if math.dist(from_point, a) <= math.dist(from_point, b) else a
    dx, dy = far[0] - from_point[0], far[1] - from_point[1]
    length = math.hypot(dx, dy)
    if length < 1e-9:
        return None, far
    return (dx / length, dy / length), far


def trace_smooth_chains(segments: list[tuple], max_turn: float = MAX_TURN_DEG,
                        join_tol: float = JOIN_TOLERANCE_PT) -> list[list[tuple]]:
    """碎段 → **平滑延续**的链(不改入参)。

    **这是此前失败的关键修复**:直接按连通性追踪会在交叉点串进整个轴网
    (实测 A-01-03A 出现 53100 段的巨链)。改为每步只接受**转角最小且
    小于阈值**的延续——轴线以大角度穿过弧,永远不会被选中。
    """
    if not segments:
        return []
    buckets: dict[tuple[int, int], list[int]] = {}
    for idx, seg in enumerate(segments):
        for point in _seg_ends(seg):
            key = (round(point[0] / _GRID_PT), round(point[1] / _GRID_PT))
            buckets.setdefault(key, []).append(idx)

    def neighbours(point):
        gx, gy = round(point[0] / _GRID_PT), round(point[1] / _GRID_PT)
        found = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for idx in buckets.get((gx + dx, gy + dy), ()):
                    a, b = _seg_ends(segments[idx])
                    if min(math.dist(point, a), math.dist(point, b)) <= join_tol:
                        found.append(idx)
        return found

    used: set[int] = set()
    chains: list[list[tuple]] = []
    for start in range(len(segments)):
        if start in used:
            continue
        used.add(start)
        forward = _grow_from(segments, neighbours, used, start, 1,
                             max_turn)
        backward = _grow_from(segments, neighbours, used, start, 0,
                              max_turn)
        chains.append(list(reversed(backward)) + [segments[start]] + forward)
    return chains


def _grow_from(segments, neighbours, used, start, end_index, max_turn):
    """从起始段的某一端向外反复选取转角最小的延续。"""
    seg = segments[start]
    other = _seg_ends(seg)[1 - end_index]
    point = _seg_ends(seg)[end_index]
    dx, dy = point[0] - other[0], point[1] - other[1]
    length = math.hypot(dx, dy)
    if length < 1e-9:
        return []
    direction = (dx / length, dy / length)

    grown: list[tuple] = []
    while True:
        best = None
        for idx in neighbours(point):
            if idx in used:
                continue
            candidate, far = _direction(segments[idx], point)
            if candidate is None:
                continue
            dot = max(-1.0, min(1.0, candidate[0] * direction[0]
                                + candidate[1] * direction[1]))
            turn = math.degrees(math.acos(dot))
            if turn > max_turn:
                continue
            if best is None or turn < best[0]:
                best = (turn, idx, candidate, far)
        if best is None:
            return grown
        _turn, idx, candidate, far = best
        used.add(idx)
        grown.append(segments[idx])
        point, direction = far, candidate


def chain_points(chain: list[tuple]) -> list[tuple[float, float]]:
    """链 → 顶点序列(供 `fit_arc` 使用)。"""
    points: list[tuple[float, float]] = []
    for seg in chain:
        a, b = _seg_ends(seg)
        if not points:
            points.extend([a, b])
            continue
        if math.dist(points[-1], b) < math.dist(points[-1], a):
            a, b = b, a
        points.append(b)
    return points
