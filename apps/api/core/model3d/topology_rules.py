"""构件拓扑规则（B-12/B-13/B-14，确定性纯几何，无 shapely 依赖）。

坐标均为米（平面 xy）。三条从属/支承规则供 IFC 扣减与几何一致性检查：
- B-12 门窗-墙：洞口中心落在墙中线邻域 → host=墙；跨墙/悬空标 orphan。
- B-13 梁-柱：梁端点落在柱截面（含近邻自适应阈值）→ 支承于柱；悬挑端不连。
- B-14 板-梁：板边与梁轴线对齐（带宽阈值内）→ 板托承于梁；无梁降级。

几何判定用平面数学（点-线距离、点-多边形包含、线段对齐），避免引入重型 CV 依赖。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

# 阈值（米）
_OPENING_HOST_TOL_M = 0.3        # 洞口中心到墙中线的额外容差（叠加墙半宽）
_BEAM_SUPPORT_BASE_TOL_M = 0.15  # 梁端-柱截面近邻基础阈值
_BEAM_SUPPORT_DIAG_RATIO = 0.5   # 近邻阈值随柱对角自适应比例
_SLAB_BEAM_BAND_M = 0.3          # 板边-梁轴线对齐带宽
_PARALLEL_SIN_TOL = 0.2          # 平行判据 |sinθ| 上限


@dataclass(frozen=True)
class HostRel:
    """洞口-墙从属。orphan=True 时 wall_id 为 None（跨墙/悬空，未硬塞）。"""
    opening_id: str
    wall_id: str | None
    confidence: float
    orphan: bool


@dataclass(frozen=True)
class BeamSupport:
    """梁-柱支承（按梁端）。end ∈ start|end。"""
    beam_id: str
    column_id: str
    end: str
    confidence: float


@dataclass(frozen=True)
class SlabSupport:
    """板-梁托承。beam_ids 为空 → 悬挑/无梁楼盖降级。"""
    slab_id: str
    beam_ids: tuple[str, ...]
    confidence: float


# ── B-12 门窗-墙从属 ───────────────────────────────────────────

def resolve_opening_host(openings: list[dict], walls: list[dict]) -> list[HostRel]:
    rels: list[HostRel] = []
    for index, opening in enumerate(openings or []):
        opening_id = str(opening.get("id") or f"opening_{index}")
        center = _element_center(opening)
        if center is None:
            rels.append(HostRel(opening_id, None, 0.2, True))
            continue
        matches = _matching_walls(center, walls)
        if not matches:
            rels.append(HostRel(opening_id, None, 0.2, True))
        elif len(matches) == 1:
            rels.append(HostRel(opening_id, matches[0][0], 0.95, False))
        else:
            nearest = min(matches, key=lambda item: item[1])
            rels.append(HostRel(opening_id, nearest[0], 0.7, False))
    return rels


def _matching_walls(center: tuple[float, float], walls: list[dict]) -> list[tuple[str, float]]:
    matches: list[tuple[str, float]] = []
    for index, wall in enumerate(walls or []):
        seg = _path_segment(wall)
        if seg is None:
            continue
        dist, t = _point_segment(center, seg)
        half_width = float(wall.get("width") or 0.2) / 2.0
        if dist <= half_width + _OPENING_HOST_TOL_M and -0.05 <= t <= 1.05:
            matches.append((str(wall.get("id") or f"wall_{index}"), dist))
    return matches


# ── B-13 梁-柱支承 ─────────────────────────────────────────────

def resolve_beam_support(beams: list[dict], columns: list[dict]) -> list[BeamSupport]:
    rels: list[BeamSupport] = []
    column_boxes = [
        (str(col.get("id") or f"column_{i}"), _polygon_bbox(col.get("outline")))
        for i, col in enumerate(columns or [])
    ]
    column_boxes = [(cid, box) for cid, box in column_boxes if box is not None]

    for index, beam in enumerate(beams or []):
        seg = _path_segment(beam)
        if seg is None:
            continue
        beam_id = str(beam.get("id") or f"beam_{index}")
        for end_name, point in (("start", seg[0]), ("end", seg[1])):
            support = _column_at(point, column_boxes)
            if support is not None:
                column_id, contained = support
                rels.append(
                    BeamSupport(beam_id, column_id, end_name, 0.9 if contained else 0.7)
                )
    return rels


def _column_at(
    point: tuple[float, float],
    column_boxes: list[tuple[str, tuple[float, float, float, float]]],
) -> tuple[str, bool] | None:
    best: tuple[str, bool, float] | None = None
    for column_id, box in column_boxes:
        margin = _adaptive_margin(box)
        if not _point_in_bbox(point, box, margin):
            continue
        contained = _point_in_bbox(point, box, 0.0)
        cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        dist = math.hypot(point[0] - cx, point[1] - cy)
        if best is None or dist < best[2]:
            best = (column_id, contained, dist)
    if best is None:
        return None
    return best[0], best[1]


def _adaptive_margin(box: tuple[float, float, float, float]) -> float:
    diag = math.hypot(box[2] - box[0], box[3] - box[1])
    return _BEAM_SUPPORT_BASE_TOL_M + _BEAM_SUPPORT_DIAG_RATIO * diag


# ── B-14 板-梁托承 ─────────────────────────────────────────────

def resolve_slab_support(slabs: list[dict], beams: list[dict]) -> list[SlabSupport]:
    beam_segs = [
        (str(beam.get("id") or f"beam_{i}"), _path_segment(beam))
        for i, beam in enumerate(beams or [])
    ]
    beam_segs = [(bid, seg) for bid, seg in beam_segs if seg is not None]

    rels: list[SlabSupport] = []
    for index, slab in enumerate(slabs or []):
        slab_id = str(slab.get("id") or f"slab_{index}")
        edges = _polygon_edges(slab.get("outline"))
        supporting = [
            bid for bid, seg in beam_segs
            if any(_segments_aligned(seg, edge) for edge in edges)
        ]
        confidence = 0.9 if supporting else 0.3
        rels.append(SlabSupport(slab_id, tuple(supporting), confidence))
    return rels


def _segments_aligned(a: tuple, b: tuple) -> bool:
    """两线段平行且 a 的中点落在 b 的带宽内、投影在 b 跨度内。"""
    if not _parallel(a, b):
        return False
    mid = ((a[0][0] + a[1][0]) / 2, (a[0][1] + a[1][1]) / 2)
    dist, t = _point_segment(mid, b)
    return dist <= _SLAB_BEAM_BAND_M and -0.05 <= t <= 1.05


def _parallel(a: tuple, b: tuple) -> bool:
    ax, ay = a[1][0] - a[0][0], a[1][1] - a[0][1]
    bx, by = b[1][0] - b[0][0], b[1][1] - b[0][1]
    la, lb = math.hypot(ax, ay), math.hypot(bx, by)
    if la == 0 or lb == 0:
        return False
    cross = abs(ax * by - ay * bx) / (la * lb)
    return cross <= _PARALLEL_SIN_TOL


# ── 几何原语 ────────────────────────────────────────────────────

def _path_segment(element: dict) -> tuple[tuple[float, float], tuple[float, float]] | None:
    path = element.get("path")
    if not path or len(path) < 2:
        return None
    return (float(path[0][0]), float(path[0][1])), (float(path[-1][0]), float(path[-1][1]))


def _element_center(element: dict) -> tuple[float, float] | None:
    center = element.get("center")
    if center and len(center) >= 2:
        return float(center[0]), float(center[1])
    outline = element.get("outline")
    if outline:
        xs = [float(p[0]) for p in outline]
        ys = [float(p[1]) for p in outline]
        if xs and ys:
            return sum(xs) / len(xs), sum(ys) / len(ys)
    return None


def _polygon_bbox(outline) -> tuple[float, float, float, float] | None:
    if not outline:
        return None
    xs = [float(p[0]) for p in outline]
    ys = [float(p[1]) for p in outline]
    return min(xs), min(ys), max(xs), max(ys)


def _polygon_edges(outline) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    if not outline or len(outline) < 2:
        return []
    points = [(float(p[0]), float(p[1])) for p in outline]
    return [(points[i], points[(i + 1) % len(points)]) for i in range(len(points))]


def _point_in_bbox(point: tuple[float, float], box: tuple[float, float, float, float], margin: float) -> bool:
    return (
        box[0] - margin <= point[0] <= box[2] + margin
        and box[1] - margin <= point[1] <= box[3] + margin
    )


def _point_segment(point: tuple[float, float], seg: tuple) -> tuple[float, float]:
    """点到线段距离 + 投影参数 t（0~1 为段内）。"""
    px, py = point
    ax, ay = seg[0]
    bx, by = seg[1]
    dx, dy = bx - ax, by - ay
    denom = dx * dx + dy * dy
    if denom == 0:
        return math.hypot(px - ax, py - ay), 0.0
    t = ((px - ax) * dx + (py - ay) * dy) / denom
    tc = max(0.0, min(1.0, t))
    proj_x, proj_y = ax + tc * dx, ay + tc * dy
    return math.hypot(px - proj_x, py - proj_y), t
