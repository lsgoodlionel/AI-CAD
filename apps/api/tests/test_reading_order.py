"""多栏阅读顺序恢复 —— 条文重组的前置。

**实测失败**：直接按提取顺序重组说明文字，3352 行只归出 7 个条目、
平均 6493 字，内容是多个不相关条文的拼接：

    [7.1] 对于大体积混凝土…用塑料薄膜覆盖，《砌体填充墙》12SG614－1。D
    [8.2.2] 地下室顶板纵向钢筋全断…若顶板厚度小于400mm,2.8．《补偿收缩…》

根因：**PDF 文字提取顺序不是阅读顺序**。工程图的说明是**多栏排版**
（通常 3~5 栏），按提取顺序读会在栏间反复跳跃。

人能读懂说明，前提是**知道怎么按栏读** —— 这个隐含前提此前漏了。

做法：x 坐标聚类分栏 → 栏内按 y 排序 → 栏间按 x 排序。
位置数据实测齐备（3302/3352 带 x/y）。
"""
from __future__ import annotations

import pytest

from core.model3d.reading_order import detect_columns, sort_by_reading_order


def _t(x, y, text):
    return {"x": float(x), "y": float(y), "text": text}


@pytest.mark.unit
def test_two_columns_detected():
    """**核心用例**:两栏文字按 x 分开。"""
    tokens = [_t(100, 10, "左1"), _t(100, 20, "左2"),
              _t(800, 10, "右1"), _t(800, 20, "右2")]
    assert len(detect_columns(tokens)) == 2


@pytest.mark.unit
def test_reading_order_is_column_major():
    """**先读完一栏再读下一栏** —— 这正是人读工程图说明的方式。"""
    tokens = [_t(800, 10, "右1"), _t(100, 20, "左2"),
              _t(100, 10, "左1"), _t(800, 20, "右2")]
    assert [t["text"] for t in sort_by_reading_order(tokens)] == [
        "左1", "左2", "右1", "右2"]


@pytest.mark.unit
def test_single_column_falls_back_to_y_order():
    """单栏时就是从上到下 —— 不该因为分栏逻辑而乱序。"""
    tokens = [_t(100, 30, "三"), _t(105, 10, "一"), _t(98, 20, "二")]
    assert [t["text"] for t in sort_by_reading_order(tokens)] == ["一", "二", "三"]


@pytest.mark.unit
def test_same_line_tokens_ordered_left_to_right():
    """**同一行内按 x 从左到右** —— 一行被切成多个 token 时要拼对。"""
    tokens = [_t(160, 10, "界"), _t(100, 10, "世"), _t(130, 10, "你好")]
    assert [t["text"] for t in sort_by_reading_order(tokens)] == ["世", "你好", "界"]


@pytest.mark.unit
def test_five_columns_typical_of_drawings():
    """工程图说明常见 3~5 栏。"""
    tokens = [_t(200 + col * 600, row * 12, f"c{col}r{row}")
              for col in range(5) for row in range(3)]
    ordered = [t["text"] for t in sort_by_reading_order(tokens)]
    assert ordered[:3] == ["c0r0", "c0r1", "c0r2"]
    assert ordered[-3:] == ["c4r0", "c4r1", "c4r2"]


@pytest.mark.unit
def test_slightly_ragged_column_still_one_column():
    """**栏内 x 有抖动**(缩进、居中)不该被拆成多栏。"""
    tokens = [_t(100, 10, "a"), _t(118, 20, "b"), _t(96, 30, "c")]
    assert len(detect_columns(tokens)) == 1


@pytest.mark.unit
def test_missing_position_tokens_are_kept_last():
    """**没有位置的 token 不丢** —— 追加在末尾,保持原相对顺序。"""
    tokens = [{"text": "无位置1"}, _t(100, 10, "有位置"), {"text": "无位置2"}]
    got = [t["text"] for t in sort_by_reading_order(tokens)]
    assert got == ["有位置", "无位置1", "无位置2"]


@pytest.mark.unit
def test_empty():
    assert sort_by_reading_order([]) == []
    assert detect_columns([]) == []


# ── 行合并（实测:档案层粒度是**单字符**，不是行）─────────────────

@pytest.mark.unit
def test_tokens_merge_into_lines():
    """**实测根因**:档案层存的是单字符 token —— `1.2` 这样的条文号
    永远不会出现在单个 token 里,条文重组的输入假设从根上就错了。

    合并:同一行(y 容差内)按 x 拼串。
    """
    from core.model3d.reading_order import merge_into_lines

    tokens = [_t(100, 10, "1"), _t(106, 10, "."), _t(112, 10, "2"),
              _t(120, 10, "保"), _t(130, 10, "护层"),
              _t(100, 24, "下"), _t(110, 24, "一行")]
    assert merge_into_lines(tokens) == ["1.2保护层", "下一行"]


@pytest.mark.unit
def test_duplicate_tokens_at_same_position_collapse():
    """**同位置重复入库**(实测同一点 8 条 `A`、4 条 `筑`)必须去重,
    否则一行会被写成 `AAAAAAAA`。"""
    from core.model3d.reading_order import merge_into_lines

    tokens = [_t(60, 131, "A")] * 8 + [_t(75, 131, "区")] * 3
    assert merge_into_lines(tokens) == ["A区"]


@pytest.mark.unit
def test_columns_merge_independently():
    """分栏后各栏独立成行 —— 不能把两栏的同一 y 拼成一行。"""
    from core.model3d.reading_order import merge_into_lines

    tokens = [_t(100, 10, "左"), _t(900, 10, "右"),
              _t(100, 24, "边"), _t(900, 24, "侧")]
    assert merge_into_lines(tokens) == ["左", "边", "右", "侧"]


@pytest.mark.unit
def test_merge_empty():
    from core.model3d.reading_order import merge_into_lines

    assert merge_into_lines([]) == []
    assert merge_into_lines(None) == []


@pytest.mark.unit
def test_bbox_position_is_accepted():
    """**实测**:档案层有**两种位置结构** —— OCR 存 `{"bbox": [...]}`、
    矢量文字存 `{"x":…, "y":…}`。只认后者会让 **2469 条 OCR 记录
    全部被判「无位置」**,版面分析直接失效(检出 0 栏)。
    """
    from core.model3d.reading_order import merge_into_lines

    tokens = [{"bbox": [100, 10, 130, 22], "text": "左"},
              {"bbox": [900, 10, 930, 22], "text": "右"},
              {"bbox": [100, 30, 130, 42], "text": "边"}]
    assert merge_into_lines(tokens) == ["左", "边", "右"]


@pytest.mark.unit
def test_bbox_and_xy_mixed():
    """两种结构混用时都要认。"""
    from core.model3d.reading_order import sort_by_reading_order

    tokens = [{"bbox": [100, 30, 130, 42], "text": "下"},
              _t(100, 10, "上")]
    assert [t["text"] for t in sort_by_reading_order(tokens)] == ["上", "下"]
