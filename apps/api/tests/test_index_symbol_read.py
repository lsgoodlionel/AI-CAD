"""读出索引符号圈内的编号 —— 建立**平面图 ↔ 详图**的跳转关系。

GB/T 50001 §5：索引符号用水平直径分成上下两半 ——

    ┌─────┐
    │  3  │  ← 上半：详图编号
    ├─────┤
    │ 15  │  ← 下半：详图所在图纸编号（同张图时画一横 `—`）
    └─────┘

读出这两个数，就知道「这个部位的做法看 15 号图的 3 号详图」——
**那正是人核图时的跳转动作**，也是取得真实构造尺寸的路径。

裁剪要点：上下半区各自**只取圆内**，且留一点内缩避开圆弧本身
（弧线会被 OCR 当成字符笔画）。
"""
from __future__ import annotations

import pytest

from core.model3d.index_symbol import index_symbol_halves, parse_index_reference


def _circle(cx=100.0, cy=100.0, d=20.0) -> dict:
    return {"cx": cx, "cy": cy, "diameter_pt": d}


@pytest.mark.unit
def test_halves_are_inside_the_circle():
    """两个半区都必须落在圆内。"""
    top, bottom = index_symbol_halves(_circle())
    for rect in (top, bottom):
        x0, y0, x1, y1 = rect
        assert 90.0 <= x0 < x1 <= 110.0
        assert 90.0 <= y0 < y1 <= 110.0


@pytest.mark.unit
def test_top_is_above_bottom():
    """上半在上、下半在下 —— PDF 坐标 y 向下增。"""
    top, bottom = index_symbol_halves(_circle())
    assert top[3] <= bottom[1] + 1e-6


@pytest.mark.unit
def test_halves_avoid_the_arc():
    """**内缩避开圆弧** —— 弧线会被 OCR 当成字符笔画。"""
    top, _ = index_symbol_halves(_circle(d=20.0))
    x0, y0, x1, y1 = top
    assert x0 > 90.0 and x1 < 110.0, "横向要内缩"
    assert y0 > 90.0, "顶部要内缩"


@pytest.mark.unit
def test_parse_reference_pairs_the_two_reads():
    """**核心用例**:上下两次 OCR → 一条跳转引用。"""
    ref = parse_index_reference(["3"], ["15"])
    assert ref is not None
    assert ref.detail_no == "3" and ref.sheet_no == "15"
    assert not ref.same_sheet


@pytest.mark.unit
def test_dash_in_bottom_means_same_sheet():
    """§5：下半画一横表示**详图就在本张图上**。"""
    for dash in ("—", "-", "－", "──"):
        ref = parse_index_reference(["5"], [dash])
        assert ref is not None and ref.same_sheet, dash
        assert ref.sheet_no is None


@pytest.mark.unit
def test_noise_reads_are_rejected():
    """**读不出就不猜** —— 详图编号必须是数字或单字母。"""
    assert parse_index_reference([], ["15"]) is None
    assert parse_index_reference(["详图说明"], ["15"]) is None
    assert parse_index_reference(["3"], []) is None


@pytest.mark.unit
def test_letter_detail_numbers_allowed():
    """详图编号也可以是字母（`A/15`）。"""
    ref = parse_index_reference(["A"], ["15"])
    assert ref is not None and ref.detail_no == "A"
