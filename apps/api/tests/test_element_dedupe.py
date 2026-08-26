"""构件去重——重复构件会**直接虚增算量**。

`services/model_qto.py` 是 `for column in columns` 逐个累加体积的，
同一根柱出现 N 次，混凝土量、模板面积就乘 N，而算量喂给创效提案。

**实测重复率**（去重前后对比整机 scene）：

| 类别 | 大歌剧院 | 轨道交通 |
|---|---|---|
| 柱 | 44% | 32% |
| 设备 | 38% | 43% |
| 板 | 19% | 14% |

板不在去重范围内：`_slab_from_columns` 造的包络板本就套着真实板，
那是合理嵌套，没有证据说它是重复。
"""
import pytest

from core.model3d.dedupe import merge_overlapping


def _rect(x0, y0, x1, y1, **extra):
    return {"outline": [[x0, y0], [x1, y0], [x1, y1], [x0, y1]], **extra}


@pytest.mark.unit
def test_a_column_drawn_twice_counts_once():
    """同一根柱的两个框合成一个。"""
    outer = _rect(0.0, 0.0, 0.6, 0.6)
    inner = _rect(0.05, 0.05, 0.55, 0.55)
    assert len(merge_overlapping([outer, inner])) == 1


@pytest.mark.unit
def test_the_survivor_keeps_a_real_outline_not_a_synthesised_box():
    """保留面积最大的那个**真实轮廓**，而不是合成一个包围盒。

    算量吃的是轮廓：合成矩形会把八边形柱的面积抬高约 27%。
    """
    octagon = {"outline": [[0.1, 0.0], [0.5, 0.0], [0.6, 0.1], [0.6, 0.5],
                           [0.5, 0.6], [0.1, 0.6], [0.0, 0.5], [0.0, 0.1]]}
    sliver = _rect(0.2, 0.2, 0.4, 0.4)
    kept = merge_overlapping([octagon, sliver])
    assert len(kept) == 1
    assert len(kept[0]["outline"]) == 8


@pytest.mark.unit
def test_two_real_columns_side_by_side_both_survive():
    """相邻的两根真柱不能被合并掉。"""
    assert len(merge_overlapping([_rect(0.0, 0.0, 0.6, 0.6),
                                  _rect(0.7, 0.0, 1.3, 0.6)])) == 2


@pytest.mark.unit
def test_elements_without_a_usable_outline_pass_through():
    """轮廓残缺的构件原样保留——去重不该顺手丢数据。"""
    junk = {"outline": [[1.0, 1.0]]}
    assert len(merge_overlapping([junk, _rect(0.0, 0.0, 0.6, 0.6)])) == 2


@pytest.mark.unit
def test_merging_is_stable_regardless_of_input_order():
    """结果不依赖输入顺序（同类缺陷此前在轴网聚合上出现过）。"""
    a, b, c = _rect(0, 0, .6, .6), _rect(.05, .05, .55, .55), _rect(2, 2, 2.6, 2.6)
    assert len(merge_overlapping([a, b, c])) == len(merge_overlapping([c, b, a])) == 2
