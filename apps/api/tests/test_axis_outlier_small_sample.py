"""小样本下「比例阈值」过于敏感 —— 5 条轴线里 1 条逆序不该作废整个方向。

**实测**(大歌剧院 B1,2681 根柱全楼最多,追查 14 层才定位):

    轴网聚合 B1：4/12 张 → x=32 y=19
    轴号同名冲突 32 条（图纸变换不一致）      ← 去重后剩 14+5 条
    x 向轴号离群 5/14（36% ≥ 20%）—— 不输出
    y 向轴号离群 1/5 （20% ≥ 20%）—— 不输出   ← **恰好触线**
    → 两方向皆空 → B1 轴 0

x 向 36% 作废是对的(那确实是两套轴网交织)。
**y 向 5 条里 1 条就作废则过苛** —— 单个离群点更像识别噪声,
而「两套轴网交织」必然产生**多个**逆序。

判据补一条**绝对数下限**:逆序 ≥2 条才谈得上「交织」。
这不是放宽比例,是给「比例」加一个样本量前提。
"""
from __future__ import annotations

import pytest

from services.model_elements import should_drop_direction


@pytest.mark.unit
def test_single_outlier_in_small_sample_is_kept():
    """**核心用例**:5 条轴线 1 条逆序 —— 保留(单点噪声)。"""
    assert not should_drop_direction(outliers=1, total=5)


@pytest.mark.unit
def test_two_outliers_in_small_sample_are_dropped():
    """2 条逆序就够「交织」了 —— 照旧作废。"""
    assert should_drop_direction(outliers=2, total=5)


@pytest.mark.unit
def test_large_sample_still_uses_the_ratio():
    """**大样本不受影响** —— 14 条里 5 条(36%)照旧作废。"""
    assert should_drop_direction(outliers=5, total=14)
    assert not should_drop_direction(outliers=2, total=14)   # 14% < 20%


@pytest.mark.unit
def test_no_axes_or_no_outliers():
    """空集与零逆序都不作废。"""
    assert not should_drop_direction(outliers=0, total=0)
    assert not should_drop_direction(outliers=0, total=10)


@pytest.mark.unit
def test_all_outliers_always_drops():
    """**全逆序必废** —— 哪怕只有 2 条。"""
    assert should_drop_direction(outliers=2, total=2)
