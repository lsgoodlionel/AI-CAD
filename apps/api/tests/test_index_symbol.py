"""索引符号识别（GB/T 50001 §5）—— 平面图跳转详图的**唯一线索**。

**为什么要做**：19 个专业 133 条会审检查项里「**节点号**」出现 19 条。
人核图时看到平面图上的索引符号，才知道去哪张详图看构造 ——
而详图里才有真实的构造尺寸，那是建模精度的关键。

**先证伪了文字路**：档案层里匹配 `1/A-15` 形态的 4929 条，全不是索引：

    BY/DATE   2220 次  ← 标题栏字段
    300/5A      70 次  ← 电流互感器变比
    MFZ/ABC5X3  10 次  ← 设备型号

因为 §5 的索引符号是**画成圆圈**的：水平直径把圆分成上下两半，
上半写详图编号、下半写图纸编号。文字提取时上下是**两个独立文本**，
永远拼不出 `1/A-15`。

⇒ **必须靠图形**：圆 + 圆内一条水平分割线（长度 ≈ 直径）。
这也正是它与**轴号圈**（§8.0.2，圆内只有一个字符、无分割线）的区别。
"""
from __future__ import annotations

import pytest

from core.model3d.index_symbol import has_horizontal_divider, split_index_symbols


def _circle(cx: float, cy: float, d: float = 20.0) -> dict:
    return {"cx": cx, "cy": cy, "diameter_pt": d}


@pytest.mark.unit
def test_divider_across_the_circle_is_detected():
    """**核心用例**:横贯圆心的水平线 → 索引符号。"""
    circle = _circle(100.0, 100.0, 20.0)
    strokes = [(90.0, 100.0, 110.0, 100.0)]     # 横贯，长度=直径
    assert has_horizontal_divider(circle, strokes)


@pytest.mark.unit
def test_axis_label_circle_has_no_divider():
    """**轴号圈没有分割线**（§8.0.2 圆内只有一个字符）。"""
    circle = _circle(100.0, 100.0, 20.0)
    strokes = [(95.0, 95.0, 105.0, 105.0),      # 字符笔画（斜）
               (98.0, 92.0, 98.0, 108.0)]       # 竖笔画
    assert not has_horizontal_divider(circle, strokes)


@pytest.mark.unit
def test_short_horizontal_stroke_is_not_a_divider():
    """**字符里的短横不算** —— 分割线必须接近直径长。

    `王`/`工`/`二` 这类字有短横，长度远小于直径。
    """
    circle = _circle(100.0, 100.0, 20.0)
    strokes = [(97.0, 100.0, 103.0, 100.0)]     # 长 6，仅 0.3 倍直径
    assert not has_horizontal_divider(circle, strokes)


@pytest.mark.unit
def test_divider_must_pass_near_the_centre():
    """线要过**圆心附近** —— 贴边的横线是别的图形。"""
    circle = _circle(100.0, 100.0, 20.0)
    far = [(90.0, 108.0, 110.0, 108.0)]         # 偏离圆心 8pt
    assert not has_horizontal_divider(circle, far)


@pytest.mark.unit
def test_tilted_line_is_not_a_divider():
    """**必须水平** —— §5 规定是水平直径。"""
    circle = _circle(100.0, 100.0, 20.0)
    tilted = [(90.0, 95.0, 110.0, 105.0)]       # 斜 26 度
    assert not has_horizontal_divider(circle, tilted)


@pytest.mark.unit
def test_split_separates_index_symbols_from_axis_circles():
    """**分流用例**:一批圆里分出索引符号与轴号圈。"""
    circles = [_circle(100.0, 100.0), _circle(200.0, 200.0)]
    strokes = [(90.0, 100.0, 110.0, 100.0)]     # 只有第一个圆有分割线
    index, axis = split_index_symbols(circles, strokes)
    assert len(index) == 1 and index[0]["cx"] == 100.0
    assert len(axis) == 1 and axis[0]["cx"] == 200.0


@pytest.mark.unit
def test_empty_inputs():
    assert split_index_symbols([], []) == ([], [])
    assert not has_horizontal_divider(_circle(0, 0), [])


# ── 直径判据（真实数据逼出）──────────────────────────────────

@pytest.mark.unit
def test_small_circles_are_not_index_symbols():
    """**实测误判**:栈桥详图上 13 个「索引符号」直径仅 **4.74mm**,
    裁开一看圈内暗像素 0% —— **本来就没字**。

    GB/T 50001 §5：索引符号圆直径 **8~10mm**。
    小圆是钢筋断面、圆点标记之类，与索引符号无关。
    """
    strokes = [(90.0, 100.0, 110.0, 100.0)]
    small = {"cx": 100.0, "cy": 100.0, "diameter_pt": 13.45}   # 4.74mm
    assert not has_horizontal_divider(small, strokes)


@pytest.mark.unit
def test_standard_diameter_circles_pass():
    """8~10mm（22.7~28.3pt）照常识别，留一档容差。"""
    strokes_at = lambda cy: [(80.0, cy, 120.0, cy)]
    for d_mm in (8.0, 9.0, 10.0):
        d_pt = d_mm * 72.0 / 25.4
        circle = {"cx": 100.0, "cy": 100.0, "diameter_pt": d_pt}
        assert has_horizontal_divider(circle, strokes_at(100.0)), d_mm


@pytest.mark.unit
def test_oversized_circles_are_rejected():
    """详图符号（§5 直径 14mm 粗实线圆）不是索引符号 —— 它标的是
    「我就是那张详图」，不是「去看那张详图」，语义相反。"""
    d_pt = 20.0 * 72.0 / 25.4        # 20mm
    circle = {"cx": 100.0, "cy": 100.0, "diameter_pt": d_pt}
    assert not has_horizontal_divider(circle, [(60.0, 100.0, 140.0, 100.0)])
