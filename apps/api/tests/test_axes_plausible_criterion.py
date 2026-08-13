"""轴网自洽性该按**中心错位**判,不是「边界不得超出」(J1 任务 3)。

**原始缺陷**(本判据当初要挡的):B1 层轴网 x[−76.7, 960.1]、
构件 x[−4.4, 123.5] —— 偏离 **700+ 米**。那是**中心错位**。

**但「边界不得超出构件包络」这个形式是错的**:

| 层 | 轴网跨度 | 构件跨度 | 旧判定 |
|---|---:|---:|---|
| RF | x203 | x100 | ❌ 远离构件 |
| F6 | x223 | x98 | ❌ 远离构件 |
| B2 | x140 | x95 | ❌ 远离构件 |

轴网**本来就该比构件大**:

1. §8.0.2 轴号圈画在**轴线端部**,在建筑轮廓之外;
2. 构件识别每层只取 2 张图,包络**必然不完整**。

先前管线把包络撑到 6500 米,**意外地**让这个判据变宽松;
一旦按 §8 只算结构主体,判据的缺陷就暴露了 —— 通过率从 5 层跌到 3 层。

**改判据**:比中心距离与尺度比,不比边界包含。
"""
from __future__ import annotations

import pytest

from services.axes_validation import axes_plausible


def _axes(x0: float, x1: float, y0: float, y1: float) -> dict:
    return {"x": [{"label": "1", "coord": x0}, {"label": "9", "coord": x1}],
            "y": [{"label": "A", "coord": y0}, {"label": "H", "coord": y1}]}


def _els(x0: float, x1: float, y0: float, y1: float) -> dict:
    return {"columns": [{"outline": [[x0, y0], [x1, y1]]}]}


@pytest.mark.unit
def test_the_original_700m_misplacement_is_still_rejected():
    """**回归锚**:当初促成本判据的那个案例必须仍被挡住。"""
    ok, _ = axes_plausible(_axes(-76.7, 960.1, 720.6, 1306.6),
                           _els(-4.4, 123.5, -35.1, 144.0))
    assert not ok


@pytest.mark.unit
@pytest.mark.parametrize("ax_span,el_span", [(203, 100), (223, 98), (140, 95)])
def test_axes_wider_than_elements_is_normal(ax_span, el_span):
    """**轴网比构件大是常态** —— 轴线延伸出轮廓 + 构件识别不完整。

    实测 RF/F6/B2 三层正因此被误判,而它们的轴网中心与构件中心是重合的。
    """
    half_a, half_e = ax_span / 2, el_span / 2
    ok, reason = axes_plausible(_axes(-half_a, half_a, -half_a, half_a),
                                _els(-half_e, half_e, -half_e, half_e))
    assert ok, reason


@pytest.mark.unit
def test_centre_offset_beyond_the_element_span_is_rejected():
    """中心错开超过构件跨度 ⇒ 坐标系不一致。"""
    ok, _ = axes_plausible(_axes(300, 400, 300, 400), _els(0, 100, 0, 100))
    assert not ok


@pytest.mark.unit
def test_absurd_scale_ratio_is_rejected():
    """轴网比构件大一个数量级 ⇒ 比例错了。"""
    ok, _ = axes_plausible(_axes(-500, 500, -500, 500), _els(-50, 50, -50, 50))
    assert not ok


@pytest.mark.unit
def test_tiny_axes_span_is_rejected():
    """轴网只覆盖构件的极小一块 ⇒ 局部详图轴网,代表不了整层。"""
    ok, _ = axes_plausible(_axes(0, 3, 0, 1), _els(0, 216, 0, 106))
    assert not ok


@pytest.mark.unit
def test_single_direction_is_rejected():
    """交点要两个方向 —— 单向轴网定不出位置。"""
    ok, _ = axes_plausible({"x": [{"label": "1", "coord": 0.0}], "y": []},
                           _els(0, 100, 0, 100))
    assert not ok


@pytest.mark.unit
def test_no_elements_is_rejected():
    ok, _ = axes_plausible(_axes(0, 100, 0, 100), {})
    assert not ok
