"""柱候选的「孤立度」：最近一个**别的**候选的中心距 ÷ 自身较大边长。

真柱之间隔着一个跨度（8.4m 轴距 ÷ 0.6m 柱 ≈ 14）；文字笔画、填充、座椅挨着排，
比值在 1~2 之间（col3 判读格子实测，见 `isolation_filter` 模块文档）。
阈值由调用方显式给出 —— 在用金标准定下之前不写默认值。
"""
from __future__ import annotations

import math
import random

import pytest

from core.model3d.isolation_filter import find_crowded_flags


def _sq(cx, cy, side):
    h = side / 2
    return {"outline": [[cx - h, cy - h], [cx + h, cy - h], [cx + h, cy + h], [cx - h, cy + h]]}


@pytest.mark.unit
def test_column_grid_is_not_crowded():
    """8.4m 轴距的 0.6m 柱网：比值 14，一根都不该被标。"""
    grid = [_sq(x * 8.4, y * 8.4, 0.6) for x in range(5) for y in range(4)]
    assert find_crowded_flags(grid, min_isolation=3.0) == [False] * len(grid)


@pytest.mark.unit
def test_glyph_like_cluster_is_crowded():
    """文字/填充那样挨着排（间距 = 1.1 × 自身尺寸）：全部标出。"""
    row = [_sq(i * 0.33, 0.0, 0.3) for i in range(6)]
    assert all(find_crowded_flags(row, min_isolation=3.0))


@pytest.mark.unit
def test_outline_and_its_own_fill_are_one_object():
    """同一根柱的外框与内部填充几乎同心 —— 不能算成彼此的邻居，否则真柱会被自己挤掉。"""
    column = [_sq(0.0, 0.0, 0.6), _sq(0.02, 0.0, 0.5)]
    assert find_crowded_flags(column, min_isolation=3.0) == [False, False]


@pytest.mark.unit
def test_isolated_column_next_to_a_distant_cluster_is_kept():
    items = [_sq(0.0, 0.0, 0.6)] + [_sq(20 + i * 0.33, 0.0, 0.3) for i in range(5)]
    flags = find_crowded_flags(items, min_isolation=3.0)
    assert flags[0] is False
    assert all(flags[1:])


@pytest.mark.unit
def test_degenerate_outlines_are_left_alone_and_input_is_untouched():
    items = [{"outline": []}, {"outline": [[0, 0], [0, 0], [0, 0]]}, _sq(0, 0, 0.6)]
    snapshot = [dict(i) for i in items]
    assert find_crowded_flags(items, min_isolation=3.0) == [False, False, False]
    assert items == snapshot


@pytest.mark.unit
def test_spatial_index_agrees_with_brute_force():
    """网格查近邻只是加速，结果必须与两两比较逐项一致。"""
    rng = random.Random(7)
    items = [_sq(rng.uniform(0, 60), rng.uniform(0, 40), rng.uniform(0.2, 1.2))
             for _ in range(400)]

    def brute(k):
        cs = []
        for it in items:
            xs = [p[0] for p in it["outline"]]; ys = [p[1] for p in it["outline"]]
            cs.append(((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2,
                       max(max(xs) - min(xs), max(ys) - min(ys))))
        out = []
        for i, (x, y, s) in enumerate(cs):
            best = math.inf
            for j, (u, v, _t) in enumerate(cs):
                d = math.hypot(u - x, v - y)
                if i != j and d >= 0.5 * s:
                    best = min(best, d)
            out.append(best < k * s)
        return out

    for k in (1.5, 3.0, 6.0):
        assert find_crowded_flags(items, min_isolation=k) == brute(k)
