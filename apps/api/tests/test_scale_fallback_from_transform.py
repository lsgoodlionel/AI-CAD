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


# --- 门禁挡不住的一档：值本身「合理」，但它是猜出来的 -------------------
#
# **实测三例**（大歌剧院竣工图，图幅均 3370pt，图上**都没有比例文字**
# 因而都走轴距兜底）。抽样 60 张平面图里**只有 1 张**能读到明文比例
# ——图框有「审定人/日期」这些字段名，却没有比例栏——所以 98% 的图
# 只能在「猜的」和「库里的」之间二选一，而两者都可能错：
#
# | 图 | 轴距猜测 → 图宽 | 落库 → 图宽 | 真值 |
# |---|---|---|---|
# | `1fc56cbf` 基础分区平面图 | 1:412 → 490m | **1:50 → 59m** | 落库对 |
# | `864853a6` 隔声隔振平面图 | 1:2835 → 3370m | **1:50 → 59m** | 落库对 |
# | `8657c221` 地下一层平面图 | **1:100 → 119m** | 1:1000 → 1189m | **猜测对** |
#
# 所以「猜的一律让位给库里的」是错的规则——第三例会被它害了。
# 裁决判据用**图幅换算出的实际宽度是否说得通**，这是本文件已有的判断
# （`MAX_DRAWING_EXTENT_M`），只是从「阈值」改成「两个候选相比」。

@pytest.mark.unit
def test_guess_yields_to_stored_when_its_own_extent_is_absurd():
    """轴距猜测换算出 490 米的基础平面图，让位给落库的 1:50（59 米）。"""
    assert resolve_scale(0.145379, 0.017639, 3370.0,
                         detected_is_guess=True) == pytest.approx(0.017639)


@pytest.mark.unit
def test_stored_scale_rejected_when_it_is_the_absurd_one():
    """反过来也要成立：落库值换算 1189 米时，保留猜出来的 1:100。

    这一条是三例里唯一「猜测对、落库错」的，没有它这条规则会害了它。
    """
    guess_1_100 = 0.0352778
    assert resolve_scale(guess_1_100, 0.352778, 3370.0,
                         detected_is_guess=True) == pytest.approx(guess_1_100)


@pytest.mark.unit
def test_scale_read_from_the_drawing_wins_over_stored():
    """图上明写比例时不让位——明文是最强证据（尽管只占 2% 的图）。"""
    assert resolve_scale(0.0352778, 0.017639, 3370.0,
                         detected_is_guess=False) == pytest.approx(0.0352778)


@pytest.mark.unit
def test_guess_kept_when_nothing_trustworthy_to_fall_back_on():
    """没有可借的落库比例时保持原状——归零会让整张图坍缩到一点。"""
    assert resolve_scale(0.145379, None, 3370.0,
                         detected_is_guess=True) == pytest.approx(0.145379)


@pytest.mark.unit
def test_a_plausible_guess_is_not_displaced_by_a_stored_value():
    """猜测值换算说得通时不让位——上一版「都合理就优先落库」已被证伪。

    **实测**（大歌剧院「南区一层结构平面图（四）」，图幅 3370pt）：

    | | 比例 | 图宽换算 | 是标准比例 |
    |---|---|---|---|
    | 识别（轴距猜） | 1:100 | 119 米 | 是 |
    | 落库 | **1:15** | **18 米** | **也是** |

    两者都在标准比例表里、都过了旧区间，旧规则选了落库的 1:15，
    于是尺寸判据下的候选从 658 个塌到 **3** 个，整块柱归零
    （金标准 12 → 识别 0）。标准性判不出来，**图幅换算判得出来**。
    """
    guess_1_100 = 0.0352778
    stored_1_15 = 15 * 0.000352778
    assert resolve_scale(guess_1_100, stored_1_15, 3370.0,
                         detected_is_guess=True) == pytest.approx(guess_1_100)
