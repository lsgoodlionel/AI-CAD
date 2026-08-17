"""识别器算不出原点时，要用轴网路径已落库的原点（J7 收尾）。

**实测**：修好 `S-0-20-102.04C` 的 `drawing_transform`（origin_x 0→595.29）
并重建后，F1 层的墙**跨度仍是 2207 米、范围 `[149, 2356]` 一字未改**。

因为**构件坐标根本不走那张表**：

| 路径 | 谁算原点 | 存哪 |
|---|---|---|
| 档案/轴号 | `transform_from_axes` | `drawing_transform` 表 |
| **构件识别** | `_Ctx` 自己调 `_origin_pt` | 不落库，直接算进构件坐标 |

我修的是前者，而 2207 米出在后者 —— `_Ctx` 对缺失方向仍 `or 0.0`，
构件坐标于是从**图幅边缘**算起，整体偏移「真原点 × 比例」。

修法与 `_transform_of` 借比例同源，方向相反：识别器自己判不出原点时，
用轴网路径的结果补上。**两条路径各握一半，就该合起来用。**
"""
from __future__ import annotations

import pytest

from core.model3d.element_recognizer import _Ctx


@pytest.mark.unit
def test_ctx_uses_the_override_when_it_cannot_find_the_origin():
    """**核心用例**：自己算不出 x 原点时，用传入的原点。"""
    ctx = _Ctx(1000.0, 0.05, (None, 200.0), "d1", origin_override=(595.29, None))
    assert ctx.origin[0] == pytest.approx(595.29)


@pytest.mark.unit
def test_own_origin_wins_over_the_override():
    """自己检出的轴线更贴近本图实际，不被覆盖。"""
    ctx = _Ctx(1000.0, 0.05, (100.0, 200.0), "d1", origin_override=(595.29, 0.0))
    assert ctx.origin[0] == pytest.approx(100.0)


@pytest.mark.unit
def test_override_is_applied_per_direction():
    """缺哪个方向补哪个 —— 实测缺的都是**一个**方向。"""
    ctx = _Ctx(1000.0, 0.05, (None, 200.0), "d1", origin_override=(595.29, 999.0))
    assert ctx.origin[0] == pytest.approx(595.29)
    assert ctx.origin[1] == pytest.approx(200.0), "有值的方向不该被覆盖"


@pytest.mark.unit
def test_missing_flag_reflects_that_the_override_filled_it():
    """补上了就不再算「缺失」—— 否则下游会误以为这方向仍不可信。"""
    ctx = _Ctx(1000.0, 0.05, (None, 200.0), "d1", origin_override=(595.29, None))
    assert ctx.origin_missing[0] is False


@pytest.mark.unit
def test_still_falls_back_to_zero_without_an_override():
    """没有 override 时行为不变（按 0 兜底并标记缺失）。"""
    ctx = _Ctx(1000.0, 0.05, (None, 200.0), "d1")
    assert ctx.origin[0] == 0.0
    assert ctx.origin_missing[0] is True


@pytest.mark.unit
def test_coordinates_shift_by_the_recovered_origin():
    """**这才是 2207 米的解法**：补上原点后，构件坐标整体回到正确位置。

    实测 F1 的墙落在 x[149, 2356]，而该图真原点是 595.29pt、比例 1:150
    (0.0529 m/pt) ⇒ 少减的 595.29pt 相当于 **31.5 米**的整体偏移。
    """
    without = _Ctx(1000.0, 0.0529, (None, 0.0), "d1")
    with_override = _Ctx(1000.0, 0.0529, (None, 0.0), "d1",
                         origin_override=(595.29, None))
    x_without, _ = without.to_m(1000.0, 500.0)
    x_with, _ = with_override.to_m(1000.0, 500.0)
    assert x_without - x_with == pytest.approx(595.29 * 0.0529, abs=0.01)


@pytest.mark.unit
def test_override_reaches_the_context_through_recognize():
    """**参数接了不传等于没接** —— `recognize` 曾把 override 收下就丢掉。

    这一类「上游算对了下游不读」在本项目已反复出现；这次是我自己
    在同一轮里犯的，所以必须有测试守着整条传递链，而不只测 `_Ctx`。
    """
    from core.model3d.element_recognizer import _recognize
    import inspect

    assert "origin_override" in inspect.signature(_recognize).parameters
    src = inspect.getsource(
        __import__("core.model3d.element_recognizer", fromlist=["x"]).recognize)
    assert "origin_override" in src.split("return _recognize")[1][:60], \
        "recognize 必须把 override 传给 _recognize"


# ── 不能拿「兜底的 0」去补另一条路径 ──────────────────────────────

@pytest.mark.unit
def test_estimated_origin_is_not_used_as_an_override():
    """**标记为兜底的方向是假值，不能拿来补** —— 那等于把「没找到」
    当成「原点在 0」再传一手，错误就从一条路径漏到另一条。
    """
    from services.drawing_transform import DrawingTransform
    from services.model_elements import _origin_override_of

    t = DrawingTransform(scale_m_pt=0.05, origin_x=0.0, origin_y=200.0,
                         page_h=1000.0, origin_x_estimated=True)
    got = _origin_override_of({"d1": t}, "d1")
    assert got[0] is None, "兜底值不得外传"
    assert got[1] == pytest.approx(200.0)


@pytest.mark.unit
def test_real_origin_is_passed_through():
    from services.drawing_transform import DrawingTransform
    from services.model_elements import _origin_override_of

    t = DrawingTransform(scale_m_pt=0.05, origin_x=595.29, origin_y=706.47,
                         page_h=1000.0)
    assert _origin_override_of({"d1": t}, "d1") == (595.29, 706.47)


@pytest.mark.unit
def test_missing_transform_yields_no_override():
    from services.model_elements import _origin_override_of

    assert _origin_override_of({}, "d1") is None
    assert _origin_override_of(None, "d1") is None
