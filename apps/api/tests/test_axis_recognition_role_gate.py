"""非几何图不该产出轴网 —— 判据早就有,只是识别层没用(J6 第二轮)。

**实测**(J6 首轮清完圆形构件误检后仍剩):

| 图 | 轴线 | 圈 | 分区 |
|---|---:|---:|---:|
| **P-22-01B 消火栓系统原理图** | **385** | 390 | **21** |
| P-22-02B 消火栓系统原理图(二) | 239 | 317 | 7 |

**系统原理图是示意图,根本没有定位轴线** —— 轴线用于**平面定位**,
而原理图不表达平面位置。那 390 个「圈」是管道节点符号。
21 个分区更是荒谬(大歌剧院真值 3 个)。

**判据早就存在**:`services/drawing_role.py` 的 `ROLE_NON_GEOMETRIC`
已覆盖「系统图|流程图|原理图|配电箱|接线图」,是**国标术语**判据、
不绑任何院的编号体系(见 MODELING_PIPELINE_BLUEPRINT §7 约束 1、2)。
识别层只是**没有去读它**。

**不跳过识别,而是识别后置零并写明原因** ——
「降级必须可见」(§7 约束 3):跳过会让这张图连「判过了」的记录都没有,
界面上与「还没跑」分不开。
"""
from __future__ import annotations

import pytest

from services.axis_recognition import NON_GEOMETRIC_WARNING, should_skip_axes


@pytest.mark.unit
def test_schematic_is_gated():
    """**核心用例**:消火栓系统原理图不该产出 385 条轴线。"""
    assert should_skip_axes({"title": "给排水-竣工图--消火栓系统原理图(一)"})


@pytest.mark.unit
@pytest.mark.parametrize("title", [
    "电气-竣工图--配电箱系统图",
    "暖通-竣工图--空调水系统流程图",
    "电气-竣工图--弱电接线图",
])
def test_other_non_geometric_terms_are_gated(title):
    """国标术语表里的其余非几何图种同样拦下。"""
    assert should_skip_axes({"title": title})


@pytest.mark.unit
@pytest.mark.parametrize("title", [
    "建筑-竣工图--地下二层完整平面图",
    "建筑-竣工图--正交轴网定位图",
    "结构-竣工图--一层墙柱平面图",
])
def test_plans_are_not_gated(title):
    """**平面图必须放行** —— 它们正是轴网的来源。"""
    assert not should_skip_axes({"title": title})


@pytest.mark.unit
@pytest.mark.parametrize("title", [
    "建筑-竣工图--东立面图",
    "建筑-竣工图--7-7剖面图",
])
def test_elevations_and_sections_are_not_gated(title):
    """立面/剖面**有**轴线(单向投影),不能拦 —— Phase I 靠它们做 z 恢复。"""
    assert not should_skip_axes({"title": title})


@pytest.mark.unit
def test_details_are_not_gated():
    """详图可能带轴号(§9.4.4「详图适用于多根轴线时应注明各轴线编号」),
    不拦 —— 只拦**非几何**图。"""
    assert not should_skip_axes({"title": "建筑-竣工图--楼梯大样图"})


@pytest.mark.unit
def test_drawing_no_is_also_checked():
    """标题缺失时看图号 —— `_by_term` 逐源匹配。"""
    assert should_skip_axes({"title": "", "drawing_no": "消火栓系统原理图-01"})


@pytest.mark.unit
def test_empty_drawing_is_not_gated():
    """判不出就放行 —— 宁可多识别,不可漏掉真轴网图。"""
    assert not should_skip_axes({})
    assert not should_skip_axes(None)


@pytest.mark.unit
def test_warning_says_why():
    """降级必须可见:要说清**为什么**置零,而不是静默返回空。"""
    assert "原理图" in NON_GEOMETRIC_WARNING or "系统图" in NON_GEOMETRIC_WARNING
    assert "轴线" in NON_GEOMETRIC_WARNING
