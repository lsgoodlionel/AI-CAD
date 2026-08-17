"""轴号在同一方向上必须唯一 —— 这是用户报告「轴线名称不对」的根因。

**实测证据**(模型 v31 的 F5 层):

```
{"coord":  8.394, "label": "2"}
{"coord": 16.290, "label": "2"}    ← 同名 `2` 出现在两个位置
{"coord": 16.794, "label": "3"}    ← 与上条只差 0.5 米
```

`_merge_axes` **只按坐标去重（容差 0.3 米），完全不看标签**。
于是同一条 `2` 轴在两张图上因变换差异落到 8.394 与 16.290
（相距 **7.9 米** > 容差）→ 不合并 → 变成两条都叫 `2` 的轴线。

**国标依据**:GB/T 50001 §8.0.3「依次注写」+ §8.0.5 分区编号 ⇒
同一分区、同一方向上**一个轴号只对应一条轴线**。

**同名冲突的真正含义**:不是「标签写错了」，而是**这些图的坐标变换不一致**。
留哪一条都不对——整套轴网都偏了 7.9 米。所以冲突要**报出来**，
并且**只保留最可靠那张图的轴网**（选图已按定位可靠度排序，先到的更可信）。
"""
from __future__ import annotations

import pytest

from services.model_elements import _merge_axes, dedupe_axis_labels


@pytest.mark.unit
def test_duplicate_label_in_one_direction_is_dropped():
    """**核心用例**:同名轴号只留一条。"""
    axes = {"x": [["1", 0.0], ["2", 8.394], ["2", 16.290], ["3", 16.794]],
            "y": []}
    got, conflicts = dedupe_axis_labels(axes)
    labels = [a[0] for a in got["x"]]
    assert labels == ["1", "2", "3"]
    assert conflicts == 1


@pytest.mark.unit
def test_the_first_occurrence_wins():
    """先到的更可信 —— 选图已按定位可靠度排序,第一张最可靠。"""
    axes = {"x": [["2", 8.394], ["2", 16.290]], "y": []}
    got, _ = dedupe_axis_labels(axes)
    assert got["x"] == [["2", 8.394]]


@pytest.mark.unit
def test_same_label_in_different_directions_is_fine():
    """x 向的 `1` 与 y 向的 `1` 是两条不同轴线,不冲突。"""
    axes = {"x": [["1", 0.0]], "y": [["1", 0.0]]}
    got, conflicts = dedupe_axis_labels(axes)
    assert len(got["x"]) == 1 and len(got["y"]) == 1
    assert conflicts == 0


@pytest.mark.unit
def test_empty_labels_are_not_deduped():
    """无标签轴线不参与唯一性约束 —— 它们本来就没身份。"""
    axes = {"x": [["", 1.0], ["", 2.0], ["", 3.0]], "y": []}
    got, conflicts = dedupe_axis_labels(axes)
    assert len(got["x"]) == 3 and conflicts == 0


@pytest.mark.unit
def test_zone_prefixed_labels_are_distinct():
    """§8.0.5:`1-1` 与 `2-1` 是不同分区的轴线,不算重复。"""
    axes = {"x": [["1-1", 0.0], ["2-1", 50.0]], "y": []}
    got, conflicts = dedupe_axis_labels(axes)
    assert len(got["x"]) == 2 and conflicts == 0


@pytest.mark.unit
def test_result_is_sorted_by_coordinate():
    """§8.0.3 依次注写 —— 轴线按坐标排列,便于核对序列是否单调。"""
    axes = {"x": [["3", 20.0], ["1", 0.0], ["2", 10.0]], "y": []}
    got, _ = dedupe_axis_labels(axes)
    assert [a[1] for a in got["x"]] == [0.0, 10.0, 20.0]


@pytest.mark.unit
def test_empty_input_is_safe():
    got, conflicts = dedupe_axis_labels({"x": [], "y": []})
    assert got == {"x": [], "y": []} and conflicts == 0
    got2, _ = dedupe_axis_labels(None)
    assert got2 == {"x": [], "y": []}


# ── 与 _merge_axes 的配合 ────────────────────────────────────────

@pytest.mark.unit
def test_merge_still_dedupes_by_coordinate():
    """坐标去重的旧行为要保住:容差内视为同一条轴线。"""
    agg = _merge_axes(None, {"x": [["1", 0.0]], "y": []})
    agg = _merge_axes(agg, {"x": [["1", 0.1]], "y": []})   # 0.1 < 0.3 容差
    assert len(agg["x"]) == 1


@pytest.mark.unit
def test_merge_then_dedupe_removes_the_measured_duplicate():
    """端到端:两张变换不一致的图合并后,同名轴号被收敛掉。"""
    agg = _merge_axes(None, {"x": [["1", 0.0], ["2", 8.394]], "y": []})
    agg = _merge_axes(agg, {"x": [["2", 16.290], ["3", 16.794]], "y": []})
    assert len(agg["x"]) == 4, "合并阶段按坐标去重,同名两条都还在"
    got, conflicts = dedupe_axis_labels(agg)
    assert [a[0] for a in got["x"]] == ["1", "2", "3"]
    assert conflicts == 1


@pytest.mark.unit
def test_scene_payload_drops_duplicates_and_reports_conflicts():
    """端到端:scene 载荷里不该再有同名轴号,且冲突数要报出来。"""
    from services.model_elements import _axes_scene_payload

    payload = _axes_scene_payload(
        {"x": [["1", 0.0], ["2", 8.394], ["2", 16.290], ["3", 16.794]],
         "y": []}, "d1")
    assert payload is not None
    labels = [e["label"] for e in payload["x"]]
    assert labels == sorted(set(labels), key=labels.index)
    assert labels == ["1", "2", "3"]
    assert payload["label_conflicts"] == 1
