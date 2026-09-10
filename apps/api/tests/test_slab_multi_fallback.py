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


# ── 图框不是楼板（实测 8/8 兜底图，最大两块都是图框）───────────────────

@pytest.mark.unit
def test_frame_layer_polygon_is_not_a_slab():
    """**图框画的是纸不是楼** —— 它面积最大，却不该产出板。

    实测（12 张平面图，8 张走兜底）：**8/8** 的面积最大多边形都在
    `C-SHET-TTLB` 图层上，占图幅 0.97~1.00，4 点轴对齐矩形；
    次大的是同层 48 点内框（图框是双线框），占 0.91~0.95。

    最极端一张「北区（小歌剧厅）三层墙柱平面图」挑中 **581,023 m²**
    —— 一整层实际只有 2000~8000 m²。该板经 `services/model_qto.py`
    进算量、汇总进创效提案，是三审审批人看的数字。
    """
    frame = _square(0, 0, 1000)          # 图框：面积最大
    room = _square(10, 10, 100)          # 真候选：被图框套住
    picked = pick_fallback_slab_polygons(
        [frame, room],
        area_of=lambda p: (max(x for x, _ in p) - min(x for x, _ in p)) ** 2,
        layers=["C-SHET-TTLB", "S-SLAB"])
    assert picked == [room]


@pytest.mark.unit
def test_frame_does_not_swallow_candidates_via_containment():
    """**图框被排除后不能再套住别人** —— 这才是「每图只剩 1 块」的直接原因。

    `pick_fallback_slab_polygons` 的「被已选中套住就跳过」本是防同一块板
    数两遍；图框套住图上**一切**，于是候选全被跳过、只剩图框自己。
    排除必须发生在入选之前，被排除的框不得进入包含判定。
    """
    frame = _square(0, 0, 1000)
    rooms = [_square(10, 10, 100), _square(200, 10, 100), _square(400, 10, 100)]
    picked = pick_fallback_slab_polygons(
        [frame, *rooms],
        area_of=lambda p: (max(x for x, _ in p) - min(x for x, _ in p)) ** 2,
        layers=["图框A0", "S-SLAB", "S-SLAB", "S-SLAB"])
    assert len(picked) == 3          # 不接闸时这里是 1（只剩图框）


@pytest.mark.unit
def test_layers_are_optional_and_default_to_no_gate():
    """**不传图层就不拦** —— 无图层的图（大歌剧院部分 PDF）路径一字不变。"""
    polys = [_square(0, 0, 20), _square(50, 0, 20)]
    assert len(pick_fallback_slab_polygons(polys, area_of=lambda p: 400.0)) == 2


@pytest.mark.unit
def test_short_layer_list_does_not_crash():
    """并行列表长度不齐时按缺图层处理 —— 缺失不得阻断（`_at` 同一约定）。"""
    polys = [_square(0, 0, 20), _square(50, 0, 20), _square(100, 0, 20)]
    picked = pick_fallback_slab_polygons(
        polys, area_of=lambda p: 400.0, layers=["C-SHET-TTLB"])
    assert len(picked) == 2


@pytest.mark.unit
def test_recognize_wires_poly_layers_into_the_fallback():
    """**接线用例**：`_find_slabs` 必须把 `poly_layers` 真的传下去。

    上面四条测的是 `pick_fallback_slab_polygons` 自己 —— 调用点若没接上，
    它们照样全绿。这个项目在「接线静默失效」上栽过：P2 那条通道字段名
    写错、被宽泛 `except` 吞掉，**从未生效过**却一直显示正常
    （见 `docs/DEV_REVIEW_2026-07.md` §3）。故单独钉住调用点。

    构造：一张无图层命中的图 —— 图框大矩形在 `C-SHET-TTLB` 上，
    另有三块互不重叠的候选在真构件层上。断言产出的板里没有图框尺寸的。
    """
    from core.model3d.element_recognizer import (SLAB_BASIS_LARGEST_POLYGON,
                                                 recognize)
    from core.model3d.types import DrawingGeometry

    def _rect_pts(x0, y0, w, h):
        return [(x0, y0), (x0 + w, y0), (x0 + w, y0 + h), (x0, y0 + h)]

    geom = DrawingGeometry(page_w=2384.0, page_h=3370.0)
    geom.polys.append(_rect_pts(10, 10, 2360, 3350))     # 图框：面积最大、套住一切
    geom.poly_layers.append("C-SHET-TTLB")
    geom.poly_blocks.append("")
    for i in range(3):
        geom.polys.append(_rect_pts(100 + i * 600, 500, 500, 500))
        geom.poly_layers.append("A-FLOR")
        geom.poly_blocks.append("")

    result = recognize(geom, "structure", "wiring-check")
    slabs = [s for s in result.slabs
             if s.get("basis") == SLAB_BASIS_LARGEST_POLYGON]
    assert len(slabs) == 3, "图框未被排除（或排除后仍在套住候选）"

    widths = [max(p[0] for p in s["outline"]) - min(p[0] for p in s["outline"])
              for s in slabs]
    frame_w = 2360 * result.scale
    assert all(w < frame_w * 0.5 for w in widths), "产出里仍有图框尺寸的板"
