"""A-16 图层约定强化识别：修「柱必须 filled」漏检 + 图层/块名识别。

无图层信息时行为与原启发式一致（由 test_element_recognizer 覆盖 + 本文件对照用例）。
"""
import pytest

from core.model3d import DrawingGeometry, FloorElements, recognize
from core.model3d.element_recognizer import SCALE_1_100_M_PER_PT

PAGE_W, PAGE_H = 842.0, 595.0
PT_PER_M = 1.0 / SCALE_1_100_M_PER_PT


def _plan_unfilled_columns(layer: str) -> DrawingGeometry:
    """轴网 + 4 根【未填充】0.6m 柱矩形，图层=layer（并行列表对齐 append）。"""
    geom = DrawingGeometry(page_w=PAGE_W, page_h=PAGE_H)
    ox, oy = 100.0, 100.0
    span = 8.4 * PT_PER_M
    for i in range(3):
        x = ox + i * span
        geom.lines.append((x, 30.0, x, PAGE_H - 30.0))
        geom.line_layers.append("AXIS")
        y = oy + i * span * 0.5
        geom.lines.append((30.0, y, PAGE_W - 30.0, y))
        geom.line_layers.append("AXIS")
    col = 0.6 * PT_PER_M
    for i in range(2):
        for j in range(2):
            geom.rects.append(
                (ox + i * span - col / 2, oy + j * span * 0.5 - col / 2, col, col, False)
            )
            geom.rect_layers.append(layer)
            geom.rect_blocks.append("")
    geom.texts.append((60.0, 40.0, "1:100"))
    geom.texts.append((400.0, 20.0, "一层柱结构平面图"))
    return geom


@pytest.mark.unit
def test_unfilled_columns_recognized_via_layer():
    """S-COLU 图层的未填充柱矩形被识别（修复 filled 漏检）。"""
    result = recognize(_plan_unfilled_columns("S-COLU"), "structure", "d1")
    assert isinstance(result, FloorElements)
    assert len(result.columns) == 4


@pytest.mark.unit
def test_unfilled_columns_missed_without_layer():
    """无图层信息时未填充矩形按原启发式跳过（零回归对照）。"""
    result = recognize(_plan_unfilled_columns(""), "structure", "d1")
    assert result.columns == []


@pytest.mark.unit
def test_equipment_recognized_via_layer_block():
    """机电图：超尺寸阈值的具名设备块靠图层 M-EQPM 识别为设备。"""
    geom = DrawingGeometry(page_w=PAGE_W, page_h=PAGE_H)
    for x in (120.0, 360.0):
        geom.lines.append((x, 30.0, x, PAGE_H - 30.0))
        geom.line_layers.append("AXIS")
    for y in (120.0, 360.0):
        geom.lines.append((30.0, y, PAGE_W - 30.0, y))
        geom.line_layers.append("AXIS")
    big = 6.0 * PT_PER_M  # 超出 0.5~5m 设备尺寸阈值
    geom.rects.append((180.0, 180.0, big, big, False))
    geom.rect_layers.append("M-EQPM")
    geom.rect_blocks.append("SB-1")
    geom.texts.append((60.0, 40.0, "1:100"))
    geom.texts.append((200.0, 200.0, "水泵"))
    result = recognize(geom, "mep", "d9")
    assert result.equipment
    assert result.equipment[0]["label"] == "水泵"


@pytest.mark.unit
def test_pipe_system_from_layer():
    """机电图：管线系统由图层判定（消防）优先于全图关键词。"""
    geom = DrawingGeometry(page_w=PAGE_W, page_h=PAGE_H)
    for x in (120.0, 360.0):
        geom.lines.append((x, 30.0, x, PAGE_H - 30.0))
        geom.line_layers.append("AXIS")
    for y in (120.0, 360.0):
        geom.lines.append((30.0, y, PAGE_W - 30.0, y))
        geom.line_layers.append("AXIS")
    pipe_len = 5.0 * PT_PER_M
    geom.lines.append((150.0, 240.0, 150.0 + pipe_len, 240.0))
    geom.line_layers.append("消防")
    geom.texts.append((60.0, 40.0, "1:100"))
    result = recognize(geom, "mep", "d9")
    assert any(p["system"] == "消防" for p in result.pipes)
