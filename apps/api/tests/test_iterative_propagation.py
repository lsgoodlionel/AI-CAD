"""交点传播要**迭代** —— 传播成功的图本身就是新锚（J1 覆盖率）。

**实测瓶颈**（上海大歌剧院）：

| 环节 | 数量 |
|---|---:|
| 已识别轴网 | 2309 |
| **双向轴网**（几何上够构成交点） | **856** |
| 可作锚的图（有 ≥2 个坐标标注锚点且变换可解） | **1** |
| 实际有交点 | 32 |

几何条件早就满足，卡的是**世界坐标来源只有一张图**。
用单张锚的轴距序列去匹配 856 张，「对不上任何锚」占 91% 是必然的。

而那 32 张传播成功的图**已经带世界坐标**了 —— 它们可以拟合出自己的
变换、成为第二代锚，再去匹配剩下的图。现在跑一轮就停，等于放着不用。

**误差会累积**：每一跳都在上一跳的结果上再拟合。所以
① 限制代数；② 记录代数供下游降级；③ 残差大的不作新锚。
"""
from __future__ import annotations

import pytest

from services.axis_intersection_propagate import (
    MAX_PROPAGATION_GENERATIONS, next_generation_anchors,
)


@pytest.mark.unit
def test_newly_covered_drawings_become_anchors():
    """**核心用例**：本代新覆盖的图进入下一代锚。"""
    got = next_generation_anchors(
        covered={"a", "b", "c"}, previous_anchors={"a"},
        residuals={"b": 0.01, "c": 0.02})
    assert set(got) == {"b", "c"}


@pytest.mark.unit
def test_previous_anchors_are_not_reused():
    """已用过的锚不再作锚 —— 否则每代都在重复同一批匹配。"""
    got = next_generation_anchors(
        covered={"a", "b"}, previous_anchors={"a", "b"}, residuals={})
    assert got == []


@pytest.mark.unit
def test_high_residual_drawings_do_not_become_anchors():
    """**残差大的不作新锚** —— 拿它当基准会把误差放大给下一代。"""
    from services.axis_intersection_propagate import MAX_ANCHOR_RESIDUAL_M

    got = next_generation_anchors(
        covered={"good", "bad"}, previous_anchors=set(),
        residuals={"good": 0.01, "bad": MAX_ANCHOR_RESIDUAL_M * 2})
    assert got == ["good"]


@pytest.mark.unit
def test_drawings_without_a_residual_are_not_anchors():
    """算不出残差 = 变换没解出来，**判不出就说判不出**。"""
    got = next_generation_anchors(
        covered={"x"}, previous_anchors=set(), residuals={})
    assert got == []


@pytest.mark.unit
def test_order_is_deterministic():
    """顺序依赖会让重建结果漂移（本项目已犯过）。"""
    residuals = {"b": 0.01, "a": 0.01, "c": 0.01}
    first = next_generation_anchors({"a", "b", "c"}, set(), residuals)
    second = next_generation_anchors({"c", "b", "a"}, set(), residuals)
    assert first == second


@pytest.mark.unit
def test_best_residual_first():
    """残差小的先传播 —— 它的世界坐标更可信。"""
    got = next_generation_anchors(
        {"a", "b"}, set(), {"a": 0.5, "b": 0.001})
    assert got[0] == "b"


@pytest.mark.unit
def test_generations_are_bounded():
    """**误差逐代累积**，必须有上限；同时至少要跑得动两代才有意义。"""
    assert 2 <= MAX_PROPAGATION_GENERATIONS <= 5


@pytest.mark.unit
def test_residuals_helper_takes_the_prior_explicitly():
    """**未定义的名字会被宽泛 except 吞掉** —— 那是最难发现的一类。

    我给 `_residuals_of` 里的求解加了 `prior=prior`，而 `prior`
    根本不在它的作用域里 ⇒ `NameError` → 被 `except Exception` 吞掉
    → 所有图都「算不出残差」→ **迭代传播静默退化成单代**，
    而日志里一句话都没有。

    所以签名要显式带 prior，别指望闭包。
    """
    import inspect

    from services.axis_intersection_propagate import _residuals_of

    assert "prior" in inspect.signature(_residuals_of).parameters
