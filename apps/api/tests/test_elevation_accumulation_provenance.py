"""累加出来的标高要继承**层高**的来源 —— 否则可信度被高估。

**实测缺陷**(模型 v39,上海大歌剧院):

| 层 | 标高 | `elevation_source` | `elevation_estimated` |
|---|---:|---|---|
| F1 | 0.00 | level_elevation_pairing | false |
| **F2** | **4.50** | **manual** | **false** ← 高估 |

人工在 2026-07-12 录了 F2 层高 6.0、F3 层高 5.4，但**没录 F1 的层高**。
而 F2 的标高是这样算出来的：

```
F2 标高 = F1 标高(0) + F1 层高(**默认 4.5**)
```

即 F2 的标高**完全由一个默认值推出**，却被标成 `manual` / 非估算 ——
界面上它与真正的实测标高长得一模一样。这正是楼层级标高门禁
本来要解决的问题(见 `docs/PROGRESS_2026Q3.md` 「P2 楼层级门禁」)。

根因：`_accumulate_manual_elevations` 只输出 `elevation_bottom_m`，
不带来源；而 `normalize_story_table` 看到 override 有标高就
无条件 `elev_estimated = False`。

**规则**：累加链上只要用过一个默认层高，其后(其下)所有层的标高都是估算。
±0.000 锚点层除外 —— 它是 §11.8.5 定义的基准，不由累加得来。
"""
from __future__ import annotations

import pytest

from services.model_builder import _accumulate_manual_elevations
from services.model_story import StoryLevel


def _level(story_key: str, order: int, height_m: float,
           height_estimated: bool) -> StoryLevel:
    return StoryLevel(
        building_unit_key="main", story_key=story_key, display_name=story_key,
        story_order=order, elevation_m=0.0, height_m=height_m, source="x",
        confidence=1.0, display_building_name="主楼",
        height_estimated=height_estimated,
    )


class _Norm:
    def __init__(self, levels: list[StoryLevel]) -> None:
        self.stories_by_building = {"main": levels}


@pytest.mark.unit
def test_anchor_floor_is_never_estimated():
    """±0.000 是 §11.8.5 定义的基准,不由累加得来。"""
    norm = _Norm([_level("F1", 1, 4.5, True), _level("F2", 2, 6.0, False)])
    got = _accumulate_manual_elevations(norm, {("main", "F2"): {"height_m": 6.0}})
    assert got[("main", "F1")]["elevation_estimated"] is False


@pytest.mark.unit
def test_default_height_below_makes_the_floor_above_estimated():
    """**核心用例**:F1 层高是默认值 ⇒ F2 的标高是估出来的。

    实测正是这一例:人录了 F2/F3 的层高却没录 F1 的,
    而 F2 标高 = F1 标高 + F1 层高。
    """
    norm = _Norm([_level("F1", 1, 4.5, True),      # 默认层高
                  _level("F2", 2, 6.0, False)])
    got = _accumulate_manual_elevations(norm, {("main", "F2"): {"height_m": 6.0}})
    assert got[("main", "F2")]["elevation_estimated"] is True


@pytest.mark.unit
def test_all_measured_heights_keep_the_elevation_unestimated():
    norm = _Norm([_level("F1", 1, 5.0, False), _level("F2", 2, 6.0, False)])
    got = _accumulate_manual_elevations(
        norm, {("main", "F1"): {"height_m": 5.0},
               ("main", "F2"): {"height_m": 6.0}})
    assert got[("main", "F2")]["elevation_estimated"] is False


@pytest.mark.unit
def test_estimation_propagates_upward():
    """**一处估算,其上全估** —— 误差是累加进去的,不会自己消失。"""
    norm = _Norm([_level("F1", 1, 4.5, True),
                  _level("F2", 2, 6.0, False),
                  _level("F3", 3, 5.4, False)])
    got = _accumulate_manual_elevations(
        norm, {("main", "F2"): {"height_m": 6.0}, ("main", "F3"): {"height_m": 5.4}})
    assert got[("main", "F2")]["elevation_estimated"] is True
    assert got[("main", "F3")]["elevation_estimated"] is True


@pytest.mark.unit
def test_estimation_propagates_downward_for_basements():
    """向下累加同理:地下层标高 = 上层标高 − 本层层高。"""
    norm = _Norm([_level("B1", -1, 4.5, True),
                  _level("F1", 1, 5.0, False)])
    got = _accumulate_manual_elevations(norm, {("main", "F1"): {"height_m": 5.0}})
    assert got[("main", "B1")]["elevation_estimated"] is True


@pytest.mark.unit
def test_manual_override_height_counts_as_measured():
    """人工录入的层高不是估算 —— 它正是为替换默认值而录的。"""
    norm = _Norm([_level("F1", 1, 4.5, True), _level("F2", 2, 4.5, True)])
    got = _accumulate_manual_elevations(
        norm, {("main", "F1"): {"height_m": 5.2}, ("main", "F2"): {"height_m": 6.0}})
    assert got[("main", "F2")]["elevation_estimated"] is False


@pytest.mark.unit
def test_existing_fields_are_preserved():
    """不能把已有的 source/confidence 冲掉。"""
    norm = _Norm([_level("F1", 1, 4.5, False), _level("F2", 2, 6.0, False)])
    got = _accumulate_manual_elevations(
        norm, {("main", "F1"): {"height_m": 4.5, "source": "level_elevation_pairing",
                                "confidence": 0.9}})
    assert got[("main", "F1")]["source"] == "level_elevation_pairing"
    assert got[("main", "F1")]["confidence"] == 0.9


@pytest.mark.unit
def test_unaffected_units_are_untouched():
    """没有 override 的单体不参与累加,不该被写入。"""
    norm = _Norm([_level("F1", 1, 4.5, True)])
    norm.stories_by_building["north"] = [_level("F1", 1, 4.5, True)]
    got = _accumulate_manual_elevations(norm, {("main", "F1"): {"height_m": 5.0}})
    assert ("north", "F1") not in got


# ── 实测标高不得被累加覆盖（v40 实测的严重缺陷）────────────────

@pytest.mark.unit
def test_measured_elevation_is_not_overwritten_by_accumulation():
    """**核心缺陷**:`_accumulate_manual_elevations` 把实测标高覆盖成累加值。

    实测(v40,north 单体):

    | 层 | pairing 读出(图纸) | 累加后(落库) | 差 |
    |---|---:|---:|---:|
    | RF | **25.00** | 22.50 | **2.50 米** |
    | F5 | **16.50** | 18.00 | 1.50 米 |
    | B2 | **−9.30** | −8.40 | 0.90 米 |

    而 `source` 仍是 `level_elevation_pairing` —— **标签说图纸读的,
    值却是 4.5 默认层高推的**。这比 `elevation_estimated` 标错严重得多:
    数据本身被换掉了。

    这正是「P2 覆盖 0→10 层」在模型里看不到效果的原因。
    """
    norm = _Norm([_level("F1", 1, 4.5, True), _level("F2", 2, 4.5, True),
                  _level("F3", 3, 4.5, True)])
    got = _accumulate_manual_elevations(norm, {
        ("main", "F2"): {"elevation_bottom_m": 3.05,
                         "source": "level_elevation_pairing"},
        ("main", "F3"): {"elevation_bottom_m": 9.35,
                         "source": "level_elevation_pairing"},
    })
    assert got[("main", "F2")]["elevation_bottom_m"] == pytest.approx(3.05)
    assert got[("main", "F3")]["elevation_bottom_m"] == pytest.approx(9.35)


@pytest.mark.unit
def test_measured_elevations_are_not_estimated():
    norm = _Norm([_level("F1", 1, 4.5, True), _level("F2", 2, 4.5, True)])
    got = _accumulate_manual_elevations(norm, {
        ("main", "F2"): {"elevation_bottom_m": 3.05,
                         "source": "level_elevation_pairing"},
    })
    assert got[("main", "F2")]["elevation_estimated"] is False


@pytest.mark.unit
def test_unknown_floors_accumulate_from_the_nearest_measured_floor():
    """**锚定实测层**:未知层从最近的已知层累加,而不是一路从 ±0.000 推。

    实测 north 的 F5=16.500 是图纸值,那 F6 应当从 16.500 起算,
    而不是从 0 按 order 推出 22.5。
    """
    norm = _Norm([_level("F1", 1, 4.5, True), _level("F2", 2, 4.5, True),
                  _level("F3", 3, 5.0, False)])
    got = _accumulate_manual_elevations(norm, {
        ("main", "F2"): {"elevation_bottom_m": 3.05,
                         "source": "level_elevation_pairing"},
        ("main", "F3"): {"height_m": 5.0},
    })
    # F3 未给实测标高 ⇒ 由 F2 的实测 3.05 + F2 层高累加，而非 0+4.5×2
    assert got[("main", "F3")]["elevation_bottom_m"] > 3.05
    assert got[("main", "F3")]["elevation_estimated"] is True


@pytest.mark.unit
def test_manual_height_still_takes_effect_between_measured_floors():
    """本函数的**原意**要保住:人工层高要真正抬升上层。"""
    norm = _Norm([_level("F1", 1, 4.5, True), _level("F2", 2, 4.5, True)])
    got = _accumulate_manual_elevations(
        norm, {("main", "F1"): {"height_m": 6.0}})
    assert got[("main", "F2")]["elevation_bottom_m"] == pytest.approx(6.0)
