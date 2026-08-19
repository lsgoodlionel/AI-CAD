"""按**构件编号**追溯 —— 会审 133 条检查项里「定位信息是否完整」的直接落地。

那条检查项写着：

    判断依据：图号、层位、轴线、**节点号**、**系统编号**、房间名称、设备名称
    常见冲突：只有疑问没有坐标，导致**无法核图、无法追责、无法复核**
    必问问题：问题具体对应哪张图、哪个部位？

**实测**（大歌剧院档案层）：587 个构件编号里 **214 个（36%）跨多图出现**，
`M1124` 出现在 **84 张图**上 —— 一个构件的信息本就分散在多张图里，
人核图时要把它们并起来看。

平法图集不另出柱表梁表（配筋直接标在平面图上，这正是「平面整体表示」），
所以关联的形态不是「平面图 → 构件表」，而是**同一编号在多图间的共现**。
"""
from __future__ import annotations

import pytest

from services.component_mark_index import build_mark_index, mark_summary


def _row(mark: str, drawing_id: str, title: str = "", floor: str | None = None):
    return {"content": mark, "drawing_id": drawing_id,
            "title": title, "floor_key": floor}


@pytest.mark.unit
def test_index_groups_drawings_by_mark():
    """**核心用例**:同一编号的多张图聚到一起。"""
    index = build_mark_index([
        _row("KZ1", "d1", "三层柱平面图"),
        _row("KZ1", "d2", "四层柱平面图"),
        _row("KL2(3)", "d1", "三层柱平面图"),
    ])
    assert set(index["KZ1"]["drawings"]) == {"d1", "d2"}
    assert index["KZ1"]["kind"] == "column"
    assert index["KL2(3)"]["kind"] == "beam"


@pytest.mark.unit
def test_non_marks_are_ignored():
    """**材料牌号不进索引** —— C30/Q235 不是构件。"""
    index = build_mark_index([
        _row("C30", "d1"), _row("Q235", "d1"), _row("标高", "d1"),
        _row("KZ1", "d1"),
    ])
    assert set(index) == {"KZ1"}


@pytest.mark.unit
def test_summary_ranks_by_cross_drawing_span():
    """**跨图多的排前面** —— 那是最需要并起来看的。"""
    index = build_mark_index([
        _row("M1124", f"d{i}") for i in range(5)
    ] + [_row("KZ1", "d1"), _row("KZ1", "d2")])
    top = mark_summary(index)
    assert top[0]["mark"] == "M1124" and top[0]["drawing_count"] == 5
    assert top[1]["mark"] == "KZ1"


@pytest.mark.unit
def test_floor_keys_are_collected():
    """带上层位 —— 「哪一层的 KZ1」是核图必问的。"""
    index = build_mark_index([
        _row("KZ1", "d1", floor="F3"), _row("KZ1", "d2", floor="F4"),
        _row("KZ1", "d3", floor=None),
    ])
    assert set(index["KZ1"]["floors"]) == {"F3", "F4"}


@pytest.mark.unit
def test_empty_input():
    assert build_mark_index([]) == {}
    assert mark_summary({}) == []
