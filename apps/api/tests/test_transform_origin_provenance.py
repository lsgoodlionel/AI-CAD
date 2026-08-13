"""变换原点要能区分「原点在 0」与「没找到原点」(J7 后续)。

**实测缺陷**:`_min_labeled_pos` 在**没有轴线**时返回 `0.0` ——
而 0 是个合法坐标值,下游无从分辨。于是该方向的构件坐标变成
`pt × scale`(从图幅边缘算起)而不是 `(pt − origin) × scale`。

规模:1436 条变换里 **origin_x=0 有 72 张、origin_y=0 有 77 张**
(合计 10.4%),而**「两个方向都为 0」是 0 张** ——
说明不是「完全找不到原点」,而是恰好**缺一个方向**,
与「轴网非双向」的现象吻合。

个案:`S-0-20-102.04C` 变换正常(1:150、置信 1.00)但 `origin_x=0.0`
(正常图如 A-01-02A 是 992.1),其墙的 x 坐标落在 149~2356 米,
把 F1 层的构件包络撑到 2207 米。

**这与 `drawing_transform` 的 1:335 万教训同源**:一个「看起来合法」的值
比缺失更危险 —— 缺失会让下游降级,而假值会一路通行。

**本轮只做标记不做拒绝**:拒绝会让 149 张(10.4%)失去定位,
影响面大且要重跑才知道后果;标记是纯增量,下游可逐步利用
(「降级必须可见」,见 MODELING_PIPELINE_BLUEPRINT §7 约束 3)。
"""
from __future__ import annotations

import pytest

from core.model3d.element_recognizer import _min_labeled_pos, _origin_pt


@pytest.mark.unit
def test_no_axes_yields_none_not_zero():
    """**核心用例**:没有轴线时返回 None,不是 0.0。

    0 是合法坐标 —— 返回它等于对下游说「原点就在 0」。
    """
    assert _min_labeled_pos([]) is None


@pytest.mark.unit
def test_labelled_axis_wins():
    """有轴号时取**轴号最小者**的位置(§8.0.3 依次注写)。"""
    assert _min_labeled_pos([("2", 200.0), ("1", 500.0)]) == 500.0


@pytest.mark.unit
def test_unlabelled_axes_fall_back_to_min_position():
    assert _min_labeled_pos([("", 300.0), ("", 120.0)]) == 120.0


@pytest.mark.unit
def test_origin_zero_is_distinguishable_from_missing():
    """轴线**确实在 0** 与**没有轴线**必须能分开。"""
    assert _min_labeled_pos([("1", 0.0)]) == 0.0
    assert _min_labeled_pos([]) is None


@pytest.mark.unit
def test_origin_pt_reports_missing_directions():
    """`_origin_pt` 逐方向返回,缺哪个方向就是哪个为 None。"""
    ox, oy = _origin_pt([("1", 100.0)], [], page_h=2384.0)
    assert ox == 100.0
    assert oy is None

    ox2, oy2 = _origin_pt([], [("A", 200.0)], page_h=2384.0)
    assert ox2 is None
    assert oy2 is not None


@pytest.mark.unit
def test_both_directions_present():
    ox, oy = _origin_pt([("1", 100.0)], [("A", 200.0)], page_h=2384.0)
    assert ox is not None and oy is not None
