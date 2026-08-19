"""标题栏**竖排**文字被逐字拆开 —— 实测单字符占 other 的 27%。

**实测**（大歌剧院 other 类别里的单字符，各约 360~378 次 ≈ 图纸总数的 16%）：

    图 378  目 373  核 373  总 368  对 366  专 365  审 365
    工 363  校 363  业 363  程 362  责 361  比 361  负 361

拼起来正是标题栏的中文字段名：
项**目** / **图**号 / **审核** / **校对** / **专业** / **比**例 /
**负责**人 / **总工程**师 —— **竖排文字被逐字提取**，每字一条记录。

这不只是噪声：它说明 `title_block` 类别里的**字段值也可能是碎片**，
而「图框字段区域记忆」正要靠这些字段定位。

先做保守处理：**单个中文字 + 属于标题栏标签用字** → 归为标签碎片。
不动多字文本（那些是真内容），也不动 ASCII 单字符（可能是轴号 `A`/`1`）。
"""
from __future__ import annotations

import pytest

from services.title_block_labels import is_label_fragment


@pytest.mark.unit
def test_single_chars_of_label_words_are_fragments():
    """**核心用例**:标题栏标签用字的单字碎片。"""
    for ch in ("图", "目", "核", "总", "对", "专", "审",
               "工", "校", "业", "程", "责", "比", "负"):
        assert is_label_fragment(ch), ch


@pytest.mark.unit
def test_meaningful_single_chars_are_not_fragments():
    """**不得误伤有意义的单字** —— 方位标记是真内容。"""
    for ch in ("东", "西", "南", "北", "上", "下", "左", "右"):
        assert not is_label_fragment(ch), ch


@pytest.mark.unit
def test_ascii_single_chars_are_not_fragments():
    """ASCII 单字符可能是**轴号**,不归碎片。"""
    for ch in ("A", "1", "B", "9", "K"):
        assert not is_label_fragment(ch), ch


@pytest.mark.unit
def test_multi_char_text_is_never_a_fragment():
    """多字文本是真内容,哪怕含标签用字。"""
    for text in ("审核", "比例", "总工程师", "图号", "专业负责人",
                 "工程做法", "对照表"):
        assert not is_label_fragment(text), text


@pytest.mark.unit
def test_empty_and_none():
    assert not is_label_fragment("")
    assert not is_label_fragment(None)
    assert not is_label_fragment("   ")
