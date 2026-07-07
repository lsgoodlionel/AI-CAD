"""构件识别（DrawingGeometry → FloorElements）测试。

合成图纸：A1 尺寸 842×595pt，比例 1:100 → 1pt ≈ 0.0353m。
"""
import pytest

from core.model3d import DrawingGeometry, FloorElements, recognize
from core.model3d.element_recognizer import SCALE_1_100_M_PER_PT

PAGE_W, PAGE_H = 842.0, 595.0
# 1:100 下每米对应的页面点数（≈PT_PER_M pt/m）
PT_PER_M = 1.0 / SCALE_1_100_M_PER_PT


def _build_plan(*, with_scale_text: bool = True) -> DrawingGeometry:
    """构造结构平面：轴网 + 4 根柱（填充矩形）+ 一段双线墙。"""
    geom = DrawingGeometry(page_w=PAGE_W, page_h=PAGE_H)
    ox, oy = 100.0, 100.0            # 轴网原点（页面坐标）
    span = 8.4 * PT_PER_M            # 8.4m 轴距 ≈238pt
    # 轴网长线（纵横各 3 条，长度 >60% 页幅）
    for i in range(3):
        x = ox + i * span
        geom.lines.append((x, 30.0, x, PAGE_H - 30.0))
        y = oy + i * span * 0.5     # 纵向轴距 4.2m，页高有限
        geom.lines.append((30.0, y, PAGE_W - 30.0, y))
    # 4 根柱：0.6m×0.6m 填充矩形，位于轴交点
    col = 0.6 * PT_PER_M
    for i in range(2):
        for j in range(2):
            geom.rects.append((ox + i * span - col / 2, oy + j * span * 0.5 - col / 2, col, col, True))
    # 双线墙：间距 0.2m、长 6m 的两条平行横线
    wall_y = oy + 40.0
    wall_gap = 0.2 * PT_PER_M
    wall_len = 6.0 * PT_PER_M
    geom.lines.append((ox, wall_y, ox + wall_len, wall_y))
    geom.lines.append((ox, wall_y + wall_gap, ox + wall_len, wall_y + wall_gap))
    if with_scale_text:
        geom.texts.append((60.0, 40.0, "1:100"))
    geom.texts.append((400.0, 20.0, "一层墙柱结构平面图"))
    return geom


@pytest.mark.unit
def test_recognize_columns_with_metric_conversion():
    result = recognize(_build_plan(), "structure", "d1")
    assert isinstance(result, FloorElements)
    assert result.scale == pytest.approx(100 * 0.000352778, rel=0.01)
    assert len(result.columns) == 4
    # 柱轮廓边长应 ≈0.6m
    outline = result.columns[0]["outline"]
    xs = [p[0] for p in outline]
    assert max(xs) - min(xs) == pytest.approx(0.6, abs=0.1)
    assert result.columns[0]["src"] == "d1"


@pytest.mark.unit
def test_recognize_walls_from_parallel_pairs():
    result = recognize(_build_plan(), "structure", "d1")
    assert result.walls, "双线墙应被识别"
    wall = result.walls[0]
    assert 0.1 <= wall["width"] <= 0.4
    # 墙长 ≈6m
    (x0, y0), (x1, y1) = wall["path"][0], wall["path"][-1]
    assert ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5 == pytest.approx(6.0, abs=0.5)


@pytest.mark.unit
def test_recognize_axes_detected():
    result = recognize(_build_plan(), "structure", "d1")
    assert len(result.axes.get("x", [])) >= 3
    assert len(result.axes.get("y", [])) >= 3


@pytest.mark.unit
def test_recognize_slab_fallback_to_axis_envelope():
    result = recognize(_build_plan(), "structure", "d1")
    assert result.slabs, "无闭合外轮廓时应回退轴网包络板"
    assert result.slabs[0]["thickness"] == pytest.approx(0.12)


@pytest.mark.unit
def test_recognize_beams_only_on_beam_drawings():
    plan = _build_plan()
    assert recognize(plan, "structure", "d1").beams == []
    beam_plan = _build_plan()
    beam_plan.texts = [(400.0, 20.0, "一层主梁配筋图"), (60.0, 40.0, "1:100")]
    result = recognize(beam_plan, "structure", "d1")
    assert result.beams, "梁图上的平行线对应识别为梁"
    assert result.walls == [], "梁图不输出墙"


@pytest.mark.unit
def test_recognize_mep_pipes_and_system():
    geom = DrawingGeometry(page_w=PAGE_W, page_h=PAGE_H)
    # 一条 12m 长折线（两段线）
    geom.lines.append((100.0, 200.0, 100.0 + 6 * PT_PER_M, 200.0))
    geom.lines.append((100.0 + 6 * PT_PER_M, 200.0, 100.0 + 12 * PT_PER_M, 200.0))
    geom.texts.append((400.0, 20.0, "地下一层给排水平面图"))
    geom.texts.append((60.0, 40.0, "1:100"))
    result = recognize(geom, "mep", "d9")
    assert result.pipes
    assert result.pipes[0]["system"] == "给排水"
    assert result.pipes[0]["dia"] == pytest.approx(0.1)


@pytest.mark.unit
def test_recognize_mep_equipment_with_label():
    geom = DrawingGeometry(page_w=PAGE_W, page_h=PAGE_H)
    # 2m×1.5m 设备块 + 块内文本
    geom.rects.append((300.0, 300.0, 2.0 * PT_PER_M, 1.5 * PT_PER_M, False))
    geom.texts.append((310.0, 310.0, "水泵P-1"))
    geom.texts.append((60.0, 40.0, "1:100"))
    geom.texts.append((400.0, 20.0, "泵房给排水详图"))
    result = recognize(geom, "mep", "d9")
    assert result.equipment
    assert "水泵" in result.equipment[0]["label"]


@pytest.mark.unit
def test_recognize_truncates_over_limit():
    geom = DrawingGeometry(page_w=PAGE_W, page_h=PAGE_H)
    geom.lines = [(0.0, float(i % 500), 10.0, float(i % 500)) for i in range(25000)]
    result = recognize(geom, "structure", "d1")
    assert result.axes.get("truncated") is True


@pytest.mark.unit
def test_recognize_empty_geometry_returns_empty_elements():
    result = recognize(DrawingGeometry(), "structure", "d1")
    assert result.stats() == {
        "columns": 0, "walls": 0, "beams": 0, "slabs": 0, "pipes": 0, "equipment": 0,
    }
    assert result.scale > 0
