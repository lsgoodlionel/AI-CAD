"""单体归属兜底:区分「本就没有单体」与「该有却没识别出来」。

**为什么是当前瓶颈**:1866 / 2309 = **80.8%** 图纸 `semantic_unassigned`,
而标高、构件归属全挂在单体上——同一个 `F3` 在南区是 10.300、
北区是 10.800,**单体没定,标高就定不了**。

**但 80.8% 这个数字本身是误导的**。实测拆开看:

| 类 | 数 | 说明 |
|---|---:|---|
| 非几何(目录/说明/表) | 247 | **本就没有单体** |
| 详图(大样/楼梯/卫生间) | 351 | **本就没有单体** |
| 围护基坑(非主体结构) | 20 | **本就没有单体** |
| **有楼层却缺单体** | **778** | ← 真损失 |
| 其他 | 470 | — |

**618 张本就不该有单体归属**,把它们算进「未分配」等于虚报损失,
还会让人去优化一个不存在的问题。

**判据复用 `drawing_role`**——非几何与详图由**国标术语**判出,
不依赖任何工程的图号体系。
"""
from __future__ import annotations

import pytest

from services.building_unit_fallback import (
    DEFAULT_UNIT_KEY, UNIT_ASSIGNED, UNIT_DEFAULTED, UNIT_NOT_APPLICABLE,
    UNIT_UNRESOLVED, classify_unit_assignment, summarize_assignments,
)


def _d(title: str, no: str = "X-1") -> dict:
    return {"drawing_no": no, "title": title}


# ── 本就没有单体 ────────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.parametrize("title", [
    "图纸目录", "建筑设计说明", "室内装修用料及做法表", "电力配电箱系统图",
])
def test_non_geometric_drawings_need_no_building_unit(title):
    """目录/说明/表/系统图本来就不属于任何单体 —— 不是「未分配」。"""
    assert classify_unit_assignment(_d(title)).status == UNIT_NOT_APPLICABLE


@pytest.mark.unit
@pytest.mark.parametrize("title", [
    "楼梯ST-01放大详图", "墙身大样图", "一层卫生间，淋浴间放大详图(一)",
    "台塔设备屋面防水保温构造节点图(一)",
])
def test_detail_drawings_need_no_building_unit(title):
    """详图画的是通用做法,跨单体复用 —— 强行归到某个单体反而是错的。"""
    assert classify_unit_assignment(_d(title)).status == UNIT_NOT_APPLICABLE


@pytest.mark.unit
@pytest.mark.parametrize("title", [
    "围护桩配筋详图", "第一阶段工况平面图", "基坑支护平面图",
    "地铁连通道结构图", "环境总平图",
    # 下面五条取自实测「仍无法判定」名单 —— 同属施工阶段临时结构
    "29 1区首道支撑结合栈桥平面布置图", "30 1区第二道支撑平面布置图",
    "钻孔灌注桩说明（一）", "58 基础底板换撑平面布置图",
    "24 1区立柱桩及钢立柱平面布置图",
])
def test_excavation_drawings_are_not_main_structure(title):
    """围护/基坑/工况/连通道是施工阶段图,不属于主体单体。"""
    assert classify_unit_assignment(_d(title)).status == UNIT_NOT_APPLICABLE


# ── 有单体线索 ──────────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.parametrize("title,unit", [
    ("南区（大、中歌剧厅）一层结构平面总图", "south"),
    ("北区（小歌剧厅）地下一层结构平面图", "north"),
    ("东区二层平面图", "east"),
])
def test_directional_units_are_recognised(title, unit):
    got = classify_unit_assignment(_d(title))
    assert got.status == UNIT_ASSIGNED
    assert got.unit_key == unit


# ── 有楼层缺单体 → 降级挂默认单体 ────────────────────────────────

@pytest.mark.unit
@pytest.mark.parametrize("title", [
    "一层完整平面图", "地下二层给排水及消火栓平面图（一）",
    "四层喷淋抗震支架平面图（二）", "屋顶平面图",
])
def test_floor_bearing_drawings_fall_back_to_the_default_unit(title):
    """**这是真损失那 778 张的救法**。

    它们有明确楼层,只是没写单体。丢掉等于整层构件不进模型;
    挂到默认单体则至少几何可用,且标记为降级、可事后纠正。
    """
    got = classify_unit_assignment(_d(title))
    assert got.status == UNIT_DEFAULTED
    assert got.unit_key == DEFAULT_UNIT_KEY


@pytest.mark.unit
def test_defaulted_assignment_carries_a_reason():
    """降级必须说明理由 —— 否则事后分不清是识别出来的还是兜底的。"""
    got = classify_unit_assignment(_d("三层平面图"))
    assert "单体" in got.reason


@pytest.mark.unit
def test_drawing_with_neither_unit_nor_floor_stays_unresolved():
    """既无单体线索又无楼层 —— 如实标 unresolved,**不硬挂默认单体**。"""
    got = classify_unit_assignment(_d("某种说不清的图"))
    assert got.status == UNIT_UNRESOLVED
    assert got.unit_key is None


@pytest.mark.unit
def test_explicit_unit_beats_the_floor_fallback():
    """有单体就用单体,不该降级。"""
    got = classify_unit_assignment(_d("南区（大歌剧厅）三层平面图"))
    assert got.status == UNIT_ASSIGNED and got.unit_key == "south"


# ── 汇总 ────────────────────────────────────────────────────────

@pytest.mark.unit
def test_summary_separates_real_loss_from_non_applicable():
    """**核心用例**:汇总必须把「本就没有」与「该有却没有」分开。

    混在一起报 80.8% 未分配,会让人去优化一个不存在的问题。
    """
    got = summarize_assignments([
        _d("图纸目录"), _d("楼梯放大详图"), _d("围护桩配筋详图"),
        _d("一层平面图"), _d("南区二层平面图"), _d("某种说不清的图"),
    ])
    assert got["not_applicable"] == 3
    assert got["defaulted"] == 1
    assert got["assigned"] == 1
    assert got["unresolved"] == 1
    # 真损失只算 defaulted + unresolved，不含 not_applicable
    assert got["needs_attention"] == 2


@pytest.mark.unit
def test_empty_input_is_safe():
    got = summarize_assignments([])
    assert got["needs_attention"] == 0
