"""图元配额按类分配，而不是先到先得（分块识别调查的真正结论）。

**调查从哪来**：重建前后构件数剧烈波动 → 查到 `MAX_PRIMITIVES` 截断了
**100%** 的参与建模图（抽查 120/120），真实图元数中位 **377,686**，
实际只用到 5.5%。

**但「丢了 95%」不是准确的诊断**。实测一张典型图的构成：

    抽取 2 万（线上）    线 19,752（98.8%）· 多边形    248
    抽取 40 万           线 382,882        · 多边形 17,120

**不是均匀丢失，是线把配额吃光了** —— 而柱来自多边形。同一张图：
2 万配额下识别出柱 **3** 根，40 万配额下 **42** 根。

**分块是错的方向**（实测）：

    A 当前 2万·整图      9.0s   柱  3
    B 全量 40万·整图     9.7s   柱 42     ← 几乎免费
    C 全量 40万·4×4块  117.9s   柱 42     ← 慢 12 倍

分块每块都要遍历全量图元，是 O(块数×n)。而放开上限几乎不要钱 ——
因为识别阶段还有第二道**每类** 2 万的切片顶着。

**所以修法是按类分配配额**：线是压倒性多数（98.8%），但它们贡献的是
墙/梁的平行线对；多边形贡献柱与板。让线吃光配额，等于用墙的原料
换掉了柱的原料。
"""
from __future__ import annotations

import pytest

from core.model3d.geometry_extractor import (
    MAX_PRIMITIVES,
    PRIMITIVE_BUDGET,
    budget_for,
)


@pytest.mark.unit
def test_每类都有独立配额():
    """三类各有自己的配额，互不侵占 —— 这是本次修复的核心。"""
    for kind in ("lines", "rects", "polys"):
        assert budget_for(kind) > 0, f"{kind} 必须有独立配额"


@pytest.mark.unit
def test_多边形配额不被线挤占():
    """实测的病根：线占 98.8%，多边形只剩 248 个，而柱来自多边形。

    配额分配必须保证多边形拿得到足够的量 —— 实测 40 万抽取下
    多边形有 17,120 个，柱从 3 根变 42 根。
    """
    assert budget_for("polys") >= 15_000, (
        "多边形配额要够装下实测量级（一张典型图 17,120 个），"
        "否则柱的原料仍然被线挤掉")


@pytest.mark.unit
def test_线的配额仍然充足():
    """反向约束：不能为了救多边形把线砍狠了 —— 墙与梁的平行线对靠它。"""
    assert budget_for("lines") >= MAX_PRIMITIVES, (
        "线的配额不该低于原来的总配额，否则墙/梁会退化")


@pytest.mark.unit
def test_未知类别有兜底而不是抛异常():
    """缺失不得阻断（`MODELING_PIPELINE_BLUEPRINT.md` §7）。"""
    assert budget_for("unknown_kind") > 0


@pytest.mark.unit
def test_配额表是单一真相源():
    """抽取侧与识别侧读同一张表 —— 两处各写一遍必然漂移。

    这正是 `truncated` 标记那个 bug 的教训：收集侧用 `>=`、标记侧用 `>`，
    比较符不一致，标记就永远打不出来。
    """
    import inspect

    from core.model3d import element_recognizer

    src = inspect.getsource(element_recognizer)
    assert "budget_for" in src, (
        "识别侧必须用 `budget_for` 取配额，不能自己写死 MAX_PRIMITIVES")
    assert isinstance(PRIMITIVE_BUDGET, dict) and PRIMITIVE_BUDGET, (
        "配额表要是可读的单一来源")


@pytest.mark.unit
def test_一类天然为零时其他类不会失控():
    """**每类各自把关**，而不是「三类都满才停」。

    实测踩过的坑：很多 PDF 里 `rects` 恒为 0（矩形被当作四点多边形收），
    若停止条件写成「三类都满」，矩形永远不满，遍历就永不停止 ——
    实测线因此收到 **1,019,796** 条，而配额是 6 万。
    识别结果当时看着正常（识别侧另有切片），但抽取阶段内存无限增长。
    """
    from core.model3d.geometry_extractor import _add_line, _add_poly
    from core.model3d.types import DrawingGeometry

    geom = DrawingGeometry(page_w=842.0, page_h=595.0)
    cap = budget_for("lines")
    # 只喂线与多边形，一个矩形也不给 —— 复现「rects 恒为 0」
    for i in range(cap + 5_000):
        _add_line(geom, 0.0, float(i % 500), 10.0, float(i % 500))
    assert len(geom.rects) == 0, "前提：这一页没有矩形"
    assert len(geom.lines) == cap, (
        f"线必须停在自己的配额上，实得 {len(geom.lines):,} —— "
        "每类各自把关，不能等别人满")

    for i in range(200):
        _add_poly(geom, [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)])
    assert len(geom.polys) == 200, "多边形没到配额，不该被线的满额连累"
