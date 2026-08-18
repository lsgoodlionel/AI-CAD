"""构件识别：几何原语 → 柱/墙/梁/板/管线/设备（纯确定性启发式，无 LLM）。

坐标处理：先做 y 翻转（PDF 页面坐标 y 向下），再平移到轴网原点，最后按比例尺换算为米。
识别失败/超限均优雅降级，绝不抛异常。
"""
from __future__ import annotations

import logging
import re

from .geometry_extractor import MAX_PRIMITIVES
from .layer_conventions import (
    classify_by_layer, classify_system, is_annotation_layer,
)
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
# 图层已确认为墙时放宽间距上限：地下室外墙/挡土墙/人防墙常达 0.3~0.8m（甚至更厚），
# 会被普通 _WALL_GAP 上限 0.4m 结构性丢弃；仅当成对两线均落在墙图层时才启用宽上限。
_WIDE_WALL_GAP_MAX = 1.0
_PAIR_MIN_OVERLAP_M = 1.0
_PIPE_MIN_LEN_M = 3.0
_EQUIPMENT_SIZE = (0.5, 5.0)
_SLAB_MIN_AREA_M2 = 10.0

# 板的来源依据。**只有 SLAB_BASIS_RECOGNISED 是真识别出来的**，其余三种是兜底
# ——把它们混进同一个 slabs 计数，会让「板」这项能力看起来远比实际强：
# 上海大歌剧院模型 v30 报 21 块板，13 层里 10 层恒为 2 块，全部来自兜底，
# 靠图层判出来的是 0 块（该项目 2309 张图全是无图层 PDF，图层分支永不命中）。
SLAB_BASIS_RECOGNISED = "layer"           # 图层/块名判定 —— 唯一的真识别
SLAB_BASIS_LARGEST_POLYGON = "largest_polygon"   # 兜底：最大闭合多边形当整层单板
SLAB_BASIS_AXIS_ENVELOPE = "axis_envelope"       # 兜底：轴网包络
SLAB_BASIS_COLUMN_ENVELOPE = "column_envelope"   # 兜底：柱/桩包络
SLAB_FALLBACK_BASES = (SLAB_BASIS_LARGEST_POLYGON, SLAB_BASIS_AXIS_ENVELOPE,
                       SLAB_BASIS_COLUMN_ENVELOPE)

_SLAB_THICKNESS_M = 0.12              # 普通楼板默认厚（无实测标注时）
_RAFT_THICKNESS_M = 0.5              # 基础底板/筏板/承台默认厚（远厚于楼板）
# 筏板/底板/承台判定（在已归类为 slab 的多边形上再细分，给更厚默认值）
_RAFT_RE = re.compile(r"筏板|底板|承台|筏形|基础底板|RAFT|^(?:FB|DB|CT|JC)\d", re.IGNORECASE)
_AXIS_MIN_RATIO = 0.6                 # 轴线长度 ≥60% 页幅
_LINE_STRAIGHT_TOL_PT = 2.0

# 输出上限（防爆场景）
_CAPS = {"columns": 2000, "walls": 2000, "beams": 2000, "slabs": 500, "pipes": 1000, "equipment": 300}

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


#: 一张图换算后能表达的**实际宽度上限**（米）。
#:
#: **为什么不能只卡比例**：§6.0.4 表最大 1:2000，而 `is_scale_plausible`
#: 的上限留到了 1:5000 的余量 —— 实测 `S-0-20-102.04C` 的 1:4222
#: **正落在这段余量里，门禁放行**，于是 3370pt × 1.489 m/pt = 5019 米。
#:
#: 取 3000 米：1:2000 的 A0 图（3370pt）换算是 2377 米，仍在其内；
#: 而 1:4222 的 5019 米被挡下。**判据是「这张图能有多大」这个工程事实**，
#: 比单纯的比例区间更贴近现实。
MAX_DRAWING_EXTENT_M = 3000.0


def resolve_scale(detected: float, scale_override: float | None = None,
                  page_w_pt: float | None = None) -> float:
    """识别出的比例过 §6.0.4 门禁；不合理且有落库比例时改用后者。

    **实测**（`S-0-20-102.04C`，图幅 3370×2384pt）：识别器算出 **1:4222**
    而 `drawing_transform` 是 1:150 —— 差 28 倍，3370pt × 1.489 m/pt = 5019 米，
    正是 F1 层墙跨度 2207 米的来源。

    比例门禁此前加在 `transform_from_geometry` 与 `_transform_of` 上，
    **唯独漏了识别器这条唯一决定构件坐标的路径**。

    没有可借比例时保持原状：强行归零会让整张图坍缩到一点，比放着更糟。
    """
    from services.drawing_transform import is_scale_plausible

    def usable(value: float) -> bool:
        if not value or value <= 0 or not is_scale_plausible(value):
            return False
        # 图幅换算出的实际尺寸也要说得通（见 MAX_DRAWING_EXTENT_M）
        return not (page_w_pt and page_w_pt * value > MAX_DRAWING_EXTENT_M)

    if usable(detected):
        return detected
    if usable(scale_override or 0.0):
        return float(scale_override)
    return detected


def recognize(geom: DrawingGeometry, discipline: str, drawing_id: str,
              origin_override: tuple[float | None, float | None] | None = None,
              scale_override: float | None = None,
              drawing_title: str | None = None,
              ) -> FloorElements:
    """识别构件；任何异常返回空 FloorElements（scale=缺省）。

    图名判定约定：取 ``geom.texts`` 中的文本内容做关键词匹配
    （梁图=含「梁」，机电 system=按专业关键词），discipline 兜底。
    """
    try:
        return _recognize(geom, discipline, drawing_id, origin_override,
                          scale_override, drawing_title)
    except Exception as exc:  # noqa: BLE001 — 识别失败降级空构件
        logger.warning("[model3d] 构件识别失败(%s): %s", drawing_id, exc)
        return FloorElements(scale=_DEFAULT_SCALE)


def _recognize(geom: DrawingGeometry, discipline: str, drawing_id: str,
               origin_override: tuple[float | None, float | None] | None = None,
               scale_override: float | None = None,
               drawing_title: str | None = None,
               ) -> FloorElements:
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
    scale = resolve_scale(
        _detect_scale(all_text, geom.page_w, axis_x, axis_y),
        scale_override, geom.page_w)
    origin = _origin_pt(axis_x, axis_y, geom.page_h)

    ctx = _Ctx(geom.page_h, scale, origin, drawing_id,
               origin_override=origin_override)
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

    # **墙图上的填充截面是墙不是柱**（实测 1404 根假柱）。
    # 仍保留「图层明确为柱」的路径 —— 那是设计师的明确标注，
    # 比图名更强（墙图上确实可能画几根柱）。
    wall_drawing = is_wall_drawing(drawing_title)
    result.columns = _find_columns(
        rects, rect_layers, rect_blocks, polys, poly_layers, poly_blocks, ctx,
        layer_only=wall_drawing,
    )
    pairs_are_beams = _is_beam_drawing(all_text, line_layers)
    pairs = _find_parallel_pairs(
        lines, line_layers, axis_lines,
        _BEAM_GAP if pairs_are_beams else _WALL_GAP, ctx,
        allow_wide_walls=not pairs_are_beams,
    )
    if pairs_are_beams:
        result.beams = [
            {"path": p["path"], "width": p["width"], "depth": 0.6, "src": drawing_id}
            for p in pairs[:_CAPS["beams"]]
        ]
    else:
        result.walls = pairs[:_CAPS["walls"]]
    result.slabs = _find_slabs(polys, poly_layers, poly_blocks, axis_x, axis_y, ctx, result.columns)
    _clip_to_axes(result)
    return result


class _Ctx:
    """坐标换算上下文：y 翻转 → 平移轴网原点 → 比例换算（米）。"""

    def __init__(self, page_h: float, scale: float,
                 origin: tuple[float | None, float | None], src: str,
                 origin_override: tuple[float | None, float | None] | None = None):
        self.page_h = page_h
        self.scale = scale
        # 该方向没检出轴线时 `_origin_pt` 返回 None。**先用轴网路径的原点补**
        # （`origin_override`，来自 `drawing_transform`），补不上才按 0 兜底。
        #
        # 实测:修好 `S-0-20-102.04C` 的 drawing_transform 后重建，F1 的墙
        # **跨度仍是 2207 米、范围 [149,2356] 一字未改** —— 因为构件坐标不走
        # 那张表，`_Ctx` 自己算原点、缺了就当 0，于是从图幅边缘算起。
        # 该图真原点 595.29pt × 1:150 ⇒ **整体偏移 31.5 米**。
        override = origin_override or (None, None)
        resolved = (
            origin[0] if origin[0] is not None else override[0],
            origin[1] if origin[1] is not None else override[1],
        )
        self.origin = (resolved[0] or 0.0, resolved[1] or 0.0)
        # 补上了就不算缺失，否则下游会误以为这方向仍不可信
        self.origin_missing = (resolved[0] is None, resolved[1] is None)
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


def _min_labeled_pos(axes: list[tuple[str, float]]) -> float | None:
    """源坐标基准：有轴号 → 轴号最小者的位置；无轴号 → 位置最小者。

    **没有轴线时返回 `None` 而不是 0.0** —— 0 是个合法坐标值，
    返回它等于对下游说「原点就在 0」。实测 1436 条变换里
    origin_x=0 有 72 张、origin_y=0 有 77 张（10.4%），
    而「两方向都为 0」是 0 张 —— 它们是**缺一个方向**，不是原点真在 0。
    这与 `drawing_transform` 的 1:335 万教训同源：
    一个「看起来合法」的值比缺失更危险，缺失会让下游降级，假值一路通行。
    """
    if not axes:
        return None
    labeled = [(label, pos) for label, pos in axes if label]
    if labeled:
        return min(labeled, key=lambda a: _axis_label_sort_key(a[0]))[1]
    return min(pos for _label, pos in axes)


def _origin_pt(
    axis_x: list[tuple[str, float]], axis_y: list[tuple[str, float]], page_h: float,
) -> tuple[float | None, float | None]:
    """统一源坐标点：最小轴号 X 轴 × 最小轴号 Y 轴 交点（无轴号回退最小位置）。

    **逐方向返回**：缺哪个方向就是哪个为 `None`，调用方据此决定降级方式。
    """
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
    layer_only: bool = False,
) -> list[dict]:
    """填充截面 + 图层 → 柱。

    `layer_only=True` 时**只认图层明确标注为柱的**，不按尺寸猜 ——
    用于墙图（墙截面的尺寸常落在柱的判据范围内，实测造出 1404 根假柱）。
    """
    columns: list[dict] = []
    for i, (x, y, w, h, filled) in enumerate(rects):
        # **标注图层不产出构件**：实测「立柱桩标注」一层造出 3410 根假柱。
        _layer = _at(rect_layers, i)
        is_column_layer = (not is_annotation_layer(_layer)
                           and classify_by_layer(
                               _layer, _at(rect_blocks, i)) == "column")
        # 图层/块名明确为柱时，即使未填充也识别（修复「柱必须 filled 才识别」漏检）
        if not filled and not is_column_layer:
            continue
        if is_column_layer or (not layer_only
                               and _is_column_size(ctx.len_m(w), ctx.len_m(h))):
            columns.append(_rect_element(x, y, w, h, ctx))
        if len(columns) >= _CAPS["columns"]:
            return columns
    for i, poly in enumerate(polys):
        x, y, w, h = _poly_bbox(poly)
        # **标注/钢筋图层不产出构件**（与矩形分支同一条纪律）——
        # 我第一版只在矩形分支加了这道闸，而实测那 711 根假柱
        # 全部来自**多边形**（`墙柱纵筋` 图层），修了一半等于没修。
        _pl = _at(poly_layers, i)
        is_column_layer = (not is_annotation_layer(_pl)
                           and classify_by_layer(
                               _pl, _at(poly_blocks, i)) == "column")
        if is_column_layer or (not layer_only
                               and _is_column_size(ctx.len_m(w), ctx.len_m(h))):
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
    lines: list, line_layers: list, axis_idx: set[int],
    gap_range: tuple[float, float], ctx: _Ctx, *, allow_wide_walls: bool = False,
) -> list[dict]:
    """同向平行线对（间距在范围内、重叠 >1m）→ 中线构件（墙/梁通用）。

    allow_wide_walls=True 时，成对两线均落在墙图层则放宽间距上限到
    _WIDE_WALL_GAP_MAX，召回被普通 _WALL_GAP 上限丢弃的地下室外墙/挡土墙。
    """
    # 元组末位标记该线是否落在墙图层（几何无凭时为 False，行为等同旧逻辑）。
    horizontal: list[tuple[float, float, float, bool]] = []  # (y, x_start, x_end, wall)
    vertical: list[tuple[float, float, float, bool]] = []
    for i, (x0, y0, x1, y1) in enumerate(lines):
        if i in axis_idx:
            continue
        wall_layer = allow_wide_walls and classify_by_layer(_at(line_layers, i)) == "wall"
        if abs(y0 - y1) <= _LINE_STRAIGHT_TOL_PT:
            horizontal.append(((y0 + y1) / 2, min(x0, x1), max(x0, x1), wall_layer))
        elif abs(x0 - x1) <= _LINE_STRAIGHT_TOL_PT:
            vertical.append(((x0 + x1) / 2, min(y0, y1), max(y0, y1), wall_layer))
    pairs = _pair_up(horizontal, gap_range, ctx, horizontal_dir=True)
    pairs += _pair_up(vertical, gap_range, ctx, horizontal_dir=False)
    return pairs


def _pair_up(
    segments: list[tuple[float, float, float, bool]],
    gap_range: tuple[float, float], ctx: _Ctx, *, horizontal_dir: bool,
) -> list[dict]:
    segments = sorted(segments)
    used: set[int] = set()
    result: list[dict] = []
    for i, (pos_a, s_a, e_a, wall_a) in enumerate(segments):
        if i in used:
            continue
        for j in range(i + 1, len(segments)):
            if j in used:
                continue
            pos_b, s_b, e_b, wall_b = segments[j]
            gap_m = ctx.len_m(pos_b - pos_a)
            eff_max = _WIDE_WALL_GAP_MAX if (wall_a and wall_b) else gap_range[1]
            # 升序排列：间距超过最宽可能上限后，更远的 j 只会更大 → 停止扫描。
            if gap_m > _WIDE_WALL_GAP_MAX:
                break
            if gap_m > eff_max:  # 超本类上限（普通墙/梁对）→ 跳过，留待可能的墙宽对
                continue
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


#: 判定「这是梁图」所需的最少梁图层线数。
#: 一张梁配筋图实测有数千条梁线（S-31-07A 为 7809 条）；
#: 而柱图里偶尔引用几条梁线远不到这个量级。
MIN_BEAM_LINES_FOR_BEAM_DRAWING = 100

#: 墙图关键词。「墙」在「柱」之前出现即认为主语是墙 ——
#: 「墙柱配筋平面图」是墙柱共同表达，「柱墙连接节点」以柱为主。
#: 判据不完美，但**判错只影响归类、不丢构件**。
_WALL_WORD_RE = re.compile(r"墙")
_COLUMN_WORD_RE = re.compile(r"柱")


def is_wall_drawing(title: str | None) -> bool:
    """图名是否声明这是**墙**图（墙配筋图 / 剪力墙平面图…）。

    **实测 1404 根假柱**：F5 层 1921 根「柱」里，1337 + 67 根来自
    「××墙配筋平面图」—— 墙的截面填充多边形尺寸落在柱判据范围内，
    就被判成了柱。**图名明确声明了图种，几何判据却没听。**

    与「梁图上的平行线对被当成墙」是同一类问题的镜像，
    处置沿用同一条原则：**图种声明优先于几何猜测**。
    """
    text = str(title or "")
    wall = _WALL_WORD_RE.search(text)
    if not wall:
        return False
    column = _COLUMN_WORD_RE.search(text)
    return column is None or wall.start() < column.start()


def _is_beam_drawing(all_text: str, line_layers: list | None = None) -> bool:
    """这张图是否表达梁。**图层优先于图内文字**。

    图内文字是弱证据：实测那张「首层框架梁平面整体配筋图」只有
    **4 条可信文字**（全是水印签章），其余是坏 CMap 乱码被正确拦截，
    于是拿不到「梁」字 —— 图层里明明有 **7809 条梁线**，却产出 0 根梁。

    图层是设计师**明确标注**的（AIA `S-BEAM`），比图内文字可靠。
    无图层时退回文本判据，大歌剧院路径不变。
    """
    if line_layers:
        beam_lines = sum(1 for layer in line_layers
                         if layer and not is_annotation_layer(layer)
                         and classify_by_layer(layer) == "beam")
        if beam_lines >= MIN_BEAM_LINES_FOR_BEAM_DRAWING:
            return True
    return "梁" in all_text and "图" in all_text


def _is_raft_layer(layer: str, block: str) -> bool:
    """在已归类为 slab 的多边形上，进一步判定是否为基础底板/筏板/承台（更厚）。"""
    return bool(_RAFT_RE.search(layer or "") or _RAFT_RE.search(block or ""))


def _find_slabs(
    polys: list, poly_layers: list, poly_blocks: list,
    axis_x: list[float], axis_y: list[float], ctx: _Ctx,
    columns: list[dict] | None = None,
) -> list[dict]:
    columns = columns or []
    # 1) 图层命中优先：逐个收集所有被图层/块名判定为 slab 的多边形，
    #    支持多分区筏板/多板块（修复「每图仅一块板」），并区分筏板/底板（更厚）。
    layered: list[dict] = []
    best: list | None = None
    best_area = 0.0
    for i, poly in enumerate(polys):
        _x, _y, w, h = _poly_bbox(poly)
        area = ctx.len_m(w) * ctx.len_m(h)
        if area > best_area:
            best, best_area = poly, area
        if area < _SLAB_MIN_AREA_M2:
            continue
        layer, block = _at(poly_layers, i), _at(poly_blocks, i)
        if classify_by_layer(layer, block) != "slab":
            continue
        is_raft = _is_raft_layer(layer, block)
        layered.append({
            "outline": [ctx.to_m(x, y) for x, y in poly],
            "thickness": _RAFT_THICKNESS_M if is_raft else _SLAB_THICKNESS_M,
            "kind": "raft" if is_raft else "slab",
            "basis": SLAB_BASIS_RECOGNISED,
            "src": ctx.src,
        })
        if len(layered) >= _CAPS["slabs"]:
            break
    if layered:
        return layered
    # 2) 无图层命中 → 兜底。**以下三条都不是识别结果**，各自标明依据，
    #    让统计能把它们与图层命中的板分开数（否则 0 块真板会显示成 N 块）。
    if best is not None and best_area >= _SLAB_MIN_AREA_M2:
        return [{"outline": [ctx.to_m(x, y) for x, y in best],
                 "thickness": _SLAB_THICKNESS_M,
                 "basis": SLAB_BASIS_LARGEST_POLYGON, "src": ctx.src}]
    if len(axis_x) >= 2 and len(axis_y) >= 2:
        xs = [pos for _label, pos in axis_x]
        ys = [pos for _label, pos in axis_y]
        x0, x1 = min(xs), max(xs)
        y0, y1 = min(ys), max(ys)
        outline = [ctx.to_m(x0, y0), ctx.to_m(x1, y0), ctx.to_m(x1, y1), ctx.to_m(x0, y1)]
        return [{"outline": outline, "thickness": _SLAB_THICKNESS_M,
                 "basis": SLAB_BASIS_AXIS_ENVELOPE, "src": ctx.src}]
    # 兜底:无大多边形、无 2×2 轴网,但已识别出柱 → 用柱包络(米)生成楼板,
    # 让缺清晰轴网的楼层也有楼板参与体量/算量(否则该层无板)。
    slab = _slab_from_columns(columns)
    return [slab] if slab is not None else []


# 由柱包络生成楼板的最小柱数与外扩边距(米)
_SLAB_FROM_COLUMNS_MIN = 4
_SLAB_ENVELOPE_MARGIN_M = 1.0


def _slab_from_columns(columns: list[dict]) -> dict | None:
    """无多边形/轴网时,由柱外轮廓包络(已是米坐标)生成楼板兜底。"""
    pts: list[tuple[float, float]] = []
    for column in columns:
        for point in column.get("outline") or []:
            if len(point) >= 2:
                pts.append((float(point[0]), float(point[1])))
    if len(columns) < _SLAB_FROM_COLUMNS_MIN or len(pts) < 3:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    m = _SLAB_ENVELOPE_MARGIN_M
    x0, x1, y0, y1 = min(xs) - m, max(xs) + m, min(ys) - m, max(ys) + m
    if (x1 - x0) * (y1 - y0) < _SLAB_MIN_AREA_M2:
        return None
    return {
        "outline": [[x0, y0], [x1, y0], [x1, y1], [x0, y1]],
        "thickness": _SLAB_THICKNESS_M,
        "basis": SLAB_BASIS_COLUMN_ENVELOPE,
        "src": "columns-envelope",
    }


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
