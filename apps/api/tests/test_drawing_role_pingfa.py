"""平法施工图是**构件来源图** —— 实测被判为 unknown。

**实测**（第二工程抽样 120 张）：

    结构-竣工图-S-31-18A-六层次梁**平面整体配筋图**.pdf → unknown

这是结构主力图纸。术语表里 `component_source` 只认
`平面图|平面布置|平面$`，而「平面**整体**配筋图」中间夹了「整体」，
三条都不匹配。

「平面整体表示方法」即**平法**，是 22G101 系列国标图集的制图规则
（《混凝土结构施工图平面整体表示方法制图规则和构造详图》）——
柱/梁/墙的配筋图都用这套写法，覆盖整个结构专业。
"""
from __future__ import annotations

import pytest

from services.drawing_role import ROLE_COMPONENT_SOURCE, ROLE_DETAIL, classify_role


@pytest.mark.unit
def test_pingfa_drawings_are_component_sources():
    """**核心用例**:平法配筋图 → 构件来源。"""
    for title in ("六层次梁平面整体配筋图", "首层框架梁平面整体配筋图",
                  "三层柱平法施工图", "剪力墙平法配筋图",
                  "地下一层主梁配筋图"):
        assert classify_role({"title": title}).role == ROLE_COMPONENT_SOURCE, title


@pytest.mark.unit
def test_detail_still_wins_over_pingfa():
    """**详图优先** —— 「梁配筋详图」表达局部构造,不是平面定位。"""
    for title in ("梁配筋详图", "柱配筋大样图", "墙身配筋节点图"):
        assert classify_role({"title": title}).role == ROLE_DETAIL, title


@pytest.mark.unit
def test_plain_plans_unaffected():
    """既有判定不受影响。"""
    assert classify_role({"title": "首层平面图"}).role == ROLE_COMPONENT_SOURCE
    assert classify_role({"title": "2F平面布置图"}).role == ROLE_COMPONENT_SOURCE
