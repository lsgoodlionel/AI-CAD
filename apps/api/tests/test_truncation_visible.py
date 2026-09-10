"""几何截断必须可见（降级必须可见）。

**怎么发现的**：第二工程重建后逐图对比 v8 与 v10，发现几张大图的构件数
剧烈变化（照明图管线 119 → 0，而单张识别跑出 518）。查下去不是识别的问题 ——
是 `MAX_PRIMITIVES` 截断，且截断位置依赖 PDF 遍历顺序，所以**同一张图两次
重建可以给出不同结果**。

**实测规模**：抽查 120 张参与建模的图，**120 张全部触到上限**（100%）。
换句话说，整个模型都建立在每张图的前 20000 个图元上。

**而这件事完全静默**，因为标记有个 off-by-one：

    收集侧   `if geom.primitive_count() >= MAX_PRIMITIVES: return`
             —— 停在恰好等于上限的位置
    标记侧   `truncated = geom.primitive_count() > MAX_PRIMITIVES`
             —— 要求**严格大于**，于是永远是 False

两边的比较符不一致，标记就永远打不出来。本文件把这个边界钉死。

（配额此后改为**按类**分配，见 `test_primitive_budget.py`；
本文件的断言随之改读 `budget_for("lines")`。）

**注意本修复不改变识别结果** —— 它只让「这张图被截断了」这个事实
出现在 `axes.truncated` 里。上限本身该不该提高是另一个问题
（提高会成倍增加渲染与识别耗时），需要单独实测。
"""
from __future__ import annotations

import pytest

from core.model3d import DrawingGeometry, recognize
from core.model3d.geometry_extractor import budget_for


def _geometry_with(n_lines: int) -> DrawingGeometry:
    """造一张恰好有 n_lines 条线的图。"""
    geom = DrawingGeometry(page_w=842.0, page_h=595.0)
    for i in range(n_lines):
        y = 30.0 + (i % 500) * 0.5
        geom.lines.append((10.0, y, 800.0, y))
        geom.line_layers.append("")
    return geom


@pytest.mark.unit
def test_exactly_at_the_cap_is_reported_as_truncated():
    """**恰好等于上限**就是被截断了 —— 这正是收集侧停下来的位置。

    收集侧用 `>=` 停止，所以真实世界里被截断的图，`primitive_count()`
    恰好等于 `MAX_PRIMITIVES`，一个也不会超过。标记若要求严格大于，
    就永远打不出来（实测 120/120 张被截断的图，标记全是 False）。
    """
    cap = budget_for("lines")
    geom = _geometry_with(cap)
    assert len(geom.lines) == cap, "前提：构造恰好触到线的配额"

    result = recognize(geom, "structure", "d-cap")
    assert result.axes.get("truncated") is True, (
        "恰好触到上限的图必须标 truncated —— 否则截断完全静默")


@pytest.mark.unit
def test_below_the_cap_is_not_flagged():
    """反向对照：没触到上限的图不该被标，否则这个标记就没有信息量。"""
    geom = _geometry_with(budget_for("lines") - 100)
    result = recognize(geom, "structure", "d-small")
    assert result.axes.get("truncated") is not True


@pytest.mark.unit
def test_collector_and_flag_use_the_same_comparison():
    """收集侧与标记侧的判据必须是同一个 —— 两边比较符不一致就是这个 bug 的根。

    用源码断言而不是行为断言：行为断言只能证明当前这一版对，
    源码断言能挡住「有人只改一边」。
    """
    import inspect

    from core.model3d import element_recognizer, geometry_extractor

    collector = inspect.getsource(geometry_extractor)
    flagger = inspect.getsource(element_recognizer)

    assert "budget_for(" in collector, (
        "收集侧必须按类配额停止（`_budget_exhausted`），不能用总数上限")
    assert "budget_for(" in flagger, (
        "标记侧必须与收集侧读同一张配额表，否则标记会再次永远打不出来")
