"""板的来源统计:识别出来的板与兜底补的板必须分开数。

**为什么单独一条**:上海大歌剧院模型 v30 报「21 块板」,看着像成果,
实际 13 层里 10 层恒为 2 块 —— 全部来自兜底(最大多边形 1 + 柱包络 1),
靠图层判出来的真板是 **0** 块。混在一个数字里,这个区别谁也看不出来。
"""
from __future__ import annotations

import pytest

from core.model3d.element_recognizer import (
    SLAB_BASIS_COLUMN_ENVELOPE, SLAB_BASIS_LARGEST_POLYGON,
    SLAB_BASIS_RECOGNISED,
)
from services.model_elements import EMPTY_ELEMENTS, element_stats, totals


def _slab(basis: str) -> dict:
    return {"outline": [[0, 0], [1, 0], [1, 1], [0, 1]],
            "thickness": 0.12, "basis": basis, "src": "d1"}


@pytest.mark.unit
def test_element_stats_separates_recognised_slabs_from_fallbacks():
    stats = element_stats({
        **{k: [] for k in EMPTY_ELEMENTS},
        "slabs": [_slab(SLAB_BASIS_RECOGNISED),
                  _slab(SLAB_BASIS_LARGEST_POLYGON),
                  _slab(SLAB_BASIS_COLUMN_ENVELOPE)],
    })
    assert stats["slabs"] == 3                  # 总数不变,向后兼容
    assert stats["slabs_recognised"] == 1       # 只有图层命中那块算识别


@pytest.mark.unit
def test_all_fallback_slabs_report_zero_recognised():
    """本项目的真实形态:全兜底 → 识别数必须是 0,不能显示成 N。"""
    stats = element_stats({
        **{k: [] for k in EMPTY_ELEMENTS},
        "slabs": [_slab(SLAB_BASIS_LARGEST_POLYGON),
                  _slab(SLAB_BASIS_COLUMN_ENVELOPE)],
    })
    assert stats["slabs"] == 2
    assert stats["slabs_recognised"] == 0


@pytest.mark.unit
def test_slab_without_basis_is_not_counted_as_recognised():
    """缺 basis 的旧数据按**兜底**处理 —— 不能默认算成识别成果。"""
    stats = element_stats({**{k: [] for k in EMPTY_ELEMENTS},
                           "slabs": [{"outline": [], "thickness": 0.12}]})
    assert stats["slabs"] == 1 and stats["slabs_recognised"] == 0


@pytest.mark.unit
def test_totals_carries_recognised_slabs_across_floors():
    floors = [
        {"element_stats": element_stats({**{k: [] for k in EMPTY_ELEMENTS},
                                         "slabs": [_slab(SLAB_BASIS_RECOGNISED)]})},
        {"element_stats": element_stats({**{k: [] for k in EMPTY_ELEMENTS},
                                         "slabs": [_slab(SLAB_BASIS_COLUMN_ENVELOPE)]})},
    ]
    agg = totals(floors)
    assert agg["slabs"] == 2
    assert agg["slabs_recognised"] == 1
