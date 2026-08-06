"""轴号序列必须与坐标同向递增(GB/T 50001 §8.0.3)。

**实测违规**(模型 v32 的 F5 层 x 向):

```
1 2 3 4 5 10 12 14 6 15 7 8
        ↑           ↑
      跳到 10     又回到 6
```

§8.0.3 规定「横向编号用阿拉伯数字,**从左至右**;竖向用大写字母,
**从下至上**」—— 轴号必须随坐标单调递增。

**乱序的含义不是标签错,而是这些轴线的坐标不可信**:
它们来自变换不一致的多张图,拼在一起后位置互相穿插。
所以违规要**报出来**,严重时**宁可不给该方向的轴网**——
一套顺序错乱的轴网比没有轴网更误导人。
"""
from __future__ import annotations

import pytest

from services.model_elements import (
    MAX_SEQUENCE_OUTLIER_RATIO, axis_sequence_outliers,
)


def _e(label: str, coord: float) -> dict:
    return {"label": label, "coord": coord}


@pytest.mark.unit
def test_monotonic_numeric_sequence_has_no_inversion():
    entries = [_e("1", 0.0), _e("2", 8.0), _e("3", 16.0), _e("4", 24.0)]
    assert axis_sequence_outliers(entries) == 0


@pytest.mark.unit
def test_measured_f5_sequence_is_flagged():
    """**实测用例**:v32 的 F5 序列。"""
    labels = ["1", "2", "3", "4", "5", "10", "12", "14", "6", "15", "7", "8"]
    entries = [_e(l, i * 8.0) for i, l in enumerate(labels)]
    assert axis_sequence_outliers(entries) > 0


@pytest.mark.unit
def test_letter_sequence_follows_the_alphabet_skipping_forbidden():
    """§8.0.4 跳过 I、O、Z —— `H` 之后是 `J`,不算逆序。"""
    entries = [_e("G", 0.0), _e("H", 8.0), _e("J", 16.0), _e("K", 24.0)]
    assert axis_sequence_outliers(entries) == 0


@pytest.mark.unit
def test_letter_going_backwards_is_an_inversion():
    entries = [_e("A", 0.0), _e("C", 8.0), _e("B", 16.0)]
    assert axis_sequence_outliers(entries) == 1


@pytest.mark.unit
def test_mixed_kinds_are_compared_within_their_own_kind():
    """数字与字母各自成序 —— 混在一起比较没有意义。"""
    entries = [_e("1", 0.0), _e("A", 4.0), _e("2", 8.0), _e("B", 12.0)]
    assert axis_sequence_outliers(entries) == 0


@pytest.mark.unit
def test_zone_prefix_is_stripped_before_comparing():
    """§8.0.5:`1-1`、`1-2` 是同一分区的连续序列。"""
    entries = [_e("1-1", 0.0), _e("1-2", 8.0), _e("1-3", 16.0)]
    assert axis_sequence_outliers(entries) == 0


@pytest.mark.unit
def test_different_zones_are_not_compared():
    """不同分区各自从 1 开始,跨区比较会误报。"""
    entries = [_e("1-1", 0.0), _e("1-2", 8.0), _e("2-1", 50.0), _e("2-2", 58.0)]
    assert axis_sequence_outliers(entries) == 0


@pytest.mark.unit
def test_too_few_entries_never_flags():
    assert axis_sequence_outliers([_e("1", 0.0)]) == 0
    assert axis_sequence_outliers([]) == 0


@pytest.mark.unit
def test_ratio_threshold_is_conservative():
    """阈值要**宽**:附加轴线、局部补号都可能造成个别逆序,
    只有大面积错乱才说明轴网不可信。"""
    assert 0.1 <= MAX_SEQUENCE_OUTLIER_RATIO <= 0.4


# ── 端到端:严重乱序时不输出该方向 ──────────────────────────────

@pytest.mark.unit
def test_measured_f5_direction_is_withheld():
    """**实测用例**:v33 的 F5 序列离群 25% ≥ 阈值,该方向不输出。

    它是两套轴网交织(`1~8` 一套、`10 12 14 15` 一套),
    整套坐标都不可信 —— 给出去只会误导。
    """
    from services.model_elements import _axes_scene_payload

    labels = ["1", "2", "3", "4", "5", "10", "12", "14", "6", "15", "7", "8"]
    payload = _axes_scene_payload(
        {"x": [[l, i * 8.0] for i, l in enumerate(labels)], "y": []}, "d1")
    assert payload is None or payload["x"] == []


@pytest.mark.unit
def test_mild_outliers_are_reported_but_kept():
    """个别离群(附加轴线/局部补号)要保留并报数,不能一有离群就丢。"""
    from services.model_elements import _axes_scene_payload

    # 10 条里 1 条离群 = 10% < 20% 阈值
    labels = ["1", "2", "3", "4", "9", "5", "6", "7", "8", "10"]
    payload = _axes_scene_payload(
        {"x": [[l, i * 8.0] for i, l in enumerate(labels)], "y": []}, "d1")
    assert payload is not None and payload["x"]
    assert payload["sequence_outliers"] > 0


@pytest.mark.unit
def test_badly_scrambled_direction_is_withheld():
    """**宁可不给,也不给一套顺序错乱的轴网** —— 后者更误导人。"""
    from services.model_elements import _axes_scene_payload

    # 逆序占比极高的序列
    labels = ["9", "8", "7", "6", "5", "4", "3", "2", "1"]
    payload = _axes_scene_payload(
        {"x": [[l, i * 8.0] for i, l in enumerate(labels)], "y": []}, "d1")
    assert payload is None or payload["x"] == []


@pytest.mark.unit
def test_clean_sequence_is_kept():
    from services.model_elements import _axes_scene_payload

    payload = _axes_scene_payload(
        {"x": [["1", 0.0], ["2", 8.0], ["3", 16.0]], "y": []}, "d1")
    assert payload is not None
    assert len(payload["x"]) == 3
    assert payload["sequence_outliers"] == 0
