"""图框标题栏的**汉字笔画**被当成柱 —— 尺寸判不开，顶点数判得开。

**发现经过**（逐张渲染叠框核验）：「3结构详图（五）」的 190 个柱候选里，
密密麻麻一片落在图签栏的「审」「专业负责」与设计人姓名上，另有一片落在
竣工章的红格里。金标准 `data/model3d/gold/rule_vs_model_v1.json` 的 60 格
判读里 5 格判为 `text`，与此吻合；`CRITERIA.md#columns` 的「不算柱」
早已写死**图框、标题栏、竣工章、图签栏**与**文字（笔画本身也不算）**。

**为什么尺寸判不开**：3.5mm 字高的汉字在 1:100 下换算恰好 0.35 米，
落在柱的尺寸窗口（0.2~1.5m）正中间。那张图的比例还是按 8.4m 轴距**猜**
出来的 1:700，笔画于是被换算成 1.1 米。

**顶点数判得开**（全库扫描 1257 张随机样本 = 30.6%，21339 个柱候选）：

    源多边形顶点数
    ≤8 41.6% · 9~12 31.0% · 13~16 0.2% · 17~24 6.9% · 25~48 0.6% ·
    49~96 0.7% · >96 3.1%（另 15.9% 查不到）

图签栏内候选的顶点数中位是 **96**（汉字轮廓），而真柱是 **4~6**（四边形）、
真构造柱是 **4**。

**阈值取 48 而不是那条更漂亮的 13~16 空缝**：12 会删掉
`test_element_recognizer_layers.py::test_many_point_column_keeps_its_full_extent`
里那根 40 点圆柱，而那条用例记的是真事（实测 51% 的多边形超过 8 个点）。
先量后改的另一半是：量出来的缝也要先拿既有用例对一遍。

**判据与图种无关**，所以平面图上的图签栏同样管得住 —— 这正是它比
「按图种关掉整张图」强的地方：后者实测会删掉真柱
（见 `test_column_view_gate.py`）。
"""
from __future__ import annotations

import pytest

from core.model3d import DrawingGeometry, recognize
from core.model3d.element_recognizer import (
    MAX_COLUMN_OUTLINE_POINTS,
    SCALE_1_100_M_PER_PT,
    _is_component_outline,
)

PAGE_W, PAGE_H = 842.0, 595.0
PT_PER_M = 1.0 / SCALE_1_100_M_PER_PT


def _ring(n: int, cx: float, cy: float, r: float) -> list[tuple[float, float]]:
    """n 个点的闭合环（近圆）。用来造「复杂路径」而不必写死坐标。"""
    import math

    return [(cx + r * math.cos(2 * math.pi * i / n),
             cy + r * math.sin(2 * math.pi * i / n)) for i in range(n)]


@pytest.mark.unit
def test_quadrilateral_is_a_component_outline():
    """**核心用例**：柱截面是四边形。"""
    assert _is_component_outline([(0, 0), (10, 0), (10, 10), (0, 10)])


@pytest.mark.unit
def test_flattened_circle_is_a_component_outline():
    """圆柱经贝塞尔折线化后 8 个点，**必须留**。"""
    assert _is_component_outline(_ring(8, 0, 0, 5))


@pytest.mark.unit
def test_glyph_sized_path_is_not_a_component_outline():
    """汉字轮廓实测中位 96 个点 —— 不是构件截面。"""
    assert not _is_component_outline(_ring(96, 0, 0, 5))


@pytest.mark.unit
def test_threshold_stays_above_the_measured_many_point_column():
    """阈值必须**高于 40** —— `test_many_point_column_keeps_its_full_extent`
    记的那根 40 点圆柱是真事（实测 51% 的多边形超过 8 个点）。

    这条断言是给未来收紧阈值的人看的：往下调会删真柱。
    """
    assert MAX_COLUMN_OUTLINE_POINTS > 40
    assert _is_component_outline(_ring(40, 0, 0, 5))
    assert _is_component_outline(_ring(MAX_COLUMN_OUTLINE_POINTS, 0, 0, 5))
    assert not _is_component_outline(_ring(MAX_COLUMN_OUTLINE_POINTS + 1, 0, 0, 5))


def _plan_with_glyphs(glyph_points: int) -> DrawingGeometry:
    """一张平面图：2 个真柱（四边形填充）+ 2 个「笔画」（复杂填充路径）。

    两者尺寸相同 —— 尺寸判据分不开，正是实测里发生的事。
    """
    geom = DrawingGeometry(page_w=PAGE_W, page_h=PAGE_H)
    span = 8.4 * PT_PER_M
    for i in range(2):
        x = 120.0 + i * span
        geom.lines.append((x, 30.0, x, PAGE_H - 30.0))
        geom.line_layers.append("AXIS")
        y = 110.0 + i * span * 0.5
        geom.lines.append((30.0, y, PAGE_W - 30.0, y))
        geom.line_layers.append("AXIS")
    side = 0.6 * PT_PER_M
    for i in range(2):                                   # 真柱：四边形
        cx, cy = 120.0 + i * span, 110.0
        geom.polys.append([(cx, cy), (cx + side, cy),
                           (cx + side, cy + side), (cx, cy + side)])
        geom.poly_layers.append("")
        geom.poly_blocks.append("")
    for i in range(2):                                   # 笔画：同尺寸复杂路径
        geom.polys.append(_ring(glyph_points, 700.0, 120.0 + i * 40.0, side / 2))
        geom.poly_layers.append("")
        geom.poly_blocks.append("")
    geom.texts.append((60.0, 40.0, "1:100"))
    return geom


@pytest.mark.unit
def test_glyph_paths_do_not_become_columns():
    """**核心用例**：同尺寸下，四边形进模型、笔画不进。"""
    result = recognize(_plan_with_glyphs(96), "structure", "d1", view_type="plan")
    assert len(result.columns) == 2


@pytest.mark.unit
def test_simple_paths_of_the_same_size_still_do():
    """零回归对照：把「笔画」换成 8 点简单环，4 个都该留。"""
    result = recognize(_plan_with_glyphs(8), "structure", "d2", view_type="plan")
    assert len(result.columns) == 4


@pytest.mark.unit
def test_shape_gate_also_applies_to_the_layer_path():
    """图层明说是柱也不能让 96 个点的路径过 —— 图层答的是「是什么类型」，
    答不了「是不是一个真构件」（与 `_is_plausible_column` 同一条纪律）。
    """
    geom = _plan_with_glyphs(96)
    geom.poly_layers = ["S-COLS"] * len(geom.polys)
    assert len(recognize(geom, "structure", "d3", view_type="plan").columns) == 2
