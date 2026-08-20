"""识别器的比例也要过 §6.0.4 门禁，并在算错时用已落库的比例（J7 收尾）。

**实测**（`S-0-20-102.04C`，图幅 3370×2384 pt）：

| 项 | 识别器自己算 | `drawing_transform` | 差 |
|---|---|---|---|
| **比例** | **1:4222** | 1:150 | **28 倍** |
| 原点 x | `None` | 595.3 | — |

3370pt × 1.489 m/pt = **5019 米** —— F1 层墙跨度 2207 米就是这么来的。
1:4222 还远超 §6.0.4 表的上限 1:2000。

**同一个缺陷、三条路径，此前只修了两条**：

| 路径 | 有没有比例门禁 | 决定什么 |
|---|---|---|
| `transform_from_geometry` | ✅ 已加 | 变换表 |
| `_transform_of`（轴网） | ✅ 已加 | 变换表 |
| **`_recognize`（识别器）** | ❌ **没有** | **构件坐标** |

漏掉的恰恰是唯一决定构件坐标的那条。这也解释了为什么修好变换表之后
重建，F1 跨度纹丝不动 —— 那张表根本不在这条路上。
"""
from __future__ import annotations

import pytest

from core.model3d.element_recognizer import resolve_scale


@pytest.mark.unit
def test_implausible_scale_falls_back_to_the_stored_one():
    """**核心用例**：自己算出 1:4222（超 §6.0.4 上限）时，用落库的 1:150。"""
    got = resolve_scale(1.489326, scale_override=0.052917, page_w_pt=3370.0)
    assert got == pytest.approx(0.052917)


@pytest.mark.unit
def test_plausible_scale_is_kept():
    """自己算得合理就用自己的 —— 识别器读的是本图文字，更贴近实际。"""
    got = resolve_scale(0.0529, scale_override=0.14)
    assert got == pytest.approx(0.0529)


@pytest.mark.unit
def test_implausible_scale_without_override_is_kept_as_is():
    """没有可借的比例时保持原状 —— 本函数只做「有更好的就换」。

    强行归零会让整张图的构件坍缩到一点，比放着更糟。
    """
    got = resolve_scale(1.489326, scale_override=None, page_w_pt=3370.0)
    assert got == pytest.approx(1.489326)


@pytest.mark.unit
def test_implausible_override_is_not_used():
    """借来的比例同样要过门禁 —— 历史行可能写于门禁之前（1:335 万那批）。"""
    got = resolve_scale(1.489326, scale_override=1184.0, page_w_pt=3370.0)
    assert got == pytest.approx(1.489326)


@pytest.mark.unit
def test_zero_scale_takes_the_override():
    """检不出比例（<=0）时当然该用落库的。"""
    assert resolve_scale(0.0, scale_override=0.0529) == pytest.approx(0.0529)


@pytest.mark.unit
def test_scale_override_reaches_the_context_through_recognize():
    """**整条传递链都要守** —— 「参数接了不传等于没接」本轮已犯过一次。"""
    import inspect

    from core.model3d.element_recognizer import _recognize, recognize

    assert "scale_override" in inspect.signature(recognize).parameters
    assert "scale_override" in inspect.signature(_recognize).parameters
    assert "scale_override" in inspect.getsource(recognize).split(
        "return _recognize")[1][:140], "recognize 必须把它传给 _recognize"


@pytest.mark.unit
def test_stored_scale_is_read_from_the_transform():
    """从 `drawing_transform` 取比例供识别器兜底 —— **但要过门禁**。

    契约变更（本次）：此前无条件返回落库比例，而实测 633 张来自图幅推断
    的变换比例跨越三个数量级、平均置信 0.02，垃圾变换正是这样被当作
    权威交给识别器的。现在只有可信的才覆盖，不可信时返回 None
    让识别器按图纸自身内容估。

    置信为空**不再视同可信**：那正是「没人评估过」的意思。
    实测库中 0/2142 行为空，两条构造路径都会设置它，故不影响真实链路。
    """
    from services.drawing_transform import DrawingTransform
    from services.model_elements import _scale_override_of

    trusted = DrawingTransform(scale_m_pt=0.052917, origin_x=595.29,
                               origin_y=706.47, page_h=2384.0, confidence=0.97)
    assert _scale_override_of({"d1": trusted}, "d1") == pytest.approx(0.052917)

    unrated = DrawingTransform(scale_m_pt=0.052917, origin_x=595.29,
                               origin_y=706.47, page_h=2384.0)
    assert _scale_override_of({"d1": unrated}, "d1") is None

    assert _scale_override_of({}, "d1") is None
    assert _scale_override_of(None, "d1") is None
