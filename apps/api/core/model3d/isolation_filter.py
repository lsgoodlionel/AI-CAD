"""柱候选的「孤立度」闸：身边挤着别的候选的，多半不是柱。

## 为什么

柱的尺寸窗口（0.2~1.5m）装得下文字笔画（这批 PDF 的文字是轮廓化的多边形）、
填充图案的小块、标高符号、座椅 —— 尺寸判据分不开。分开它们的是**身边有没有同类**：
真柱之间隔着一个跨度（8.4m 轴距 ÷ 0.6m 柱 ≈ 14），而文字、填充、座椅是一簇一簇的。

`dense_array_filter` 只管「等间距成排、≥5 个一组」的规则阵列（座椅）；
文字与填充不成规则排，它管不到。本模块不要求成排，只看最近邻。

## 量纲

孤立度 = 最近一个**别的对象**的中心距 ÷ 自身较大边长 —— 与图纸比例无关。
「别的对象」：中心距小于 `SAME_OBJECT_RATIO` × 自身边长的视为同一对象
（同一根柱的外框与内部填充几乎同心），不算邻居。

## 阈值

由调用方给出。实测依据（col3 判读格子、`dense_array_filter` 模块文档）写在接线处。
"""
from __future__ import annotations

import math
from collections import defaultdict

from .dense_array_filter import center_size

#: 中心距小于此倍数 × 自身较大边长的两个候选视为同一对象（外框与填充）。
SAME_OBJECT_RATIO = 0.5


def find_crowded_flags(elements: list[dict] | None, *, min_isolation: float) -> list[bool]:
    """逐候选：最近的别的对象近于 `min_isolation` × 自身较大边长 → True（拥挤）。

    返回与入参等长的布尔表；入参不被修改；轮廓退化的候选不判（False）。
    用网格查近邻：格子边长取「最大边长 × min_isolation」，只看相邻 3×3 格，
    结果与两两比较一致（测试对拍），候选上限 2000 时不必 O(n²)。
    """
    items = list(elements or [])
    flags = [False] * len(items)
    measured = [(i, *cs) for i, el in enumerate(items) if (cs := center_size(el)) is not None]
    if len(measured) < 2:
        return flags
    cell = max(s for _, _, _, s in measured) * max(min_isolation, SAME_OBJECT_RATIO)
    grid: dict[tuple[int, int], list[tuple[int, float, float, float]]] = defaultdict(list)
    for node in measured:
        grid[(math.floor(node[1] / cell), math.floor(node[2] / cell))].append(node)
    for i, cx, cy, side in measured:
        gx, gy = math.floor(cx / cell), math.floor(cy / cell)
        limit = min_isolation * side
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for j, ox, oy, _s in grid.get((gx + dx, gy + dy), ()):
                    if j == i:
                        continue
                    dist = math.hypot(ox - cx, oy - cy)
                    if SAME_OBJECT_RATIO * side <= dist < limit:
                        flags[i] = True
                        break
                if flags[i]:
                    break
            if flags[i]:
                break
    return flags
