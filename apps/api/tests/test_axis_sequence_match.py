"""轴距序列匹配 —— Phase J 主线 J1 的核心算法。

**要解决的问题**:全项目只有 11 张图(0.5%)有世界锚点,`placed_drawings=0`。
板 0 块、轴网入模 4/14 层、构件差 83~103 米,都是这一个缺口的表现。

**已证伪的三条路**(见 `docs/PHASE_J_BLUEPRINT.md` §2.1):

| 路线 | 结果 | 失败根因 |
|---|---|---|
| ① 轴距序列**精确**指纹 | 只匹配到锚图自己 | **轴线检出有缺失,序列不会精确相同** |
| ② 轴号匹配 | 399 张与锚图同名轴号数为 0 | 锚图带分区前缀(`1-1`),其余是裸标签(`1`) |
| ③ 分区身份指纹 | 318 组里 191 组只出现 1 次 | 同一分区在不同图上检出的轴线数不稳定 |

**本模块是对路线①的直接改进,不是重走**:路线①失败在「精确匹配」,
而它的失败原因恰恰是子序列匹配所容忍的——局部图漏检一条轴线时,
两段轴距会**合并**成相邻两个锚距之和。

```
锚图:  8.4   8.4   6.0   8.4   8.4
局部:     16.8       6.0      8.4
          ↑ 8.4+8.4(漏检中间那条)   ↑ 匹配第 4 段
```

匹配成功**一次拿到三样东西**:轴号(含分区前缀)、分区归属、世界坐标
—— 正是上述三条路线各自卡住的那三样。

**最危险的失效模式是等距柱网**:轴距全是 8.4 米时,任何位置都能匹配,
而规则柱网在工程上极其常见。**必须判歧义,不能猜**——
猜错的世界坐标比没有世界坐标更糟(错的会带着满分置信度骗过所有下游)。
"""
from __future__ import annotations

import pytest

from services.axis_sequence_match import (
    MAX_MERGE_SPAN, MIN_MATCH_GAPS, SCALE_TOLERANCE, match_gap_sequence,
)


@pytest.mark.unit
def test_identical_sequence_matches_from_the_start():
    """最简单的情形:序列一致 ⇒ 从头逐段对上。"""
    anchor = [8.4, 8.4, 6.0, 8.4]
    got = match_gap_sequence([8.4, 8.4, 6.0, 8.4] * 2, anchor * 2)
    assert got is not None
    assert got.start_index == 0
    assert got.spans == [1] * 8


@pytest.mark.unit
def test_one_missing_axis_merges_two_gaps():
    """**核心用例**:局部图漏检一条轴线 ⇒ 两段轴距合并。

    这正是路线①(精确指纹)失败的原因,也是本方案存在的理由。
    """
    anchor = [8.4, 8.4, 6.0, 8.4, 7.2, 9.0]
    target = [16.8, 6.0, 8.4, 7.2, 9.0]      # 首两段合并为 16.8
    got = match_gap_sequence(target, anchor)
    assert got is not None
    assert got.start_index == 0
    assert got.spans == [2, 1, 1, 1, 1]


@pytest.mark.unit
def test_partial_drawing_matches_a_middle_run():
    """局部图只画中间一段 ⇒ 要能匹配到正确的起点。

    §8.0.5 分区图、局部放大图都是这个形态。
    """
    anchor = [5.1, 8.4, 6.0, 7.2, 9.3, 4.8, 6.6]
    got = match_gap_sequence([6.0, 7.2, 9.3, 4.8, 6.6], anchor)
    assert got is not None
    assert got.start_index == 2


@pytest.mark.unit
def test_small_scale_error_is_tolerated():
    """比例误差在容差内仍要匹配 —— 变换比例已按 §6.0.4 吸附,残差很小。"""
    anchor = [8.4, 6.0, 7.2, 9.3, 5.1]
    target = [g * 1.015 for g in anchor]      # 1.5% < 2% 容差
    assert match_gap_sequence(target, anchor) is not None


@pytest.mark.unit
def test_large_scale_error_is_rejected():
    """比例差 20% ⇒ 不是同一套轴网,必须拒绝。"""
    anchor = [8.4, 6.0, 7.2, 9.3, 5.1]
    target = [g * 1.20 for g in anchor]
    assert match_gap_sequence(target, anchor) is None


# ── 失效模式:必须判歧义而不是猜 ──────────────────────────────

@pytest.mark.unit
def test_uniform_grid_is_ambiguous():
    """**最危险的失效模式**:等距柱网处处能匹配。

    轴距全是 8.4 米时,局部序列在任何起点都对得上。规则柱网在工程上
    极其常见,**必须判歧义**——猜一个世界坐标比没有更糟:
    错的会带着满分置信度骗过所有下游(见 drawing_transform 的 1:335 万教训)。
    """
    anchor = [8.4] * 12
    assert match_gap_sequence([8.4] * 6, anchor) is None


@pytest.mark.unit
def test_repeated_pattern_is_ambiguous():
    """重复段(如两个对称单元)同样歧义。"""
    unit = [8.4, 6.0, 7.2, 5.1, 9.3]
    assert match_gap_sequence(unit, unit + unit) is None


@pytest.mark.unit
def test_merge_can_create_a_second_path():
    """合并本身会制造歧义:`[6.0, 6.0]` 既能逐段对,也能被别处的 12.0 吃掉。

    只要存在第二条可行路径就判 None —— 宁可少匹配,不可错匹配。
    """
    anchor = [12.0, 6.0, 6.0, 12.0, 6.0, 6.0]
    assert match_gap_sequence([12.0, 6.0, 6.0], anchor) is None


@pytest.mark.unit
def test_unrelated_sequence_does_not_match():
    anchor = [8.4, 6.0, 7.2, 9.3, 5.1]
    assert match_gap_sequence([3.3, 4.7, 2.9, 8.8, 1.5], anchor) is None


@pytest.mark.unit
def test_too_many_consecutive_misses_cannot_be_bridged():
    """连续漏检超过合并上限 ⇒ 匹配不上(而不是硬凑)。"""
    anchor = [2.0] * (MAX_MERGE_SPAN + 1) + [8.4, 6.0, 7.2, 9.3]
    target = [2.0 * (MAX_MERGE_SPAN + 1), 8.4, 6.0, 7.2, 9.3]
    assert match_gap_sequence(target, anchor) is None


# ── 门槛与边界 ────────────────────────────────────────────────

@pytest.mark.unit
def test_short_sequence_is_refused():
    """**辨识度门槛**:太短的序列碰巧对上的概率高,不参与匹配。"""
    anchor = [8.4, 6.0, 7.2, 9.3, 5.1, 4.2, 6.6]
    short = [8.4, 6.0, 7.2][:MIN_MATCH_GAPS - 1]
    assert match_gap_sequence(short, anchor) is None


@pytest.mark.unit
def test_exactly_at_the_threshold_is_accepted():
    anchor = [8.4, 6.0, 7.2, 9.3, 5.1, 4.2, 6.6]
    assert match_gap_sequence(anchor[:MIN_MATCH_GAPS], anchor) is not None


@pytest.mark.unit
def test_target_longer_than_anchor_does_not_match():
    assert match_gap_sequence([8.4] * 9, [8.4, 6.0, 7.2, 9.3, 5.1]) is None


@pytest.mark.unit
def test_empty_and_degenerate_inputs_are_safe():
    assert match_gap_sequence([], [8.4, 6.0, 7.2, 9.3, 5.1]) is None
    assert match_gap_sequence([8.4, 6.0, 7.2, 9.3, 5.1], []) is None
    assert match_gap_sequence(None, None) is None


@pytest.mark.unit
def test_non_positive_gaps_are_rejected():
    """零或负轴距是数据错误(重复轴线/坐标乱序),不该进匹配。"""
    anchor = [8.4, 6.0, 7.2, 9.3, 5.1, 4.2]
    assert match_gap_sequence([8.4, 0.0, 7.2, 9.3, 5.1], anchor) is None
    assert match_gap_sequence([8.4, -6.0, 7.2, 9.3, 5.1], anchor) is None


@pytest.mark.unit
def test_tolerance_is_relative_not_absolute():
    """容差必须**按比例**:误差来自比例尺,大轴距的绝对误差自然更大。

    绝对容差会让大跨度轴网(如 30 米柱距)全部落空。
    """
    anchor = [30.0, 25.0, 28.0, 32.0, 27.0]
    target = [g * (1 + SCALE_TOLERANCE * 0.9) for g in anchor]
    assert match_gap_sequence(target, anchor) is not None


@pytest.mark.unit
def test_result_reports_the_scale_ratio():
    """要报出实测比例比 —— 它是独立的一道合理性校验。

    比例比明显偏离 1.0 说明两图的 `drawing_transform` 不一致,
    即便序列对上了也该存疑。
    """
    anchor = [8.4, 6.0, 7.2, 9.3, 5.1]
    got = match_gap_sequence([g * 1.01 for g in anchor], anchor)
    assert got is not None
    assert got.scale_ratio == pytest.approx(1.01, rel=0.005)


# ── 跨锚序列的全局歧义（J1-A 留一法实测暴露）──────────────────

@pytest.mark.unit
def test_ambiguous_within_one_anchor_must_not_be_claimed_by_another():
    """**J1-A 实测缺陷**:自身歧义的序列会被别的锚序列「抢走」。

    锚图 A-01-02A 实测:区1/numeric 的子序列 `[8:13]` 在**本组内**周期重复
    （9.1/8.0 交替）故本组判歧义返回 None，却在**区2/alpha** 上唯一命中 ——
    于是系统会把它判成区 2，**分区归属直接搞错**。

    根因:歧义只在单个锚序列内部判定，没有跨锚序列做全局判定。
    正解是把所有锚序列的路径数**加总**，总数不为 1 就拒绝。
    """
    from services.axis_sequence_match import match_against_anchors

    periodic = [9.1, 8.0, 9.1, 8.0, 9.1]
    anchors = {
        "self": [4.2, 8.9] + periodic + [8.0, 9.1, 8.0],   # 本组内多处可匹配
        "other": [3.3, 4.7] + periodic + [2.9, 6.6],       # 别组唯一可匹配
    }
    assert match_against_anchors(periodic, anchors) is None


@pytest.mark.unit
def test_globally_unique_match_is_returned_with_its_key():
    from services.axis_sequence_match import match_against_anchors

    anchors = {
        "zone0": [7.1, 4.4, 4.1, 7.3, 1.2, 7.1, 2.4],
        "zone2": [8.0, 6.7, 9.9, 7.4, 8.0, 8.0, 10.0],
    }
    got = match_against_anchors([4.1, 7.3, 1.2, 7.1, 2.4], anchors)
    assert got is not None
    key, matched = got
    assert key == "zone0"
    assert matched.start_index == 2


@pytest.mark.unit
def test_no_anchor_matches_returns_none():
    from services.axis_sequence_match import match_against_anchors

    anchors = {"a": [7.1, 4.4, 4.1, 7.3, 1.2, 7.1]}
    assert match_against_anchors([3.3, 4.7, 2.9, 8.8, 1.5], anchors) is None


@pytest.mark.unit
def test_two_anchors_each_matching_once_is_still_ambiguous():
    """两个锚序列各命中一次 ⇒ 总数 2 ⇒ 拒绝。分区归属不能靠猜。"""
    from services.axis_sequence_match import match_against_anchors

    shared = [8.4, 6.0, 7.2, 9.3, 5.1]
    anchors = {"a": [3.0] + shared + [4.0], "b": [11.0] + shared + [2.0]}
    assert match_against_anchors(shared, anchors) is None


@pytest.mark.unit
def test_empty_anchor_set_is_safe():
    from services.axis_sequence_match import match_against_anchors

    assert match_against_anchors([8.4, 6.0, 7.2, 9.3, 5.1], {}) is None
    assert match_against_anchors([8.4, 6.0, 7.2, 9.3, 5.1], None) is None
