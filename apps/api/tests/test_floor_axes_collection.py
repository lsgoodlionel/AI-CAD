"""楼层轴网聚合不该受构件选图上限的约束。

**实测缺口**(模型 v33):六个楼层 scene 里**无轴网**,而原料充足:

| 层 | 图 | 有轴号 | 有变换 | **两者都有** |
|---|---:|---:|---:|---:|
| F1 | 195 | 178 | 153 | **139** |
| F4 | 136 | 124 | 114 | **105** |
| F6 | 43 | 37 | 34 | 33 |

根因:轴网是在**构件识别的循环里**顺带聚合的,而那个循环只跑
`pick_element_drawings` 选中的 **2 张**结构图。选中的那 2 张若恰好
没轴号或没变换,该层轴网就是空的 —— 另外 137 张白白浪费。

**两者不该共用一个上限**:构件识别每图要 10~40 秒(几何提取 + 识别 +
YOLO),所以限 2 张;而轴网只是「坐标 + 标签」的纯计算,聚合几乎不花时间。

仍然要限量,但限的理由不同:图越多、变换不一致的风险越大,
所以按**定位可靠度**排序后取前若干张,再由
`dedupe_axis_labels` 与序列校验(§8.0.3)兜底。
"""
from __future__ import annotations

import pytest

from services.model_elements import MAX_AXIS_SOURCE_PLANS, collect_floor_axes


class _T:
    def __init__(self, scale_m_pt: float) -> None:
        self.scale_m_pt = scale_m_pt
        self.origin_x = 0.0
        self.origin_y = 0.0
        self.page_h = 2384.0
        self.confidence = 1.0


_STD = _T(150 * 25.4 / 72 / 1000)
_ODD = _T(120 * 25.4 / 72 / 1000)          # 非标准且不会被吸附


def _d(did: str, title: str = "一层结构平面图") -> dict:
    return {"id": did, "drawing_no": did, "title": title,
            "discipline": "structure"}


def _axes(*labels: str) -> list[dict]:
    """构造识别轴线:数字轴号,90° 竖向。

    **偏移按轴号本身算**,不是按在本次调用里的下标 —— 否则
    `_axes("1")`、`_axes("2")`、`_axes("3")` 三条都落在 offset 0,
    被 `_merge_axes` 的坐标容差(0.3m)当成同一条合并掉,
    测出来的就不是「聚合了几张图」而是「容差去重」。
    """
    out = []
    for label in labels:
        try:
            index = int(label)
        except ValueError:
            index = abs(hash(label)) % 50
        out.append({"label": label, "label_kind": "numeric", "angle_deg": 90.0,
                    "offset_pt": -200.0 * index, "zone_label_confirmed": True})
    return out


@pytest.mark.unit
def test_axes_come_from_more_drawings_than_the_element_cap():
    """**核心用例**:轴网上限必须远大于构件选图上限(2)。"""
    from services.model_elements import _MAX_STRUCTURE_PLANS

    assert MAX_AXIS_SOURCE_PLANS > _MAX_STRUCTURE_PLANS * 2


@pytest.mark.unit
def test_collects_from_every_drawing_that_has_axes_and_a_transform():
    drawings = [_d("a"), _d("b"), _d("c")]
    got = collect_floor_axes(
        drawings,
        transforms={"a": _STD, "b": _STD, "c": _STD},
        recognized={"a": _axes("1"), "b": _axes("2"), "c": _axes("3")})
    labels = {e[0] for e in got["x"]}
    assert labels == {"1", "2", "3"}


@pytest.mark.unit
def test_drawings_without_a_transform_are_skipped():
    """没有变换就没有米坐标,轴线放不到正确位置 —— 跳过而不是硬放。"""
    got = collect_floor_axes(
        [_d("a"), _d("b")],
        transforms={"a": _STD},
        recognized={"a": _axes("1"), "b": _axes("9")})
    assert {e[0] for e in got["x"]} == {"1"}


@pytest.mark.unit
def test_standard_scale_drawings_are_used_first():
    """可靠度高的先合 —— 同名冲突时先到的胜出(见 dedupe_axis_labels)。"""
    got = collect_floor_axes(
        [_d("odd"), _d("std")],
        transforms={"odd": _ODD, "std": _STD},
        recognized={"odd": _axes("1"), "std": _axes("1")},
        max_drawings=1)
    assert len(got["x"]) == 1


@pytest.mark.unit
def test_respects_the_cap():
    drawings = [_d(f"d{i}") for i in range(30)]
    got = collect_floor_axes(
        drawings,
        transforms={f"d{i}": _STD for i in range(30)},
        recognized={f"d{i}": _axes(str(i + 1)) for i in range(30)},
        max_drawings=5)
    assert len(got["x"]) <= 5


@pytest.mark.unit
def test_empty_inputs_are_safe():
    assert collect_floor_axes([], transforms={}, recognized={}) == {"x": [], "y": []}
    assert collect_floor_axes([_d("a")], transforms={}, recognized={}) == {
        "x": [], "y": []}


@pytest.mark.unit
def test_empty_collected_axes_falls_back():
    """**这条防的是一个 truthy 陷阱**:`{"x": [], "y": []}` 是非空 dict,
    写成 `collected or fallback` 时 `or` 永远不回落,
    没有识别轴号的场景会整个丢掉轴网。
    """
    from services.model_elements import _prefer_collected_axes

    fallback = {"x": [["1", 0.0]], "y": []}
    assert _prefer_collected_axes({"x": [], "y": []}, fallback) is fallback
    assert _prefer_collected_axes(None, fallback) is fallback


@pytest.mark.unit
def test_non_empty_collected_axes_wins():
    from services.model_elements import _prefer_collected_axes

    collected = {"x": [["1", 0.0], ["2", 8.0]], "y": []}
    fallback = {"x": [["9", 99.0]], "y": []}
    assert _prefer_collected_axes(collected, fallback) is collected


# ── 一致性门禁:不一致的来源要跳过,不是照单全收 ──────────────────

@pytest.mark.unit
def test_inconsistent_drawing_is_skipped():
    """**实测教训**:把聚合上限从 2 提到 12,轴网覆盖反而从 6 层跌到 2 层。

    原因是新引入的图变换与已有的不一致,同名轴号落在不同位置,
    冲突暴增(实测 B3 一层 **74 条**),去重后保留的反而更少。

    **更多来源 ≠ 更好的结果**。正确做法是逐张检验:新图与已聚合的轴网
    在**同名轴号上位置对不上**,就说明它的变换与主组不一致,跳过它。
    """
    from services.model_elements import collect_floor_axes

    good = _axes("1", "2", "3")
    # 同名轴号但整体偏移 20 米 —— 变换不一致
    shifted = [{**a, "offset_pt": a["offset_pt"] + 20.0 / (150 * 25.4 / 72 / 1000)}
               for a in _axes("1", "2", "3")]
    got = collect_floor_axes(
        [_d("good"), _d("bad")],
        transforms={"good": _STD, "bad": _STD},
        recognized={"good": good, "bad": shifted})
    # 只保留一致的那组:3 条,不是 6 条
    assert len(got["x"]) == 3


@pytest.mark.unit
def test_consistent_drawings_are_all_merged():
    """一致的来源要全部并入 —— 各图识别到部分轴线,并集才完整。"""
    from services.model_elements import collect_floor_axes

    got = collect_floor_axes(
        [_d("a"), _d("b")],
        transforms={"a": _STD, "b": _STD},
        recognized={"a": _axes("1", "2"), "b": _axes("2", "3")})
    assert {e[0] for e in got["x"]} == {"1", "2", "3"}


@pytest.mark.unit
def test_first_drawing_is_always_accepted():
    """第一张没有参照,必然接受(它就是主组的基准)。"""
    from services.model_elements import collect_floor_axes

    got = collect_floor_axes(
        [_d("a")], transforms={"a": _STD}, recognized={"a": _axes("1", "2")})
    assert len(got["x"]) == 2


# ── 最大一致子集:基准选错不该全盘皆错 ──────────────────────────

@pytest.mark.unit
def test_picks_the_largest_consistent_group_not_just_the_first():
    """**实测教训**:用「第一张」当基准,若它恰好是离群值,
    后面**正确的全被挡掉**。

    v35 实测:F1(195 张图)、F2、F3、B1 在加了一致性门禁后全部失去轴网,
    而它们在 v33 是有的 —— 基准选错的代价是整层归零。

    正确做法是找**彼此一致的最大那组**,而不是「与第一张一致」。
    """
    from services.model_elements import collect_floor_axes

    shift = 30.0 / (150 * 25.4 / 72 / 1000)      # 30 米偏移
    outlier = [{**a, "offset_pt": a["offset_pt"] + shift}
               for a in _axes("1", "2", "3")]
    # 排序后 outlier 排在最前(可靠度相同则保持原顺序)
    got = collect_floor_axes(
        [_d("outlier"), _d("g1"), _d("g2"), _d("g3")],
        transforms={k: _STD for k in ("outlier", "g1", "g2", "g3")},
        recognized={"outlier": outlier,
                    "g1": _axes("1", "2", "3"),
                    "g2": _axes("2", "3", "4"),
                    "g3": _axes("3", "4", "5")})
    # 应当采纳 g1/g2/g3 这一致的三张(并集 1~5),而不是被 outlier 带偏
    assert {e[0] for e in got["x"]} == {"1", "2", "3", "4", "5"}


@pytest.mark.unit
def test_single_drawing_still_works():
    from services.model_elements import collect_floor_axes

    got = collect_floor_axes(
        [_d("a")], transforms={"a": _STD}, recognized={"a": _axes("1", "2")})
    assert len(got["x"]) == 2


# ── 结果必须可复现 ──────────────────────────────────────────────

@pytest.mark.unit
def test_result_does_not_depend_on_input_order():
    """**同样的数据,顺序不同必须得到同样的结果**。

    实测教训:`_transform_rank` 只返回 0/1/2 三档,stable sort 在同档内
    保持**输入顺序**。而 builder 拿到的 `floor_drawings` 是 DB 返回顺序、
    诊断脚本拿到的是 scene 里的顺序 —— **两者的「前 12 张」不是同一批**,
    于是同一层算出的轴网不同,诊断结论无法用来预期 builder 的行为。

    排序键必须**完全确定**:同档时按 drawing_id 定序。
    """
    from services.model_elements import collect_floor_axes

    drawings = [_d(f"d{i}") for i in range(6)]
    transforms = {f"d{i}": _STD for i in range(6)}
    recognized = {f"d{i}": _axes(str(i + 1)) for i in range(6)}

    forward = collect_floor_axes(drawings, transforms=transforms,
                                 recognized=recognized, max_drawings=3)
    backward = collect_floor_axes(list(reversed(drawings)),
                                  transforms=transforms,
                                  recognized=recognized, max_drawings=3)
    assert forward == backward
