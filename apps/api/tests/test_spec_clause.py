"""设计说明条文重组 —— **人看图的真正起点**。

**逻辑起点**（用户指出）：人拿到一套图，先读设计总说明 ——
分区范围、标高体系、材料等级、构造要求都在那里写死，
而不是从图形反推。此前整条链路都在从几何猜，忽略了图纸自己已经
用文字说清楚的东西。

**实测规模**（两工程）：说明图 **169 张**、文字 **61227 条**，
其中**带条文编号 5166 条**，层级 1~4 级：

    1.2   混凝土保护层
    1.2.6 地下室外墙纵筋的混凝土保护层厚度：外侧迎水面…
    1.3   钢筋的锚固与连接
    1.3.1 …

**前置问题**：PDF 按行提取，长条文被切碎 —— `1.3.1拉钢筋的抗`
这样的残句无法直接使用。而**编号是文档自带的结构**：
一条编号开启一个条目，直到下一条编号出现，中间的行都属于它。
这比通用的「断行重组」更有依据。
"""
from __future__ import annotations

import pytest

from core.model3d.spec_clause import parse_clause_number, regroup_clauses


@pytest.mark.unit
def test_clause_numbers_recognised():
    """一到四级编号,兼容 `1.2` / `1.2.6` / `1、` / `（3）`。"""
    assert parse_clause_number("1.2 混凝土保护层") == ("1.2", "混凝土保护层")
    assert parse_clause_number("1.2.6地下室外墙纵筋") == ("1.2.6", "地下室外墙纵筋")
    assert parse_clause_number("3、钢筋的连接") == ("3", "钢筋的连接")
    assert parse_clause_number("1.1.1.2 细则") == ("1.1.1.2", "细则")


@pytest.mark.unit
def test_non_clause_lines():
    """**不得把普通文字当条文** —— 标高 `3.600`、比例 `1:100`、
    构件编号 `KZ1` 都不是条文号。"""
    for text in ("3.600", "1:100", "KZ1", "混凝土保护层厚度", "", None,
                 "C30", "2020-06-22"):
        assert parse_clause_number(text) is None, text


@pytest.mark.unit
def test_regroup_merges_continuation_lines():
    """**核心用例**:断行归并到它所属的条文。"""
    lines = [
        "1.2 混凝土保护层",
        "最外层钢筋的保护层厚度不应小于下表：",
        "混凝土保护层厚度尚应符合现行国家防火标准的要求。",
        "1.3 钢筋的锚固与连接",
        "2）当锚固钢筋的保护层厚度不大于5d时",
    ]
    got = regroup_clauses(lines)
    assert [c.number for c in got] == ["1.2", "1.3"]
    assert "防火标准" in got[0].text
    assert "5d" in got[1].text


@pytest.mark.unit
def test_text_before_first_clause_is_kept_as_preamble():
    """**首条编号之前的文字不丢** —— 那常是标题或引言。"""
    got = regroup_clauses(["结构设计总说明", "本工程为…", "1 总则", "依据…"])
    assert got[0].number is None and "结构设计总说明" in got[0].text
    assert got[1].number == "1"


@pytest.mark.unit
def test_hierarchy_is_exposed():
    """层级要能取到 —— `1.2.6` 属于 `1.2`,人靠它导航。"""
    got = regroup_clauses(["1 总则", "1.2 保护层", "1.2.6 外墙", "2 材料"])
    by_num = {c.number: c for c in got}
    assert by_num["1.2.6"].level == 3
    assert by_num["1.2.6"].parent == "1.2"
    assert by_num["1"].parent is None


@pytest.mark.unit
def test_sequence_break_is_not_a_clause():
    """**编号要单调递进才算条文** —— 否则表格里的 `1.5`(尺寸)
    会被当成条文号切断上一条。"""
    lines = ["1.2 保护层", "板厚 1.5 mm", "1.3 锚固"]
    got = regroup_clauses(lines)
    assert [c.number for c in got] == ["1.2", "1.3"]
    assert "1.5" in got[0].text


@pytest.mark.unit
def test_empty_input():
    assert regroup_clauses([]) == []
    assert regroup_clauses(None) == []
