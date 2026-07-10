"""构件识别：几何原语 → 柱/墙/梁/板/管线/设备（纯确定性启发式，无 LLM）。

坐标处理：先做 y 翻转（PDF 页面坐标 y 向下），再平移到轴网原点，最后按比例尺换算为米。
识别失败/超限均优雅降级，绝不抛异常。
"""
from __future__ import annotations

import logging
import re

from .geometry_extractor import MAX_PRIMITIVES
from .layer_conventions import classify_by_layer, classify_system
from .types import DrawingGeometry, FloorElements


def _at(values: list, index: int) -> str:
    """安全读取索引对齐的图层/块并行列表（越界或缺失返回空串），保证无图层时零副作用。"""
    return values[index] if index < len(values) else ""

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

# 轴号标注：①-⑳ 圈号 / 1~2 位数字 / 1~2 位大写字母
_CIRCLED_BASE = ord("①") - 1
_AXIS_NUM_RE = re.compile(r"^\d{1,2}$")
_AXIS_ALPHA_RE = re.compile(r"^[A-Z]{1,2}$")
_AXIS_LABEL_SEARCH_X_PT = 18.0   # 轴号距轴线的横向容差
_AXIS_LABEL_SEARCH_END_PT = 34.0  # 轴号距轴线端点的容差
# 工程标高：±0.000 / -9.300 / 23.700（三位小数），合理范围 [-30, 300] 米
_ELEVATION_RE = re.compile(r"(±|[+-])?(\d{1,3}\.\d{3})")
_ELEVATION_RANGE = (-30.0, 300.0)


def _normalize_axis_label(text: str) -> str | None:
    """轴号归一化：③→'3'；'12'→'12'；'B'→'B'；其他→None。"""
    text = (text or "").strip().strip("()（）")
    if len(text) == 1 and "①" <= text <= "⑳":
        return str(ord(text) - _CIRCLED_BASE)
    if _AXIS_NUM_RE.match(text):
        return str(int(text))
    if _AXIS_ALPHA_RE.match(text):
        return text
    return None


def _axis_label_sort_key(label: str) -> tuple[int, int]:
    """轴号排序键：数字轴按数值，字母轴按字母序（'AA' 排在 'Z' 后）。"""
    if label.isdigit():
        return (0, int(label))
    return (1, (len(label) - 1) * 26 * 26 + sum(ord(c) - ord("A") for c in label))


def extract_elevations(all_text: str) -> list[float]:
    """提取工程标高文本（±0.000/-9.300/23.700），去重升序。"""
    values: set[float] = set()
    for sign, number in _ELEVATION_RE.findall(all_text):
        value = float(number)
        if sign == "-":
            value = -value
        if _ELEVATION_RANGE[0] <= value <= _ELEVATION_RANGE[1]:
            values.add(round(value, 3))
    return sorted(values)


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
    line_layers = geom.line_layers[:MAX_PRIMITIVES]
    rect_layers = geom.rect_layers[:MAX_PRIMITIVES]
    rect_blocks = geom.rect_blocks[:MAX_PRIMITIVES]
    poly_layers = geom.poly_layers[:MAX_PRIMITIVES]
    poly_blocks = geom.poly_blocks[:MAX_PRIMITIVES]

    all_text = "；".join(t[2] for t in geom.texts)
    axis_x, axis_y, axis_lines = _detect_axes(
        lines, geom.page_w, geom.page_h, geom.texts
    )
    scale = _detect_scale(all_text, geom.page_w, axis_x, axis_y)
    origin = _origin_pt(axis_x, axis_y, geom.page_h)

    ctx = _Ctx(geom.page_h, scale, origin, drawing_id)
    result = FloorElements(
        scale=scale, axes=_axes_dict(axis_x, axis_y, ctx, truncated, all_text)
    )

    if discipline == "mep":
        result.pipes = _find_pipes(lines, line_layers, axis_lines, all_text, ctx)
        result.equipment = _find_equipment(
            rects, rect_layers, rect_blocks, polys, poly_layers, poly_blocks, geom.texts, ctx
        )
        _clip_to_axes(result)
        return result

    result.columns = _find_columns(
        rects, rect_layers, rect_blocks, polys, poly_layers, poly_blocks, ctx
    )
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
    _clip_to_axes(result)
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


def _median_spacing(axes: list[tuple[str, float]]) -> float | None:
    positions = [pos for _label, pos in axes]
    if len(positions) < 2:
        return None
    gaps = sorted(b - a for a, b in zip(positions, positions[1:]))
    return gaps[len(gaps) // 2] if gaps else None


def _find_axis_label(
    texts: list, *, along: str, pos: float, end_a: float, end_b: float,
) -> str:
    """轴线端部附近的轴号标注（along='x' 时轴为竖线，pos 为 x 坐标）。"""
    for tx, ty, content in texts:
        label = _normalize_axis_label(content)
        if label is None:
            continue
        near_pos = abs((tx if along == "x" else ty) - pos) <= _AXIS_LABEL_SEARCH_X_PT
        cursor = ty if along == "x" else tx
        near_end = (
            abs(cursor - end_a) <= _AXIS_LABEL_SEARCH_END_PT
            or abs(cursor - end_b) <= _AXIS_LABEL_SEARCH_END_PT
        )
        if near_pos and near_end:
            return label
    return ""


def _detect_axes(
    lines: list, page_w: float, page_h: float, texts: list,
) -> tuple[list[tuple[str, float]], list[tuple[str, float]], set[int]]:
    """长直线 → 轴网（带轴号标注）；返回 (x 轴, y 轴, 轴线索引集合)。

    轴元素为 ``(label, pos_pt)``，label 由端部圈号/数字/字母标注识别，无标注为 ""。
    """
    axis_x: list[tuple[str, float]] = []
    axis_y: list[tuple[str, float]] = []
    axis_idx: set[int] = set()
    for i, (x0, y0, x1, y1) in enumerate(lines):
        if abs(x0 - x1) <= _LINE_STRAIGHT_TOL_PT and abs(y1 - y0) >= _AXIS_MIN_RATIO * page_h:
            pos = (x0 + x1) / 2
            label = _find_axis_label(texts, along="x", pos=pos, end_a=min(y0, y1), end_b=max(y0, y1))
            axis_x.append((label, pos))
            axis_idx.add(i)
        elif abs(y0 - y1) <= _LINE_STRAIGHT_TOL_PT and abs(x1 - x0) >= _AXIS_MIN_RATIO * page_w:
            pos = (y0 + y1) / 2
            label = _find_axis_label(texts, along="y", pos=pos, end_a=min(x0, x1), end_b=max(x0, x1))
            axis_y.append((label, pos))
            axis_idx.add(i)
    return _dedupe(axis_x), _dedupe(axis_y), axis_idx


def _dedupe(axes: list[tuple[str, float]], tol: float = 2.0) -> list[tuple[str, float]]:
    merged: list[tuple[str, float]] = []
    for label, pos in sorted(axes, key=lambda a: a[1]):
        if merged and pos - merged[-1][1] <= tol:
            # 同一条轴：保留已有标注
            if label and not merged[-1][0]:
                merged[-1] = (label, merged[-1][1])
            continue
        merged.append((label, pos))
    return merged


def _min_labeled_pos(axes: list[tuple[str, float]]) -> float:
    """源坐标基准：有轴号 → 轴号最小者的位置；无轴号 → 位置最小者。"""
    if not axes:
        return 0.0
    labeled = [(label, pos) for label, pos in axes if label]
    if labeled:
        return min(labeled, key=lambda a: _axis_label_sort_key(a[0]))[1]
    return min(pos for _label, pos in axes)


def _origin_pt(
    axis_x: list[tuple[str, float]], axis_y: list[tuple[str, float]], page_h: float,
) -> tuple[float, float]:
    """统一源坐标点：最小轴号 X 轴 × 最小轴号 Y 轴 交点（无轴号回退最小位置）。"""
    ox = _min_labeled_pos(axis_x)
    flipped_y = [(label, page_h - pos) for label, pos in axis_y]
    oy = _min_labeled_pos(flipped_y)
    return ox, oy


def _axes_dict(
    axis_x: list[tuple[str, float]], axis_y: list[tuple[str, float]],
    ctx: _Ctx, truncated: bool, all_text: str,
) -> dict:
    axes = {
        "x": [[label, ctx.to_m(pos, ctx.page_h)[0]] for label, pos in axis_x],
        "y": sorted(
            ([label, ctx.to_m(0, pos)[1]] for label, pos in axis_y),
            key=lambda a: a[1],
        ),
        "elevations": extract_elevations(all_text),
    }
    if truncated:
        axes["truncated"] = True
    return axes


def _find_columns(
    rects: list, rect_layers: list, rect_blocks: list,
    polys: list, poly_layers: list, poly_blocks: list, ctx: _Ctx,
) -> list[dict]:
    columns: list[dict] = []
    for i, (x, y, w, h, filled) in enumerate(rects):
        is_column_layer = classify_by_layer(_at(rect_layers, i), _at(rect_blocks, i)) == "column"
        # 图层/块名明确为柱时，即使未填充也识别（修复「柱必须 filled 才识别」漏检）
        if not filled and not is_column_layer:
            continue
        if is_column_layer or _is_column_size(ctx.len_m(w), ctx.len_m(h)):
            columns.append(_rect_element(x, y, w, h, ctx))
        if len(columns) >= _CAPS["columns"]:
            return columns
    for i, poly in enumerate(polys):
        x, y, w, h = _poly_bbox(poly)
        is_column_layer = classify_by_layer(_at(poly_layers, i), _at(poly_blocks, i)) == "column"
        if is_column_layer or _is_column_size(ctx.len_m(w), ctx.len_m(h)):
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
        xs = [pos for _label, pos in axis_x]
        ys = [pos for _label, pos in axis_y]
        x0, x1 = min(xs), max(xs)
        y0, y1 = min(ys), max(ys)
        outline = [ctx.to_m(x0, y0), ctx.to_m(x1, y0), ctx.to_m(x1, y1), ctx.to_m(x0, y1)]
        return [{"outline": outline, "thickness": 0.12, "src": ctx.src}]
    return []


# 轴网范围外允许的构件溢出边距（米）——超出视为图框/图例等离群图形
_AXIS_CLIP_PAD_M = 15.0


def _clip_to_axes(result: FloorElements) -> None:
    """构件裁剪到轴网包络 + 边距内（防图框/图例/说明块被当构件拉大建筑包络）。"""
    xs = [pos for _label, pos in result.axes.get("x", [])]
    ys = [pos for _label, pos in result.axes.get("y", [])]
    if len(xs) < 2 or len(ys) < 2:
        return
    x_lo, x_hi = min(xs) - _AXIS_CLIP_PAD_M, max(xs) + _AXIS_CLIP_PAD_M
    y_lo, y_hi = min(ys) - _AXIS_CLIP_PAD_M, max(ys) + _AXIS_CLIP_PAD_M

    def _inside(item: dict) -> bool:
        points = item.get("outline") or item.get("path") or []
        if not points:
            return True
        cx = sum(p[0] for p in points) / len(points)
        cy = sum(p[1] for p in points) / len(points)
        return x_lo <= cx <= x_hi and y_lo <= cy <= y_hi

    for kind in ("columns", "walls", "beams", "pipes", "equipment"):
        setattr(result, kind, [i for i in getattr(result, kind) if _inside(i)])


def _pipe_system(all_text: str) -> str:
    for system, keywords in _SYSTEM_KEYWORDS:
        if any(k in all_text for k in keywords):
            return system
    return "其他"


def _find_pipes(
    lines: list, line_layers: list, axis_idx: set[int], all_text: str, ctx: _Ctx
) -> list[dict]:
    default_system = _pipe_system(all_text)
    pipes: list[dict] = []
    for i, (x0, y0, x1, y1) in enumerate(lines):
        if i in axis_idx:
            continue
        length_m = ctx.len_m(((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5)
        if length_m >= _PIPE_MIN_LEN_M:
            # 图层可判定系统时优先（消防/给排水/电气/暖通），否则回退全图关键词
            system = classify_system(_at(line_layers, i)) or default_system
            pipes.append({
                "path": [ctx.to_m(x0, y0), ctx.to_m(x1, y1)],
                "dia": 0.1, "system": system, "src": ctx.src,
            })
        if len(pipes) >= _CAPS["pipes"]:
            break
    return pipes


def _find_equipment(
    rects: list, rect_layers: list, rect_blocks: list,
    polys: list, poly_layers: list, poly_blocks: list, texts: list, ctx: _Ctx,
) -> list[dict]:
    equipment: list[dict] = []
    for i, (x, y, w, h, _filled) in enumerate(rects):
        is_equip_layer = classify_by_layer(_at(rect_layers, i), _at(rect_blocks, i)) == "equipment"
        # 图层/块名明确为设备时，放宽尺寸阈值（具名设备块常不规则）
        if not is_equip_layer and not _is_equipment_size(ctx.len_m(w), ctx.len_m(h)):
            continue
        label = _text_inside(texts, x, y, w, h)
        element = _rect_element(x, y, w, h, ctx)
        equipment.append({"outline": element["outline"], "height": 1.5, "label": label, "src": ctx.src})
        if len(equipment) >= _CAPS["equipment"]:
            return equipment
    for i, poly in enumerate(polys):
        x, y, w, h = _poly_bbox(poly)
        is_equip_layer = classify_by_layer(_at(poly_layers, i), _at(poly_blocks, i)) == "equipment"
        if not is_equip_layer and not _is_equipment_size(ctx.len_m(w), ctx.len_m(h)):
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
