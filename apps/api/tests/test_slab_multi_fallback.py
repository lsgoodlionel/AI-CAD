"""无图层命中时，兜底也该产出**多块**板 —— 实测每层只有 1~6 块。

**实测**（大歌剧院 v75，14 层）：

| 层 | 板 | 柱 |
|---|---:|---:|
| B1 | **5** | 2681 |
| F1 | **3** | 1400 |
| F3 | **1** | 790 |

根因：板识别的图层路径可产出多块，而**三条兜底各只返回 1 块**
（最大多边形 / 轴网包络 / 柱包络）。大歌剧院图层命中率仅 6.6%，
绝大多数图走兜底 ⇒ 每图 1 块。

改进：兜底一改为收**所有面积达标且互不包含**的多边形。
「互不包含」是关键 —— 结构平面图上大轮廓常层层嵌套
（外墙轮廓套房间轮廓套洞口），全收会把同一块楼板数很多遍。

依据仍标 `SLAB_BASIS_LARGEST_POLYGON`（它本就不是识别结果，
统计要能与图层命中的真板分开数）。
"""
from __future__ import annotations

import pytest

from core.model3d.element_recognizer import pick_fallback_slab_polygons


def _square(x0: float, y0: float, size: float) -> list[tuple[float, float]]:
    return [(x0, y0), (x0 + size, y0), (x0 + size, y0 + size), (x0, y0 + size)]


@pytest.mark.unit
def test_multiple_disjoint_polygons_all_kept():
    """**核心用例**:三块互不重叠的大多边形 → 三块板。"""
    polys = [_square(0, 0, 20), _square(50, 0, 20), _square(0, 50, 20)]
    picked = pick_fallback_slab_polygons(polys, area_of=lambda p: 400.0)
    assert len(picked) == 3


@pytest.mark.unit
def test_nested_polygons_keep_only_the_outer():
    """**互不包含** —— 嵌套轮廓只取外层,否则同一块楼板数很多遍。"""
    outer = _square(0, 0, 100)
    inner = _square(10, 10, 20)
    innermost = _square(15, 15, 5)
    picked = pick_fallback_slab_polygons(
        [inner, outer, innermost],
        area_of=lambda p: (max(x for x, _ in p) - min(x for x, _ in p)) ** 2)
    assert len(picked) == 1
    assert picked[0] is outer


@pytest.mark.unit
def test_small_polygons_are_dropped():
    """面积不达标的不算板 —— 由调用方传入的 area_of 判定。"""
    polys = [_square(0, 0, 2), _square(50, 0, 20)]
    picked = pick_fallback_slab_polygons(
        polys, area_of=lambda p: 4.0 if len(p) and p[1][0] - p[0][0] < 10 else 400.0,
        min_area=10.0)
    assert len(picked) == 1


@pytest.mark.unit
def test_cap_limits_the_count():
    """**上限兜底** —— 兜底不是识别,不该无限产出。"""
    polys = [_square(i * 50, 0, 20) for i in range(20)]
    picked = pick_fallback_slab_polygons(polys, area_of=lambda p: 400.0, cap=5)
    assert len(picked) == 5


@pytest.mark.unit
def test_empty_input():
    assert pick_fallback_slab_polygons([], area_of=lambda p: 0.0) == []
