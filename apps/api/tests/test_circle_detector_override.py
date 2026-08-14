"""圆检测要与识别器**同口径** —— 否则同一张图里两套坐标（J7 收尾）。

**实测**（模型 v53，按 (楼层, 来源) 统计 35 个组合）：

| 坐标系 | 数量 | 占比 |
|---|---:|---:|
| 局部 | 29 | 82.9% |
| 世界 | 5 | 14.3% |
| **同来源内混** | **1** | 2.9% |

真实图纸的跨度已全部 ≤225 米，唯一异常是 `RF` 层的 `columns-envelope`
**跨 6362 米** —— 而它是**单张图内**的柱包络。单图内混坐标系只有一种可能：

| 同一张图里 | 比例/原点从哪来 |
|---|---|
| 几何识别的柱 | `resolve_scale` + `origin_override`（已修正） |
| **圆检测的桩** | `detect_pile_columns` 自己调 `_detect_scale`/`_origin_pt`（**未修正**） |

于是两套坐标混进同一个 `columns` 列表，柱包络自然横跨两个坐标系。

这是同一个缺陷的**第四条路径**：比例/原点的兜底此前补到了
`transform_from_geometry`、`_transform_of`、`_recognize`，唯独漏了圆检测。
"""
from __future__ import annotations

import pytest

from core.model3d.circle_detector import resolve_detection_frame


@pytest.mark.unit
def test_implausible_scale_is_replaced_by_the_override():
    """**核心用例**：圆检测自己算出的离谱比例要被落库比例顶替。"""
    scale, _origin = resolve_detection_frame(
        1.489326, (None, 175.9), scale_override=0.052917,
        origin_override=(595.29, None), page_w_pt=3370.0)
    assert scale == pytest.approx(0.052917)


@pytest.mark.unit
def test_missing_origin_direction_takes_the_override():
    _scale, origin = resolve_detection_frame(
        0.0529, (None, 175.9), scale_override=None,
        origin_override=(595.29, 706.47), page_w_pt=3370.0)
    assert origin[0] == pytest.approx(595.29)
    assert origin[1] == pytest.approx(175.9), "自己有值的方向不该被覆盖"


@pytest.mark.unit
def test_same_frame_as_the_recogniser():
    """**同口径**才是目的：同样的输入，两边算出同一个比例。

    否则同一张图的柱与桩落在不同坐标系，柱包络会横跨两者（实测 6362 米）。
    """
    from core.model3d.element_recognizer import resolve_scale

    scale, _ = resolve_detection_frame(
        1.489326, (None, 0.0), scale_override=0.052917,
        origin_override=(0.0, None), page_w_pt=3370.0)
    assert scale == pytest.approx(
        resolve_scale(1.489326, 0.052917, page_w_pt=3370.0))


@pytest.mark.unit
def test_without_overrides_behaviour_is_unchanged():
    """没有 override 时保持原状 —— 老路径零回归。"""
    scale, origin = resolve_detection_frame(0.0529, (100.0, 200.0))
    assert scale == pytest.approx(0.0529)
    assert origin == (100.0, 200.0)


@pytest.mark.unit
def test_origin_stays_none_when_nothing_can_fill_it():
    """补不上就仍是 None —— 由 `circle_px_to_meter` 按 0 兜底并保持可见。"""
    _scale, origin = resolve_detection_frame(0.0529, (None, 200.0))
    assert origin[0] is None
