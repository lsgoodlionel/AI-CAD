"""训练集切瓦片 —— 让构件以可检测的像素尺寸进入模型。

**为什么**：现役 `drawing_elements.pt` 是 yolov8n、`imgsz=800` 训**整张图**。
语料里最多的是 A0/A1 大图（页高 2412/2384 pt 共 1158 张），整张 A0 压到
长边 800px 等效只有 **17 DPI**，1:150 下 0.6m 的柱只有 **2.7px** —— YOLO
的可靠下限是 16~20px，模型根本看不见要学的东西。它的 mAP50 = 0.049
不是模型不行，是它从没见过柱。

**和几何抽取恰好相反**：几何抽取不该分块（实测慢 12 倍、零额外收益，
因为矢量几何没有尺寸约束）；训练必须分块，因为 YOLO 输入是定尺寸的，
整图只能压缩。所以：按 `render_budget.dpi_for_scale` 渲染（柱 ≥24px），
再切成 `imgsz` 见方的瓦片，瓦片间留重叠，不让跨缝的构件被切成两半都丢掉。
"""
from __future__ import annotations

import pytest

from core.model3d.tiling import boxes_in_tile, tile_origins


@pytest.mark.unit
def test_tiles_cover_the_whole_page():
    """每个像素至少落在一块瓦片里 —— 包括右边和下边的零头。"""
    origins = tile_origins(3000, 2000, tile=1024, overlap=0.2)
    covered_x = max(x for x, _ in origins) + 1024
    covered_y = max(y for _, y in origins) + 1024
    assert covered_x >= 3000 and covered_y >= 2000
    assert min(x for x, _ in origins) == 0 and min(y for _, y in origins) == 0


@pytest.mark.unit
def test_adjacent_tiles_overlap():
    """相邻瓦片要重叠 —— 否则跨缝的构件在两块里都被切成残片。"""
    origins = sorted({x for x, _ in tile_origins(3000, 1024, tile=1024, overlap=0.2)})
    steps = [b - a for a, b in zip(origins, origins[1:])]
    assert all(s <= 1024 * 0.8 + 1 for s in steps), f"步长 {steps} 超过 80%，重叠不足"


@pytest.mark.unit
def test_small_page_is_one_tile():
    """比一块瓦片还小的页就是一块，不越界。"""
    assert tile_origins(600, 400, tile=1024, overlap=0.2) == [(0, 0)]


@pytest.mark.unit
def test_box_fully_inside_tile_is_normalized():
    """瓦片内的框转成 YOLO 归一化 (cx, cy, w, h)。"""
    out = boxes_in_tile([(0, (100, 200, 148, 248))], origin=(0, 0), tile=1024)
    assert len(out) == 1
    cls, cx, cy, w, h = out[0]
    assert cls == 0
    assert cx == pytest.approx(124 / 1024) and cy == pytest.approx(224 / 1024)
    assert w == pytest.approx(48 / 1024) and h == pytest.approx(48 / 1024)


@pytest.mark.unit
def test_box_mostly_outside_is_dropped():
    """只露出一小角的框不收 —— 教模型认半截柱子是在教它错。"""
    out = boxes_in_tile([(0, (1000, 100, 1100, 200))], origin=(0, 0), tile=1024,
                        min_visible=0.5)
    assert out == [], "只露出 24% 的框应被丢弃"


@pytest.mark.unit
def test_box_mostly_inside_is_clipped():
    """大部分在瓦片里的框按瓦片边界裁剪后保留。"""
    out = boxes_in_tile([(0, (980, 100, 1040, 160))], origin=(0, 0), tile=1024,
                        min_visible=0.5)
    assert len(out) == 1
    _cls, cx, _cy, w, _h = out[0]
    assert (cx + w / 2) * 1024 == pytest.approx(1024), "右边界裁到瓦片边"


@pytest.mark.unit
def test_box_offset_by_tile_origin():
    """框坐标是整页像素，要减去瓦片原点。"""
    out = boxes_in_tile([(2, (1700, 900, 1740, 940))], origin=(1640, 820), tile=1024)
    cls, cx, cy, _w, _h = out[0]
    assert cls == 2
    assert cx * 1024 == pytest.approx(1720 - 1640)
    assert cy * 1024 == pytest.approx(920 - 820)
