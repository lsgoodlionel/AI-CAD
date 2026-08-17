"""分区号传播 —— 把人工确认的分区号经轴距序列匹配扩散到其他图(J1-3)。

**为什么需要它**:§8.0.5 的分区编号**几何推不出**(国标本身的限制),
只能人工确认。全项目 1052 张多分区图逐张确认不现实。

**J1 实测给出的方向**:未匹配原因中「对不上任何锚」占 **91%**、歧义仅 **1%**
⇒ 瓶颈是锚覆盖不足,不是算法。所以正解不是做个批量点击界面,
而是**人工确认少数覆盖广的锚图,其余自动继承** —— 有杠杆的人工投入。

**三条硬规则**:

1. **只以人工确认的图为锚**。用传播结果当锚会让一次误传播沿链扩散,
   且无法回溯源头。
2. **唯一匹配才传播**。歧义判 unknown —— 猜错的分区号会让轴号身份全错。
3. **不覆盖人工确认**。人的判断优先于自动推导。
"""
from __future__ import annotations

import pytest

from services.axis_zone_propagation import (
    SCALE_RATIO_REVIEW_THRESHOLD, axis_gap_sequences, propagate_zone_labels,
)


def _axis(offset_pt: float, kind: str = "numeric", zone: int = 0,
          angle: float = 0.0) -> dict:
    return {"offset_pt": offset_pt, "label_kind": kind,
            "zone_index": zone, "angle_deg": angle}


# ── 轴距序列提取 ──────────────────────────────────────────────

@pytest.mark.unit
def test_gaps_are_grouped_by_zone_kind_and_angle():
    """**按角度分组**:90° 的 numeric 与 0° 的 alpha 同属正交轴网。

    只留 0° 会把整个 numeric 方向当斜轴丢掉 —— 实测锚图 A-01-02A
    的 numeric 有 39 条全是 90°,一度被整组漏掉。
    """
    axes = [_axis(0), _axis(100), _axis(200), _axis(300), _axis(400), _axis(500),
            _axis(0, "alpha", angle=90.0), _axis(100, "alpha", angle=90.0)]
    got = axis_gap_sequences(axes, scale_m_pt=0.1, min_gaps=3)
    assert (0, "numeric", 0.0) in got
    assert got[(0, "numeric", 0.0)] == [10.0] * 5


@pytest.mark.unit
def test_groups_below_the_threshold_are_dropped():
    axes = [_axis(0), _axis(100), _axis(200)]
    assert axis_gap_sequences(axes, scale_m_pt=0.1, min_gaps=5) == {}


@pytest.mark.unit
def test_duplicate_circles_on_one_axis_do_not_create_zero_gaps():
    """§8.0.2 允许一条轴线两端各注一个圈 —— 同轴重复不该变成 0 轴距。"""
    axes = [_axis(0), _axis(0.5), _axis(100), _axis(200), _axis(300), _axis(400)]
    got = axis_gap_sequences(axes, scale_m_pt=0.1, min_gaps=4)
    assert all(g > 0.05 for seq in got.values() for g in seq)


@pytest.mark.unit
def test_empty_axes_is_safe():
    assert axis_gap_sequences([], scale_m_pt=0.1) == {}
    assert axis_gap_sequences(None, scale_m_pt=0.1) == {}


# ── 传播 ──────────────────────────────────────────────────────

_ANCHOR_SEQ = [7.1, 4.4, 4.1, 7.3, 1.2, 7.1, 2.4, 5.9]


def _anchor(label: str = "1") -> dict:
    return {"drawing_id": "anchor-1", "zone_index": 0, "zone_label": label,
            "sequence": _ANCHOR_SEQ}


@pytest.mark.unit
def test_unique_match_propagates_the_zone_label():
    """**核心用例**:局部图唯一匹配上锚 ⇒ 继承分区号。"""
    got = propagate_zone_labels(
        [{"drawing_id": "d1", "zone_index": 0, "sequence": _ANCHOR_SEQ[2:7]}],
        [_anchor("1")])
    assert len(got) == 1
    assert got[0].zone_label == "1"
    assert got[0].anchor_drawing_id == "anchor-1"
    assert got[0].source == "propagated"


@pytest.mark.unit
def test_ambiguous_candidate_is_not_propagated():
    """歧义判 unknown —— 猜错的分区号会让轴号身份全错。"""
    periodic = [8.0] * 10
    got = propagate_zone_labels(
        [{"drawing_id": "d1", "zone_index": 0, "sequence": [8.0] * 5}],
        [{"drawing_id": "a", "zone_index": 0, "zone_label": "1",
          "sequence": periodic}])
    assert got == []


@pytest.mark.unit
def test_candidate_matching_two_anchors_is_not_propagated():
    shared = [8.4, 6.0, 7.2, 9.3, 5.1]
    got = propagate_zone_labels(
        [{"drawing_id": "d1", "zone_index": 0, "sequence": shared}],
        [{"drawing_id": "a", "zone_index": 0, "zone_label": "1",
          "sequence": [3.0] + shared},
         {"drawing_id": "b", "zone_index": 0, "zone_label": "2",
          "sequence": [11.0] + shared}])
    assert got == []


@pytest.mark.unit
def test_scale_ratio_drift_is_flagged_not_rejected():
    """比例比偏大 ⇒ **标记 needs_review,照常传播**。

    我把它当门禁写错了两次:
    - 5%:比匹配容差(2%)宽,永不触发。实测「比例比否决 0」
      看着像数据干净,其实是这条路径从未执行过。
    - 1%:确实会拒绝,但拒掉的是**真匹配** —— 实测 169 组里 92 组(54%)
      落在 1%~1.5%,**无一超过 1.5%**。集中成簇而非长尾,
      是系统性比例差而非误匹配的形态。当门禁用会把传播从 143 砍到 25。

    1.2% 的比例差在 100 米建筑上是 1.2 米:值得人看一眼,
    不值得直接丢弃(对比未配准时的 83~103 米错位)。
    """
    ratio = 1 + SCALE_RATIO_REVIEW_THRESHOLD * 1.5
    got = propagate_zone_labels(
        [{"drawing_id": "d1", "zone_index": 0,
          "sequence": [g * ratio for g in _ANCHOR_SEQ]}],
        [_anchor()])
    assert len(got) == 1, "该传播,只是要标记"
    assert got[0].needs_review is True


@pytest.mark.unit
def test_ratio_can_never_exceed_the_match_tolerance():
    """**这就是它做不成独立门禁的原因**(数学不变式)。

    比例比 = target 总长 ÷ anchor 对应段总长,而匹配时**每一段**都已要求
    偏差 ≤ SCALE_TOLERANCE。各段都在容差内,总和的比例比必然也在容差内 ——
    所以任何比 SCALE_TOLERANCE 宽的比例比阈值都永远不会触发。
    """
    from services.axis_sequence_match import SCALE_TOLERANCE

    over = [g * (1 + SCALE_TOLERANCE * 2) for g in _ANCHOR_SEQ]
    assert propagate_zone_labels(
        [{"drawing_id": "d1", "zone_index": 0, "sequence": over}],
        [_anchor()]) == [], "超出匹配容差的在匹配阶段就被挡了,轮不到比例比判"


@pytest.mark.unit
def test_small_scale_drift_is_accepted_without_review_flag():
    ratio = 1 + SCALE_RATIO_REVIEW_THRESHOLD * 0.5
    got = propagate_zone_labels(
        [{"drawing_id": "d1", "zone_index": 0,
          "sequence": [g * ratio for g in _ANCHOR_SEQ]}],
        [_anchor()])
    assert len(got) == 1
    assert got[0].scale_ratio == pytest.approx(ratio, rel=0.01)
    assert got[0].needs_review is False


@pytest.mark.unit
def test_the_anchor_drawing_itself_is_skipped():
    """锚图不该传播给自己。"""
    got = propagate_zone_labels(
        [{"drawing_id": "anchor-1", "zone_index": 0, "sequence": _ANCHOR_SEQ}],
        [_anchor()])
    assert got == []


@pytest.mark.unit
def test_already_confirmed_zones_are_not_overwritten():
    """**人的判断优先**:已有人工确认的分区不接受传播。"""
    got = propagate_zone_labels(
        [{"drawing_id": "d1", "zone_index": 0, "sequence": _ANCHOR_SEQ[2:7]}],
        [_anchor()],
        already_confirmed={("d1", 0)})
    assert got == []


@pytest.mark.unit
def test_other_zones_of_a_partly_confirmed_drawing_still_propagate():
    """同一张图的其他分区不受影响 —— 确认是**按分区**的,不是按图。"""
    got = propagate_zone_labels(
        [{"drawing_id": "d1", "zone_index": 1, "sequence": _ANCHOR_SEQ[2:7]}],
        [_anchor()],
        already_confirmed={("d1", 0)})
    assert len(got) == 1
    assert got[0].zone_index == 1


@pytest.mark.unit
def test_unmatched_candidate_yields_nothing():
    got = propagate_zone_labels(
        [{"drawing_id": "d1", "zone_index": 0,
          "sequence": [3.3, 4.7, 2.9, 8.8, 1.5]}],
        [_anchor()])
    assert got == []


@pytest.mark.unit
def test_empty_inputs_are_safe():
    assert propagate_zone_labels([], [_anchor()]) == []
    assert propagate_zone_labels([{"drawing_id": "d", "zone_index": 0,
                                   "sequence": _ANCHOR_SEQ}], []) == []
    assert propagate_zone_labels(None, None) == []


@pytest.mark.unit
def test_result_is_deterministic():
    """同一批输入必须给同样的结果 —— 顺序依赖会让诊断无法预测生产行为。

    这条是上一轮的教训:轴网聚合曾因 stable sort 在同档内保留输入顺序,
    导致 builder 与诊断脚本看到不同结果,排查方向被带偏三轮。
    """
    candidates = [
        {"drawing_id": f"d{i}", "zone_index": 0, "sequence": _ANCHOR_SEQ[2:7]}
        for i in range(5)
    ]
    forward = [(p.drawing_id, p.zone_label)
               for p in propagate_zone_labels(candidates, [_anchor()])]
    backward = [(p.drawing_id, p.zone_label)
                for p in propagate_zone_labels(candidates[::-1], [_anchor()])]
    assert forward == sorted(backward)
