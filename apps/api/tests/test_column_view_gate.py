"""剖面图上**不存在平面柱截面** —— 识别器却在那里猜出成百上千根柱。

**发现经过**（全库扫描 + 逐张渲染叠框核验，红=判据要删、绿=保留）：

| 图 | 图种 | 柱候选 | 图上实际是什么 |
|---|---|---:|---|
| 63 / 65 基坑西侧地质剖面展开图 | 剖面 | 338 / 338 | 钻孔柱状图的地层填充格 |
| 67 舞台深坑区域地质剖面展开图 | 剖面 | 198 | 同上 |
| 11 / 12 围护体剖面图（二）(三) | 剖面 | 54 / 51 | 立柱桩的横缀条、竣工章、标高刻度 |
| 1-1 剖面图（装修） | 剖面 | 17 | 同类 |

一根真柱都没有。

**判据来自制图规则而不是尺寸**：一个 3.5mm 字高的汉字在 1:100 下换算恰好
0.35 米，落在柱的尺寸窗口（0.2~1.5m）正中间 —— 尺寸判据分不开它们。
分得开的是**图种**：水平剖切得到的构件截面只出现在平面上。

**为什么只对剖面说不**（这是本轮量出来、并推翻了自己上一版的部分）：
立面与详图看着同理，实测各有反例，而反例删掉的正是**真柱** ——
「北立面构造柱定位图」每一格是「N 层北立面构造柱**平面**定位图」、
「楼梯 ST-18、19 结构详图」每一格是「楼梯 ST-xx 标高 -9.300 **结构平面图**」。
判据见 `drawing_conventions.shows_plan_cut_sections`。
"""
from __future__ import annotations

import pytest

from core.model3d import DrawingGeometry, recognize
from core.model3d.drawing_conventions import CLAUSES, shows_plan_cut_sections
from core.model3d.element_recognizer import SCALE_1_100_M_PER_PT

PAGE_W, PAGE_H = 842.0, 595.0
PT_PER_M = 1.0 / SCALE_1_100_M_PER_PT


@pytest.mark.unit
def test_section_shows_no_plan_cut_sections():
    """**核心用例**：剖面图上没有平面柱截面。"""
    assert not shows_plan_cut_sections("section")


@pytest.mark.unit
def test_every_other_view_type_is_left_alone():
    """立面/详图/平面/判不出 —— 一律不拦。

    立面与详图**不是漏写**：两者各有实测反例（图幅级图种 ≠ 图幅内每一格的
    图种），拦下去删的是真柱。`unknown`/空则是「缺失不得阻断」。
    """
    for view in ("elevation", "detail", "plan", "unknown", "", None):
        assert shows_plan_cut_sections(view), view


@pytest.mark.unit
def test_view_type_is_case_and_space_insensitive():
    """图种字符串来自上游服务，允许大小写与空白差异。"""
    assert not shows_plan_cut_sections(" Section ")


@pytest.mark.unit
def test_the_clause_registry_points_at_this_rule():
    """条款登记表是国标/工程约束的**单一来源**，新规则必须在册且指明生效处。"""
    clause = CLAUSES["plan_cut_sections"]
    assert "shows_plan_cut_sections" in clause["applied_in"]
    assert clause["evidence"], "登记的条款必须带实测依据"


# ── 接进识别器 ──────────────────────────────────────────────────
def _filled_blocks(layer: str = "") -> DrawingGeometry:
    """4 个 0.6m 见方的**填充**块 + 一排水平线（剖面图上的标高线）。

    尺寸落在柱窗口正中 —— 尺寸判据分不开，只有图种分得开。
    """
    geom = DrawingGeometry(page_w=PAGE_W, page_h=PAGE_H)
    span = 8.4 * PT_PER_M
    for i in range(3):
        y = 100.0 + i * span * 0.5
        geom.lines.append((30.0, y, PAGE_W - 30.0, y))
        geom.line_layers.append("AXIS")
    side = 0.6 * PT_PER_M
    for i in range(2):
        for j in range(2):
            geom.rects.append((120.0 + i * span, 110.0 + j * span * 0.5,
                               side, side, True))
            geom.rect_layers.append(layer)
            geom.rect_blocks.append("")
    geom.texts.append((60.0, 40.0, "1:100"))
    return geom


@pytest.mark.unit
def test_section_drawing_yields_no_guessed_columns():
    """**核心用例**：同一批几何，声明为剖面图就不再猜柱。"""
    geom = _filled_blocks()
    assert len(recognize(geom, "structure", "d1", view_type="plan").columns) == 4
    assert recognize(geom, "structure", "d1", view_type="section").columns == []


@pytest.mark.unit
def test_elevation_and_detail_are_not_gated():
    """**不得误伤**：立面与详图仍走原路径（各有含真柱的实测反例）。"""
    for view in ("elevation", "detail"):
        assert len(recognize(_filled_blocks(), "structure", "d2",
                             view_type=view).columns) == 4, view


@pytest.mark.unit
def test_column_layer_survives_the_gate_on_a_section():
    """图层明说是柱的照留 —— 闸关的是**猜测路径**，不是图层路径。

    与本模块既有的「墙图上仍保留图层柱」是同一条纪律。
    """
    result = recognize(_filled_blocks("S-COLS"), "structure", "d3",
                       view_type="section")
    assert len(result.columns) == 4


@pytest.mark.unit
def test_missing_view_type_keeps_old_behaviour():
    """不传图种时行为与改动前一致（零回归对照）。"""
    assert len(recognize(_filled_blocks(), "structure", "d4").columns) == 4
