"""训练集切瓦片（纯函数）。

现役模型 `imgsz=800` 训整张图：A0 压到长边 800px 等效 17 DPI，1:150 下
0.6m 的柱只有 2.7px，远低于 YOLO 16~20px 的可靠下限 —— 它的 mAP50 = 0.049
是因为从没见过柱。训练必须分块（与几何抽取相反：矢量几何没有尺寸约束，
分块只会慢；YOLO 输入是定尺寸的，整图只能压缩）。

先按 `render_budget.dpi_for_scale` 定渲染分辨率，再用本模块切块。
"""
from __future__ import annotations


def _axis_starts(length: float, tile: int, stride: int) -> list[int]:
    if length <= tile:
        return [0]
    starts = list(range(0, int(length) - tile + 1, stride))
    last = int(length) - tile
    if starts[-1] != last:
        starts.append(last)          # 右/下边的零头补一块，贴边对齐
    return starts


def tile_origins(page_w_px: float, page_h_px: float, *, tile: int = 1024,
                 overlap: float = 0.2) -> list[tuple[int, int]]:
    """覆盖整页的瓦片左上角坐标。相邻瓦片重叠 `overlap`，零头贴边补齐。"""
    stride = max(1, int(tile * (1.0 - overlap)))
    return [(x, y)
            for y in _axis_starts(page_h_px, tile, stride)
            for x in _axis_starts(page_w_px, tile, stride)]


def boxes_in_tile(
    boxes: list[tuple[int, tuple[float, float, float, float]]],
    *,
    origin: tuple[int, int],
    tile: int,
    min_visible: float = 0.5,
) -> list[tuple[int, float, float, float, float]]:
    """把整页像素框 `(类, (x0,y0,x1,y1))` 转成该瓦片的 YOLO 标注。

    露出不到 `min_visible` 的框丢弃 —— 教模型认半截构件是在教它错；
    其余按瓦片边界裁剪后归一化为 `(类, cx, cy, w, h)`。
    """
    ox, oy = origin
    out = []
    for cls, (x0, y0, x1, y1) in boxes:
        area = max(x1 - x0, 0.0) * max(y1 - y0, 0.0)
        if area <= 0:
            continue
        cx0, cy0 = max(x0 - ox, 0.0), max(y0 - oy, 0.0)
        cx1, cy1 = min(x1 - ox, float(tile)), min(y1 - oy, float(tile))
        vis = max(cx1 - cx0, 0.0) * max(cy1 - cy0, 0.0)
        if vis / area < min_visible:
            continue
        out.append((cls, (cx0 + cx1) / 2 / tile, (cy0 + cy1) / 2 / tile,
                    (cx1 - cx0) / tile, (cy1 - cy0) / tile))
    return out
