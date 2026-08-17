"""构件包络必须**稳健** —— 极值会被离群构件撑爆(J1 任务 3)。

**实测**(v49,轴网入模只有 4/14 层):

| 层 | 轴网跨度 | 构件跨度 | 判定 |
|---|---|---|---|
| F2 | x**3** | x**6552** | 「轴网 x 跨度过小」 |
| F4 | x146 | x**6454** | 同上 |
| F1 | x95 | x**2358** | 同上 |

大歌剧院再大也就 **300 米**量级。6552 米的包络是被**离群构件**撑爆的
(机电管线比例错误可以把管子甩到几千米外)。

于是 `axes_plausible` 拿一个假的基准去量轴网,把好轴网判成
「跨度过小、疑为局部详图轴网」—— **表象是轴网太小,根因是基准太大**。

`min/max` 对离群点没有任何抵抗力。改用分位数:去掉两端极值,
保留主体范围。
"""
from __future__ import annotations

import pytest

from services.axes_validation import elements_bounds


def _cols(points: list[tuple[float, float]]) -> dict:
    return {"columns": [{"outline": [list(p)]} for p in points]}


@pytest.mark.unit
def test_single_outlier_does_not_blow_up_the_envelope():
    """**核心用例**:一根跑到 6000 米外的管子不该定义整层范围。"""
    main = [(x * 1.0, 0.0) for x in range(100)]      # 主体 0~99 米
    bounds = elements_bounds(_cols(main + [(6552.0, 0.0)]))
    assert bounds is not None
    assert bounds[1] < 200, f"包络被离群点撑爆:{bounds}"


@pytest.mark.unit
def test_normal_extent_is_preserved():
    """没有离群时范围要保住 —— 不能把正常构件也裁掉。"""
    main = [(x * 1.0, x * 0.5) for x in range(100)]
    bounds = elements_bounds(_cols(main))
    assert bounds[0] <= 5 and bounds[1] >= 94


@pytest.mark.unit
def test_outliers_on_both_ends_are_trimmed():
    main = [(x * 1.0, 0.0) for x in range(100)]
    bounds = elements_bounds(_cols([(-9000.0, 0.0)] + main + [(9000.0, 0.0)]))
    assert -200 < bounds[0] and bounds[1] < 200


@pytest.mark.unit
def test_few_points_are_not_trimmed():
    """点太少时分位数没有意义 —— 全保留,否则会把仅有的构件裁没。"""
    bounds = elements_bounds(_cols([(0.0, 0.0), (50.0, 30.0)]))
    assert bounds == (0.0, 50.0, 0.0, 30.0)


@pytest.mark.unit
def test_paths_are_included_too():
    """墙/梁/管线走 `path` 字段,同样要计入。"""
    els = {"walls": [{"path": [[0.0, 0.0], [40.0, 20.0]]}]}
    assert elements_bounds(els) == (0.0, 40.0, 0.0, 20.0)


@pytest.mark.unit
def test_empty_is_none():
    assert elements_bounds({}) is None
    assert elements_bounds({"columns": []}) is None


# ── 包络只算结构主体(实测:管线跑到 6300 米外)──────────────────

@pytest.mark.unit
def test_pipes_do_not_define_the_envelope():
    """**核心用例**:机电管线不参与构件包络。

    实测 F2 层:

    | 类型 | x 范围 |
    |---|---|
    | walls | 8 ~ 212 |
    | beams | 44 ~ 211 |
    | **pipes** | **−6309** ~ 111 |

    管线把包络撑到 6513 米,于是 `axes_plausible` 拿假基准量轴网,
    把好轴网判成「跨度过小」。

    **国标依据**:GB/T 50001 §8 定位轴线用于确定**主要承重构件**位置 ——
    校验轴网该与结构主体比,不该与机电管线比。机电图的比例误差也更大。
    """
    els = {"walls": [{"path": [[8.0, 0.0], [212.0, 100.0]]}],
           "pipes": [{"path": [[-6309.0, 0.0], [111.0, 50.0]]}]}
    bounds = elements_bounds(els)
    assert bounds is not None
    assert bounds[0] >= 0, f"管线不该定义下界:{bounds}"
    assert bounds[1] <= 220


@pytest.mark.unit
def test_equipment_does_not_define_the_envelope():
    """设备同理 —— YOLO 检出的设备位置由楼层包络反推,不能反过来定义包络。"""
    els = {"columns": [{"outline": [[10.0, 10.0], [200.0, 120.0]]}],
           "equipment": [{"outline": [[-5000.0, -5000.0]]}]}
    bounds = elements_bounds(els)
    assert bounds[0] >= 0


@pytest.mark.unit
def test_structural_kinds_are_all_counted():
    """柱/墙/梁/板都算 —— 它们才是轴线定位的对象。"""
    els = {"columns": [{"outline": [[0.0, 0.0]]}],
           "slabs": [{"outline": [[100.0, 80.0]]}],
           "walls": [{"path": [[50.0, 40.0]]}],
           "beams": [{"path": [[20.0, 10.0]]}]}
    assert elements_bounds(els) == (0.0, 100.0, 0.0, 80.0)
