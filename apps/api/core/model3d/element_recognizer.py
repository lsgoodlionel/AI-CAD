"""构件识别：几何原语 → 柱/墙/梁/板/管线/设备（纯确定性启发式，无 LLM）。

坐标处理：先做 y 翻转（PDF 页面坐标 y 向下），再平移到轴网原点，最后按比例尺换算为米。
识别失败/超限均优雅降级，绝不抛异常。
"""
from __future__ import annotations

import logging
import re

from .geometry_extractor import MAX_PRIMITIVES
from .types import DrawingGeometry, FloorElements

logger = logging.getLogger(__name__)

# 1:100 下 1 页面点对应的米数（1pt = 0.352778mm 纸面 → ×100 实际）
SCALE_1_100_M_PER_PT = 100 * 0.000352778
_DEFAULT_SCALE = SCALE_1_100_M_PER_PT
_STANDARD_AXIS_SPACING_M = 8.4        # 常见柱网轴距（比例反推用）
_DXF_MODEL_SPACE_THRESHOLD = 5000.0   # 页宽超此值视为 DXF 毫米模型空间
_SCALE_RE = re.compile(r"1[:：]\s*(50|100|150|200|500)")

# 构件尺寸阈值（米）
_COLUMN_SIZE = (0.2, 1.5)
_COLUMN_MAX_ASPECT = 4.0
_WALL_GAP = (0.1, 0.4)
_BEAM_GAP = (0.15, 0.5)
_PAIR_MIN_OVERLAP_M = 1.0
_PIPE_MIN_LEN_M = 3.0
_EQUIPMENT_SIZE = (0.5, 5.0)
_SLAB_MIN_AREA_M2 = 10.0
_AXIS_MIN_RATIO = 0.6                 # 轴线长度 ≥60% 页幅
_LINE_STRAIGHT_TOL_PT = 2.0

# 输出上限（防爆场景）
_CAPS = {"columns": 2000, "walls": 2000, "beams": 2000, "pipes": 1000, "equipment": 300}

_SYSTEM_KEYWORDS = (
    ("消防", ("消防", "喷淋", "消火栓")),
    ("给排水", ("给排水", "雨水", "污水", "排水", "给水")),
    ("电气", ("电气", "桥架", "配电", "照明", "动力")),
    ("暖通", ("暖通", "风管", "空调", "通风")),
)


def recognize(geom: DrawingGeometry, discipline: str, drawing_id: str) -> FloorElements:
    """识别构件；任何异常返回空 FloorElements（scale=缺省）。

    图名判定约定：取 ``geom.texts`` 中的文本内容做关键词匹配
    （梁图=含「梁」，机电 system=按专业关键词），discipline 兜底。
    """
    try:
        return _recognize(geom, discipline, drawing_id)
    except Exception as exc:  # noqa: BLE001 — 识别失败降级空构件
        logger.warning("[model3d] 构件识别失败(%s): %s", drawing_id, exc)
        return FloorElements(scale=_DEFAULT_SCALE)


def _recognize(geom: DrawingGeometry, discipline: str, drawing_id: str) -> FloorElements:
    truncated = geom.primitive_count() > MAX_PRIMITIVES
    lines = geom.lines[:MAX_PRIMITIVES]
    rects = geom.rects[:MAX_PRIMITIVES]
    polys = geom.polys[:MAX_PRIMITIVES]

    all_text = "；".join(t[2] for t in geom.texts)
    axis_x, axis_y, axis_lines = _detect_axes(lines, geom.page_w, geom.page_h)
    scale = _detect_scale(all_text, geom.page_w, axis_x, axis_y)
    origin = _origin_pt(axis_x, axis_y, geom.page_h)

    ctx = _Ctx(geom.page_h, scale, origin, drawing_id)
    result = FloorElements(scale=scale, axes=_axes_dict(axis_x, axis_y, ctx, truncated))

    if discipline == "mep":
        result.pipes = _find_pipes(lines, axis_lines, all_text, ctx)
        result.equipment = _find_equipment(rects, polys, geom.texts, ctx)
        return result

    result.columns = _find_columns(rects, polys, ctx)
    pairs_are_beams = _is_beam_drawing(all_text)
    pairs = _find_parallel_pairs(
        lines, axis_lines, _BEAM_GAP if pairs_are_beams else _WALL_GAP, ctx
    )
    if pairs_are_beams:
        result.beams = [
            {"path": p["path"], "width": p["width"], "depth": 0.6, "src": drawing_id}
            for p in pairs[:_CAPS["beams"]]
        ]
    else:
        result.walls = pairs[:_CAPS["walls"]]
    result.slabs = _find_slabs(polys, axis_x, axis_y, ctx)
    return result


class _Ctx:
    """坐标换算上下文：y 翻转 → 平移轴网原点 → 比例换算（米）。"""

    def __init__(self, page_h: float, scale: float, origin: tuple[float, float], src: str):
        self.page_h = page_h
        self.scale = scale
        self.origin = origin
        self.src = src

    def to_m(self, x: float, y: float) -> list[float]:
        fx = x - self.origin[0]
        fy = (self.page_h - y) - self.origin[1]
        return [round(fx * self.scale, 3), round(fy * self.scale, 3)]

    def len_m(self, d_pt: float) -> float:
        return d_pt * self.scale


def _detect_scale(
    all_text: str, page_w: float,
    axis_x: list[float], axis_y: list[float],
) -> float:
    match = _SCALE_RE.search(all_text)
    if match:
        return int(match.group(1)) * 0.000352778
    if page_w > _DXF_MODEL_SPACE_THRESHOLD:
        return 0.001  # DXF 毫米模型空间
    spacing = _median_spacing(axis_x) or _median_spacing(axis_y)
    if spacing:
        return _STANDARD_AXIS_SPACING_M / spacing
    return _DEFAULT_SCALE


def _median_spacing(positions: list[float]) -> float | None:
    if len(positions) < 2:
        return None
    gaps = sorted(b - a for a, b in zip(positions, positions[1:]))
    return gaps[len(gaps) // 2] if gaps else None


def _detect_axes(
    lines: list, page_w: float, page_h: float,
) -> tuple[list[float], list[float], set[int]]:
    """长直线 → 轴网位置；返回 (x 轴位置, y 轴位置, 轴线索引集合)。"""
    axis_x: list[float] = []
    axis_y: list[float] = []
    axis_idx: set[int] = set()
    for i, (x0, y0, x1, y1) in enumerate(lines):
        if abs(x0 - x1) <= _LINE_STRAIGHT_TOL_PT and abs(y1 - y0) >= _AXIS_MIN_RATIO * page_h:
            axis_x.append((x0 + x1) / 2)
            axis_idx.add(i)
        elif abs(y0 - y1) <= _LINE_STRAIGHT_TOL_PT and abs(x1 - x0) >= _AXIS_MIN_RATIO * page_w:
            axis_y.append((y0 + y1) / 2)
            axis_idx.add(i)
    return _dedupe(sorted(axis_x)), _dedupe(sorted(axis_y)), axis_idx


def _dedupe(positions: list[float], tol: float = 2.0) -> list[float]:
    merged: list[float] = []
    for pos in positions:
        if not merged or pos - merged[-1] > tol:
            merged.append(pos)
    return merged


def _origin_pt(axis_x: list[float], axis_y: list[float], page_h: float) -> tuple[float, float]:
    ox = min(axis_x) if axis_x else 0.0
    # y 轴位置是页面坐标，翻转后取最小
    flipped = sorted(page_h - y for y in axis_y)
    oy = flipped[0] if flipped else 0.0
    return ox, oy


def _axes_dict(axis_x: list[float], axis_y: list[float], ctx: _Ctx, truncated: bool) -> dict:
    axes = {
        "x": [["", ctx.to_m(pos, ctx.page_h)[0]] for pos in axis_x],
        "y": [["", ctx.to_m(0, pos)[1]] for pos in axis_y],
    }
    if truncated:
        axes["truncated"] = True
    return axes


def _find_columns(rects: list, polys: list, ctx: _Ctx) -> list[dict]:
    columns: list[dict] = []
    for x, y, w, h, filled in rects:
        if not filled:
            continue
        if _is_column_size(ctx.len_m(w), ctx.len_m(h)):
            columns.append(_rect_element(x, y, w, h, ctx))
        if len(columns) >= _CAPS["columns"]:
            return columns
    for poly in polys:
        x, y, w, h = _poly_bbox(poly)
        if _is_column_size(ctx.len_m(w), ctx.len_m(h)):
            columns.append({"outline": [ctx.to_m(px, py) for px, py in poly[:8]], "src": ctx.src})
        if len(columns) >= _CAPS["columns"]:
            break
    return columns


def _is_column_size(w_m: float, h_m: float) -> bool:
    lo, hi = _COLUMN_SIZE
    if not (lo <= w_m <= hi and lo <= h_m <= hi):
        return False
    aspect = max(w_m, h_m) / max(min(w_m, h_m), 1e-6)
    return aspect < _COLUMN_MAX_ASPECT


def _rect_element(x: float, y: float, w: float, h: float, ctx: _Ctx) -> dict:
    outline = [
        ctx.to_m(x, y), ctx.to_m(x + w, y),
        ctx.to_m(x + w, y + h), ctx.to_m(x, y + h),
    ]
    return {"outline": outline, "src": ctx.src}


def _poly_bbox(poly: list) -> tuple[float, float, float, float]:
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    return min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)


def _find_parallel_pairs(
    lines: list, axis_idx: set[int], gap_range: tuple[float, float], ctx: _Ctx,
) -> list[dict]:
    """同向平行线对（间距在范围内、重叠 >1m）→ 中线构件（墙/梁通用）。"""
    horizontal: list[tuple[float, float, float]] = []  # (y, x_start, x_end)
    vertical: list[tuple[float, float, float]] = []
    for i, (x0, y0, x1, y1) in enumerate(lines):
        if i in axis_idx:
            continue
        if abs(y0 - y1) <= _LINE_STRAIGHT_TOL_PT:
            horizontal.append(((y0 + y1) / 2, min(x0, x1), max(x0, x1)))
        elif abs(x0 - x1) <= _LINE_STRAIGHT_TOL_PT:
            vertical.append(((x0 + x1) / 2, min(y0, y1), max(y0, y1)))
    pairs = _pair_up(horizontal, gap_range, ctx, horizontal_dir=True)
    pairs += _pair_up(vertical, gap_range, ctx, horizontal_dir=False)
    return pairs


def _pair_up(
    segments: list[tuple[float, float, float]],
    gap_range: tuple[float, float], ctx: _Ctx, *, horizontal_dir: bool,
) -> list[dict]:
    segments = sorted(segments)
    used: set[int] = set()
    result: list[dict] = []
    for i, (pos_a, s_a, e_a) in enumerate(segments):
        if i in used:
            continue
        for j in range(i + 1, len(segments)):
            if j in used:
                continue
            pos_b, s_b, e_b = segments[j]
            gap_m = ctx.len_m(pos_b - pos_a)
            if gap_m > gap_range[1]:
                break
            overlap = min(e_a, e_b) - max(s_a, s_b)
            if gap_range[0] <= gap_m and ctx.len_m(overlap) >= _PAIR_MIN_OVERLAP_M:
                mid = (pos_a + pos_b) / 2
                start, end = max(s_a, s_b), min(e_a, e_b)
                path = (
                    [ctx.to_m(start, mid), ctx.to_m(end, mid)]
                    if horizontal_dir else [ctx.to_m(mid, start), ctx.to_m(mid, end)]
                )
                result.append({"path": path, "width": round(gap_m, 3), "src": ctx.src})
                used.update((i, j))
                break
    return result


def _is_beam_drawing(all_text: str) -> bool:
    return "梁" in all_text and "图" in all_text


def _find_slabs(polys: list, axis_x: list[float], axis_y: list[float], ctx: _Ctx) -> list[dict]:
    best: list | None = None
    best_area = 0.0
    for poly in polys:
        _x, _y, w, h = _poly_bbox(poly)
        area = ctx.len_m(w) * ctx.len_m(h)
        if area > best_area:
            best, best_area = poly, area
    if best is not None and best_area >= _SLAB_MIN_AREA_M2:
        return [{"outline": [ctx.to_m(x, y) for x, y in best], "thickness": 0.12, "src": ctx.src}]
    if len(axis_x) >= 2 and len(axis_y) >= 2:
        x0, x1 = min(axis_x), max(axis_x)
        y0, y1 = min(axis_y), max(axis_y)
        outline = [ctx.to_m(x0, y0), ctx.to_m(x1, y0), ctx.to_m(x1, y1), ctx.to_m(x0, y1)]
        return [{"outline": outline, "thickness": 0.12, "src": ctx.src}]
    return []


def _pipe_system(all_text: str) -> str:
    for system, keywords in _SYSTEM_KEYWORDS:
        if any(k in all_text for k in keywords):
            return system
    return "其他"


def _find_pipes(lines: list, axis_idx: set[int], all_text: str, ctx: _Ctx) -> list[dict]:
    system = _pipe_system(all_text)
    pipes: list[dict] = []
    for i, (x0, y0, x1, y1) in enumerate(lines):
        if i in axis_idx:
            continue
        length_m = ctx.len_m(((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5)
        if length_m >= _PIPE_MIN_LEN_M:
            pipes.append({
                "path": [ctx.to_m(x0, y0), ctx.to_m(x1, y1)],
                "dia": 0.1, "system": system, "src": ctx.src,
            })
        if len(pipes) >= _CAPS["pipes"]:
            break
    return pipes


def _find_equipment(rects: list, polys: list, texts: list, ctx: _Ctx) -> list[dict]:
    equipment: list[dict] = []
    for x, y, w, h, _filled in rects:
        if not _is_equipment_size(ctx.len_m(w), ctx.len_m(h)):
            continue
        label = _text_inside(texts, x, y, w, h)
        element = _rect_element(x, y, w, h, ctx)
        equipment.append({"outline": element["outline"], "height": 1.5, "label": label, "src": ctx.src})
        if len(equipment) >= _CAPS["equipment"]:
            return equipment
    for poly in polys:
        x, y, w, h = _poly_bbox(poly)
        if not _is_equipment_size(ctx.len_m(w), ctx.len_m(h)):
            continue
        label = _text_inside(texts, x, y, w, h)
        equipment.append({
            "outline": [ctx.to_m(px, py) for px, py in poly[:12]],
            "height": 1.5, "label": label, "src": ctx.src,
        })
        if len(equipment) >= _CAPS["equipment"]:
            break
    return equipment


def _is_equipment_size(w_m: float, h_m: float) -> bool:
    lo, hi = _EQUIPMENT_SIZE
    return lo <= w_m <= hi and lo <= h_m <= hi


def _text_inside(texts: list, x: float, y: float, w: float, h: float) -> str:
    for tx, ty, content in texts:
        if x <= tx <= x + w and y <= ty <= y + h:
            return content
    return ""
