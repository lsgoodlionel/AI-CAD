"""板配筋、板洞不是板 —— 图层命中路径也要过非构件闸。

**实测**（大歌剧院 `S-0-20-102.04C` 南区一层结构平面图（三），1:150）：
当前代码产出 **139 块「板」，没有一块是板**：

| 图层（剥离 xref 前缀后） | 块数 | 围合面积 ≥10m² 的 |
|---|---:|---:|
| `0S-SLAB-HOLE`（板洞） | 77 | 17 |
| `0S-SLAB-RBAR`（板配筋） | 52 | 2 |
| `0S-SLAB-HOLE-2` | 10 | 8 |

图层名都含 `SLAB`，于是 `classify_by_layer` 判成 slab；而 `_find_slabs` 的
图层命中路径**从不过非构件闸**（兜底路径过）。配筋是开口折线，按多边形
闭合后成了横跨整页的三角形。2026-09-10 的「图元配额按类分配」放进了更多
多边形，这批假板随之冒出来（旧场景同图只有 12 块）。

AIA CAD Layer Guidelines：`RBAR` 是钢筋的次级码（闸已认 `REBAR/REIN`，
漏了 `RBAR`）；`HOLE` 是开洞 —— 画的是**没有**楼板的地方。
"""
from __future__ import annotations

import pytest

from core.model3d.layer_conventions import is_non_component_layer

XREF = "S-南区-PLAN-1F - 板配筋$0$"


@pytest.mark.unit
@pytest.mark.parametrize("layer", [
    f"{XREF}0S-SLAB-RBAR",
    f"{XREF}S-南区-PLAN-HALL$0$0S-SLAB-HOLE",
    f"{XREF}S-南区-PLAN-1F$0$0S-SLAB-HOLE-2",
    "S-RBAR",
    "0S_SLAB_HOLE",
])
def test_rebar_and_opening_layers_produce_no_components(layer):
    assert is_non_component_layer(layer)


@pytest.mark.unit
@pytest.mark.parametrize("layer", [
    "S-SLAB",
    f"{XREF}0S-COLS-HATCH",
    "S-SLAB-WHOLE",          # 边界：HOLE 必须是独立的次级码
    "A-DOOR-PANEL",
])
def test_real_component_layers_are_not_gated(layer):
    assert not is_non_component_layer(layer)


@pytest.mark.unit
def test_layered_slab_path_skips_gated_layers():
    """**接线用例**：三块同样大的多边形，只有真板层那块成板。"""
    from core.model3d.element_recognizer import SLAB_BASIS_RECOGNISED, recognize
    from core.model3d.types import DrawingGeometry

    def rect(x0, y0, w, h):
        return [(x0, y0), (x0 + w, y0), (x0 + w, y0 + h), (x0, y0 + h)]

    geom = DrawingGeometry(page_w=3370.0, page_h=2384.0)
    for i, layer in enumerate((f"{XREF}0S-SLAB-RBAR", f"{XREF}0S-SLAB-HOLE", "S-SLAB")):
        geom.polys.append(rect(200 + i * 900, 400, 800, 800))
        geom.poly_layers.append(layer)
        geom.poly_blocks.append("")

    result = recognize(geom, "structure", "slab-gate", scale_override=0.0529167)
    layered = [s for s in result.slabs if s.get("basis") == SLAB_BASIS_RECOGNISED]
    assert len(layered) == 1, f"期望只有 S-SLAB 那块，实得 {len(layered)}"
