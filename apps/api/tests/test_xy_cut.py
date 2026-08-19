"""XY-cut 块级版面分析 —— 工程图说明按**主题分块**散布。

**上一轮的局限**：单纯按 x 聚类分栏，对该图只检出 3 栏，
条文仍是拼接（`[3.28] …见图3.281**主要设计依据**建筑及设备工种…`）。
根因：工程图的说明**不是规则多栏**，而是若干说明块散布在图幅各处，
每块内部才是多栏或单栏。

**XY-cut**（文档版面分析的经典算法）：在投影直方图里找**空白带**，
在最宽处切分，递归下去。它的好处正是本轮反复需要的：
**确定性、无需调参、可解释** —— 切在哪、为什么切，都能说清。

递归顺序 y→x→y…：先横切成带，再纵切成块，块内再横切成行组。
"""
from __future__ import annotations

import pytest

from core.model3d.xy_cut import find_gaps, xy_cut


def _b(x0, y0, x1, y1, text=""):
    return {"bbox": [x0, y0, x1, y1], "text": text}


@pytest.mark.unit
def test_find_gaps_in_projection():
    """投影里的空白带 —— 区间 [起, 止)。"""
    spans = [(0.0, 10.0), (12.0, 20.0), (50.0, 60.0)]
    assert find_gaps(spans, min_gap=5.0) == [(20.0, 50.0)]


@pytest.mark.unit
def test_no_gap_when_dense():
    """密集排列时没有可切之处。"""
    assert find_gaps([(0.0, 10.0), (11.0, 20.0)], min_gap=5.0) == []


@pytest.mark.unit
def test_two_blocks_separated_vertically():
    """**核心用例**:上下两块被大片空白隔开 → 切成两块。"""
    tokens = [_b(10, 0, 100, 12, "块一行一"), _b(10, 14, 100, 26, "块一行二"),
              _b(10, 300, 100, 312, "块二行一")]
    blocks = xy_cut(tokens, min_gap=50.0)
    assert len(blocks) == 2
    assert [t["text"] for t in blocks[0]] == ["块一行一", "块一行二"]


@pytest.mark.unit
def test_two_columns_separated_horizontally():
    """左右两栏被大片空白隔开 → 切成两块（先横后纵都要能切）。"""
    tokens = [_b(10, 0, 100, 12, "左"), _b(10, 14, 100, 26, "左二"),
              _b(900, 0, 990, 12, "右"), _b(900, 14, 990, 26, "右二")]
    blocks = xy_cut(tokens, min_gap=50.0)
    assert len(blocks) == 2
    assert [t["text"] for t in blocks[0]] == ["左", "左二"]


@pytest.mark.unit
def test_nested_block_then_column():
    """**块内再分栏** —— 这正是工程图说明的实际形态。"""
    tokens = [
        _b(10, 0, 100, 12, "A左"), _b(900, 0, 990, 12, "A右"),
        _b(10, 400, 100, 412, "B左"), _b(900, 400, 990, 412, "B右"),
    ]
    blocks = xy_cut(tokens, min_gap=50.0)
    assert len(blocks) == 4
    assert [b[0]["text"] for b in blocks] == ["A左", "A右", "B左", "B右"]


@pytest.mark.unit
def test_reading_order_is_top_then_left():
    """块序:**先上后下、先左后右** —— 人读图的顺序。"""
    tokens = [_b(900, 400, 990, 412, "四"), _b(10, 0, 100, 12, "一"),
              _b(900, 0, 990, 12, "二"), _b(10, 400, 100, 412, "三")]
    blocks = xy_cut(tokens, min_gap=50.0)
    assert [b[0]["text"] for b in blocks] == ["一", "二", "三", "四"]


@pytest.mark.unit
def test_single_block_returns_itself():
    tokens = [_b(10, 0, 100, 12, "只此一块")]
    assert xy_cut(tokens, min_gap=50.0) == [tokens]


@pytest.mark.unit
def test_empty():
    assert xy_cut([], min_gap=50.0) == []
    assert xy_cut(None, min_gap=50.0) == []


@pytest.mark.unit
def test_tokens_without_bbox_are_kept():
    """**无位置的不丢** —— 单独成块附在末尾。"""
    tokens = [_b(10, 0, 100, 12, "有位置"), {"text": "无位置"}]
    blocks = xy_cut(tokens, min_gap=50.0)
    assert blocks[-1][0]["text"] == "无位置"
