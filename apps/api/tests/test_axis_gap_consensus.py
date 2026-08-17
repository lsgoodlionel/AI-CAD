"""比例的第三道闸：**同一工程的轴距应当同一量级**。

**实测**（上海大歌剧院 736 张有轴距的图，共识中位数 8.01 米）：

| 偏离 | 图数 | 占比 |
|---|---:|---:|
| 正常（<20%） | 334 | 45.4% |
| 偏小 20~50% | 80 | 10.9% |
| **偏小 >50%** | **216** | **29.3%** |
| 偏大 20~100% | 69 | 9.4% |
| 偏大 >100% | 37 | 5.0% |

**只有 45% 的图比例是对的**。用户看到的「模型分成好几块、轴线对不上」
就是这么来的：每张图按各自错误的比例换算，构件尺度五花八门。
实测同一栋楼的「轴 3 → 轴 12」，三层算出 126.9 / 91.5 / **22.7** 米，
差 5.6 倍 —— 真实建筑里这个距离是固定的。

**前两道闸都拦不住**：

| 闸 | 判据 | 为什么漏 |
|---|---|---|
| `is_scale_plausible` | 分母 1~5000 | 这些图的比例都在区间内 |
| `MAX_DRAWING_EXTENT_M` | 图幅 < 3 公里 | 比例偏小的图换算尺寸更小，更不会超 |

两者都只防「离谱」，防不住「看似合理却彼此不一致」。
共识 8.01 米是 736 张图自己算出来的，不是拍的。
"""
from __future__ import annotations

import pytest

from services.axis_gap_consensus import consensus_gap, correct_scale_by_consensus

# 1:150 → 0.0529 m/pt（§6.0.4 标准比例）
SCALE_150 = 150 * 25.4 / 72 / 1000


@pytest.mark.unit
def test_consensus_is_the_median():
    """共识取中位数 —— 少数错得离谱的图不该带偏基准。"""
    assert consensus_gap([8.0, 8.2, 8.1, 2.5, 40.0]) == pytest.approx(8.1)


@pytest.mark.unit
def test_consensus_needs_enough_samples():
    """样本太少不算共识（**判不出就说判不出**）。"""
    assert consensus_gap([8.0]) is None
    assert consensus_gap([]) is None


@pytest.mark.unit
def test_scale_within_tolerance_is_untouched():
    """接近共识就不动 —— 真实建筑本就有不同柱网。"""
    got = correct_scale_by_consensus(SCALE_150, gap_m=8.4, consensus_m=8.0)
    assert got == pytest.approx(SCALE_150)


@pytest.mark.unit
def test_scale_far_below_consensus_is_corrected():
    """**核心用例**：轴距只有共识的 1/3 ⇒ 比例被算小了，按倍数还原。"""
    got = correct_scale_by_consensus(SCALE_150 / 3, gap_m=2.7, consensus_m=8.0)
    assert got > SCALE_150 / 3
    assert got == pytest.approx(SCALE_150, rel=0.1)


@pytest.mark.unit
def test_correction_must_land_on_a_standard_scale():
    """**修正后必须落在 §6.0.4 标准比例上，否则不可信、宁可不改**。

    这是这道闸的自验证：真实图纸的比例只能是规范表里的值，
    推出来的若不是，说明推断本身有问题（轴距共识不适用于这张图）。
    """
    # 轴距 1.7 米 ⇒ 推出 1:150 × (8.0/1.7) ≈ **1:705**，
    # 落在 §6.0.4 的 600 与 1000 之间、离两者都超 10%，吸附不上
    # ⇒ 推断不可信，保持原值。
    odd = correct_scale_by_consensus(SCALE_150, gap_m=1.7, consensus_m=8.0)
    assert odd == pytest.approx(SCALE_150)


@pytest.mark.unit
def test_missing_inputs_change_nothing():
    """缺共识或缺本图轴距时不猜。"""
    assert correct_scale_by_consensus(SCALE_150, None, 8.0) == pytest.approx(SCALE_150)
    assert correct_scale_by_consensus(SCALE_150, 8.0, None) == pytest.approx(SCALE_150)


@pytest.mark.unit
def test_zero_gap_is_safe():
    """轴距为 0 会除零 —— 必须挡住。"""
    assert correct_scale_by_consensus(SCALE_150, 0.0, 8.0) == pytest.approx(SCALE_150)


# ── 接进识别链路（整条都要守）────────────────────────────────────

@pytest.mark.unit
def test_resolve_scale_applies_the_consensus():
    """`resolve_scale` 是**唯一决定构件坐标的比例出口**，闸必须落在这里。"""
    from core.model3d.element_recognizer import resolve_scale

    got = resolve_scale(SCALE_150 / 3, page_w_pt=3370.0,
                        gap_hint=(2.7, 8.0))
    assert got == pytest.approx(SCALE_150, rel=0.1)


@pytest.mark.unit
def test_resolve_scale_without_hint_is_unchanged():
    """没有共识就不动 —— 老路径零回归。"""
    from core.model3d.element_recognizer import resolve_scale

    assert resolve_scale(SCALE_150, page_w_pt=3370.0) == pytest.approx(SCALE_150)


@pytest.mark.unit
def test_gap_hint_reaches_the_recogniser():
    """**参数接了不传等于没接** —— 本轮已犯过一次，整条链都要断言。"""
    import inspect

    from core.model3d.element_recognizer import _recognize, recognize

    assert "gap_hint" in inspect.signature(recognize).parameters
    assert "gap_hint" in inspect.signature(_recognize).parameters
    assert "gap_hint" in inspect.getsource(recognize).split(
        "return _recognize")[1][:200], "recognize 必须把它传给 _recognize"


@pytest.mark.unit
def test_gap_hint_reaches_the_element_service():
    import inspect

    from services.model_elements import _recognize_sync, build_floor_elements

    assert "gap_hints" in inspect.signature(build_floor_elements).parameters
    assert "gap_hint" in inspect.signature(_recognize_sync).parameters
