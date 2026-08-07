"""把匹配结果翻译成**轴号对应关系** —— 世界坐标由此而来（J1 收尾）。

匹配给出 `GapMatch(start_index, spans)`：局部第 i 段吃掉了 `spans[i]` 个锚距。
于是局部第 i 条**轴线**对应锚图第 `start_index + sum(spans[:i])` 条。

拿到轴号后，局部图的交点世界坐标 = 锚图同名轴号对的世界坐标 ——
这是 `axis_intersections` 的写入依据，也是 `placements_for_project` 求解摆放的输入。

**为什么必须双向**：交点要 x、y 两个轴号才能确定。实测 143 张匹配成功的图里
只有 **12 张双向**、131 张单向 —— 单向构不成交点，拿不到世界坐标。
"""
from __future__ import annotations

import pytest

from services.axis_sequence_match import GapMatch
from services.anchor_label_mapping import anchor_labels_for_local_axes


@pytest.mark.unit
def test_identity_match_maps_one_to_one():
    """无合并、从头对齐 ⇒ 逐条对应。"""
    match = GapMatch(start_index=0, spans=[1, 1, 1], scale_ratio=1.0)
    got = anchor_labels_for_local_axes(4, match, ["1", "2", "3", "4", "5"])
    assert got == ["1", "2", "3", "4"]


@pytest.mark.unit
def test_merged_span_skips_the_missed_anchor_axis():
    """**核心用例**:局部漏检一条轴线 ⇒ 该处跨过锚图的一条。

    局部 3 条轴线,首段吃掉 2 个锚距 ⇒ 对应锚图第 0、2、3 条。
    """
    match = GapMatch(start_index=0, spans=[2, 1], scale_ratio=1.0)
    got = anchor_labels_for_local_axes(3, match, ["1", "2", "3", "4", "5"])
    assert got == ["1", "3", "4"]


@pytest.mark.unit
def test_middle_run_starts_at_the_matched_offset():
    """局部图只画中间一段 ⇒ 从 `start_index` 起算。"""
    match = GapMatch(start_index=2, spans=[1, 1], scale_ratio=1.0)
    got = anchor_labels_for_local_axes(3, match, ["1", "2", "3", "4", "5", "6"])
    assert got == ["3", "4", "5"]


@pytest.mark.unit
def test_zone_prefixed_labels_are_carried_through():
    """§8.0.5 的分区前缀要原样带过来 —— 那正是局部图缺的身份。"""
    match = GapMatch(start_index=0, spans=[1, 1], scale_ratio=1.0)
    got = anchor_labels_for_local_axes(3, match, ["2-1", "2-2", "2-3", "2-4"])
    assert got == ["2-1", "2-2", "2-3"]


@pytest.mark.unit
def test_running_past_the_anchor_end_yields_none():
    """越界不编号 —— 宁可缺一条,不可给错身份。"""
    match = GapMatch(start_index=2, spans=[1, 1], scale_ratio=1.0)
    got = anchor_labels_for_local_axes(3, match, ["1", "2", "3"])
    assert got == ["3", None, None]


@pytest.mark.unit
def test_axis_count_must_match_the_span_count():
    """轴线数必须比段数多 1 —— 对不上说明调用方传错了,不该猜。"""
    match = GapMatch(start_index=0, spans=[1, 1], scale_ratio=1.0)
    assert anchor_labels_for_local_axes(5, match, ["1", "2", "3", "4"]) == []
    assert anchor_labels_for_local_axes(2, match, ["1", "2", "3", "4"]) == []


@pytest.mark.unit
def test_empty_inputs_are_safe():
    match = GapMatch(start_index=0, spans=[], scale_ratio=1.0)
    assert anchor_labels_for_local_axes(1, match, []) == [None]
    assert anchor_labels_for_local_axes(0, match, ["1"]) == []
    assert anchor_labels_for_local_axes(3, None, ["1", "2"]) == []


@pytest.mark.unit
def test_blank_anchor_label_becomes_none():
    """锚图那条本身没轴号 ⇒ 传不出身份,记 None 而不是空串。"""
    match = GapMatch(start_index=0, spans=[1], scale_ratio=1.0)
    assert anchor_labels_for_local_axes(2, match, ["", "2"]) == [None, "2"]
