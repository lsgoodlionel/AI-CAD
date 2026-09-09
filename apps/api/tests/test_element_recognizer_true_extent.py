"""识别器改用真实尺寸判柱：斜细条不再是柱，旋转柱与小截面柱一根不少。

判据依据与实测来源见 `core/model3d/true_extent.py` 模块文档。
本文件守住的是**误伤边界** —— 判据能删对不算过关，删不到真柱才算。
"""
import math

from core.model3d import DrawingGeometry, recognize
from core.model3d.element_recognizer import SCALE_1_100_M_PER_PT

PAGE_W, PAGE_H = 842.0, 595.0
PT_PER_M = 1.0 / SCALE_1_100_M_PER_PT


def _rot(points, deg, cx, cy):
    a = math.radians(deg); ca, sa = math.cos(a), math.sin(a)
    return [(cx + (x - cx) * ca - (y - cy) * sa,
             cy + (x - cx) * sa + (y - cy) * ca) for x, y in points]


def _square_poly(cx, cy, side_m, deg=0.0):
    h = side_m * PT_PER_M / 2
    pts = [(cx - h, cy - h), (cx + h, cy - h), (cx + h, cy + h), (cx - h, cy + h)]
    return _rot(pts, deg, cx, cy) if deg else pts


def _bar_poly(cx, cy, long_m, short_m, deg):
    """斜细条 —— 标高符号 `∨` 的一条笔画的形态。"""
    a, b = long_m * PT_PER_M / 2, short_m * PT_PER_M / 2
    pts = [(cx - a, cy - b), (cx + a, cy - b), (cx + a, cy + b), (cx - a, cy + b)]
    return _rot(pts, deg, cx, cy)


def _plan(polys, layers=None) -> DrawingGeometry:
    """最小结构平面：两条轴线定比例，其余交给参数。"""
    geom = DrawingGeometry(page_w=PAGE_W, page_h=PAGE_H)
    for i in range(3):
        x = 100.0 + i * 8.4 * PT_PER_M
        geom.lines.append((x, 30.0, x, PAGE_H - 30.0))
        y = 100.0 + i * 4.2 * PT_PER_M
        geom.lines.append((30.0, y, PAGE_W - 30.0, y))
    for i, poly in enumerate(polys):
        geom.polys.append(poly)
        geom.poly_layers.append((layers or [])[i] if layers and i < len(layers) else "")
        geom.poly_blocks.append("")
    geom.texts.append((60.0, 40.0, "1:100"))
    geom.texts.append((400.0, 20.0, "一层结构平面图"))
    return geom


def _columns(geom):
    return recognize(geom, "structure", "d1",
                     drawing_title="一层结构平面图").columns


def test_diagonal_stroke_is_not_a_column():
    """斜细条（0.7×0.12m，斜 45°）不再被判为柱。

    它的**包围盒**是 0.58×0.58m —— 近方形、边长落在柱窗口正中间，
    这正是标高符号 `∨` 笔画混进来的形态（实测 `col_sheet/quad4.png`）。
    """
    bar = _bar_poly(300.0, 300.0, 0.70, 0.12, 45.0)
    xs = [p[0] for p in bar]; ys = [p[1] for p in bar]
    aabb = (max(xs) - min(xs)) * SCALE_1_100_M_PER_PT
    assert 0.2 < aabb < 1.5, f"前提：包围盒 {aabb:.2f}m 必须落在柱窗口内"
    assert _columns(_plan([bar])) == []


def test_rotated_square_column_survives():
    """**菱形姿态的柱仍是柱**（`gold/CRITERIA.md#columns` 明列）。

    0.6m 方柱转 45° 后包围盒是 0.85m；换成真实尺寸后量到的仍是 0.6m。
    """
    assert len(_columns(_plan([_square_poly(300.0, 300.0, 0.6, 45.0)]))) == 1


def test_axis_aligned_column_unaffected():
    """轴对齐实心方柱：判据换了，结果不许变（零误伤基线）。"""
    assert len(_columns(_plan([_square_poly(300.0, 300.0, 0.6)]))) == 1


def test_small_column_on_column_layer_survives():
    """**图层判为柱的小截面构件不得被删** —— 本轮实测发现的误伤风险。

    结构平面图上有 0.18×0.18m、正落在轴线交点上的方块，走的是
    「图层明确标注为柱」这条路（`_is_plausible_column`，下限 0.1m）。
    真实尺寸只替换**量法**，不得把猜测路径的窗口（下限 0.2m）套到它头上，
    否则这道闸删掉的是真柱（实测见 `str_sheet/gate_drop.png`）。
    """
    geom = _plan([_square_poly(300.0, 300.0, 0.18)], layers=["S-COLS"])
    assert len(_columns(geom)) == 1


def test_slender_bar_on_column_layer_still_dropped():
    """图层说是柱也挡不住物理荒谬：真实短边 0.05m 的斜条不是柱。

    图层名是「是什么类型」的证据，不是「是不是真构件」的证据 ——
    与 `_is_plausible_column` 既有的纪律一致。
    """
    geom = _plan([_bar_poly(300.0, 300.0, 1.2, 0.05, 30.0)], layers=["S-COLS"])
    assert _columns(geom) == []
