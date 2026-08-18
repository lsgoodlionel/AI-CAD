"""包络必须**按坐标系分组**求 —— 两个坐标系混算等于没有基准(J7)。

**实测**(v50,33 张产出结构构件的图):

| 坐标系归属 | 张数 | 占比 | 锚点 |
|---|---:|---:|---|
| 局部坐标系 | 25 | 75.8% | 全部**无**锚点 |
| 世界坐标系 | 5 | 15.2% | 全部**有**锚点 |
| 同图内混 | 2 | 6.1% | 无锚点 |
| 越界/异常 | 1 | 3.0% | 无锚点 |

**「有锚点 ⇔ 世界坐标」100% 对应** —— 规则是干净的,不是随机混乱。
问题在于 scene 把两类**混在一起**求包络:局部在 0~300、世界在 −6300 附近,
合起来跨 6000 米,于是

- `_clip_elements_to_envelope` 裁不掉任何东西(包络太大);
- `axes_plausible` 拿一个假基准去量轴网。

**修法**:比较必须在**同一坐标系内**。轴网在哪个坐标系,就拿同坐标系的
构件求包络;不改任何图的坐标(那属于 J7 的架构改造,需另行设计)。
"""
from __future__ import annotations

import pytest

from services.axes_validation import (
    SYSTEM_LOCAL, SYSTEM_WORLD, coordinate_system_of, elements_bounds,
    world_range_from_anchors,
)

# 大歌剧院的区间现在**从锚点推导**而非写死(通用性审计:旧常量假定
# 「工程坐标为负几千米」,正值城市坐标系的工程会全判成局部)。
_RANGE = world_range_from_anchors([-6326.0, -6065.0])


def _cols(xs: list[float]) -> dict:
    return {"columns": [{"outline": [[x, 0.0]]} for x in xs]}


@pytest.mark.unit
@pytest.mark.parametrize("value,expected", [
    (-6213.0, SYSTEM_WORLD), (-6065.0, SYSTEM_WORLD),
    (0.0, SYSTEM_LOCAL), (109.0, SYSTEM_LOCAL), (-2.0, SYSTEM_LOCAL),
])
def test_coordinate_system_is_recognised(value, expected):
    """区间从本项目锚点推导:世界锚点在 −6326~−6065,局部在 0~300。"""
    assert coordinate_system_of(value, world_range=_RANGE) == expected


@pytest.mark.unit
def test_bounds_of_one_system_ignore_the_other():
    """**核心用例**:求局部包络时,世界坐标的点不参与。

    实测一层里 25 张局部 + 5 张世界,混算得到 6000 米跨度的假基准。
    """
    els = _cols([0.0, 50.0, 100.0, -6200.0, -6100.0])
    lo, hi, _, _ = elements_bounds(els, system=SYSTEM_LOCAL, world_range=_RANGE)
    assert lo >= -10 and hi <= 110, f"局部包络被世界坐标污染:{lo}~{hi}"


@pytest.mark.unit
def test_world_system_bounds_exclude_local():
    els = _cols([0.0, 50.0, -6200.0, -6100.0, -6150.0])
    lo, hi, _, _ = elements_bounds(els, system=SYSTEM_WORLD, world_range=_RANGE)
    assert lo <= -6000 and hi <= -6000


@pytest.mark.unit
def test_without_system_keeps_the_old_behaviour():
    """不指定坐标系时行为不变 —— 老调用方不受影响。"""
    els = _cols([0.0, 100.0])
    assert elements_bounds(els) == (0.0, 100.0, 0.0, 0.0)


@pytest.mark.unit
def test_system_with_no_points_returns_none():
    """该坐标系里没有构件 ⇒ 无基准可比,返回 None(判不出就说判不出)。"""
    els = _cols([0.0, 50.0, 100.0])
    assert elements_bounds(els, system=SYSTEM_WORLD, world_range=_RANGE) is None


@pytest.mark.unit
def test_axes_are_compared_within_their_own_system():
    """**轴网在哪个坐标系,就拿同坐标系的构件比**。

    局部轴网 + 混合构件:此前拿全体构件(跨 6000 米)当基准,
    轴网跨度只占 1.6% ⇒ 被误判「局部详图轴网」。
    """
    from services.axes_validation import axes_plausible

    axes = {"x": [{"label": "1", "coord": 0.0}, {"label": "9", "coord": 100.0}],
            "y": [{"label": "A", "coord": 0.0}, {"label": "H", "coord": 80.0}]}
    els = {"columns": [{"outline": [[0.0, 0.0], [90.0, 70.0]]},
                       # 世界坐标系的构件 —— 不该参与局部轴网的校验
                       {"outline": [[-6200.0, -6200.0], [-6100.0, -6100.0]]}]}
    ok, reason = axes_plausible(axes, els, world_range=_RANGE)
    assert ok, reason
