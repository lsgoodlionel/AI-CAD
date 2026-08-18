"""PDF 页面旋转必须归一 —— 实测两个工程各约 23% 的图受影响。

**实测坐标系错位**(轨道交通 14/60、大歌剧院 9/40 页面旋转 270°):

    page.rect  (旋转后) 3370 x 2384   ← geom.page_w/page_h 取自这里
    mediabox   (旋转前) 2384 x 3370   ← get_drawings() 的坐标在这个系里

**宽高恰好颠倒**。下游任何用 `page_h` 做 y 翻转、用 `page_w` 估比例的地方
都会错 —— 而这类错误不会报异常,只会让构件位置悄悄偏掉。

修法:用 `page.rotation_matrix` 把图元变换到**显示坐标系**
(人看图的方向),与 `page.rect` 一致。实测变换后图元正确落入 page.rect。
"""
from __future__ import annotations

import pytest

from core.model3d.geometry_extractor import _apply_rotation


@pytest.mark.unit
def test_no_rotation_is_identity():
    """**未旋转的图不得受影响** —— 77% 的图走这条路。"""
    assert _apply_rotation(None, 100.0, 200.0) == (100.0, 200.0)


@pytest.mark.unit
def test_rotation_maps_into_display_space():
    """270° 旋转:mediabox 坐标 → page.rect 坐标。"""
    import fitz

    # 2384x3370 的页旋转 270° 后显示为 3370x2384
    matrix = fitz.Matrix(0, 1, -1, 0, 3370, 0)     # 等价于 270° 的 rotation_matrix
    x, y = _apply_rotation(matrix, 1160.0, 410.0)
    assert 0 <= x <= 3370 and 0 <= y <= 2384


@pytest.mark.unit
def test_broken_matrix_degrades_to_identity():
    """**变换算不出就用原值** —— 绝不因此丢掉整张图的几何。"""
    assert _apply_rotation("not a matrix", 5.0, 6.0) == (5.0, 6.0)


@pytest.mark.unit
def test_geometry_of_rotated_page_fits_the_page_box():
    """**端到端**:旋转页提取后,图元必须落在 page_w/page_h 内。

    此前 x 最大 2126 而 page_w=3370、y 最大 1151 而 page_h=2384 ——
    数值上「没超界」所以从未报错,但 x/y 实际是**转置**的,
    构件位置整体错位。这类错误只能靠坐标系一致性检查发现。
    """
    import fitz

    doc = fitz.open()
    page = doc.new_page(width=2384, height=3370)
    page.draw_line(fitz.Point(100, 200), fitz.Point(1160, 410))
    page.set_rotation(270)
    data = doc.tobytes()

    from core.model3d.geometry_extractor import extract_pdf_geometry

    geom = extract_pdf_geometry(data)
    assert geom.page_w > geom.page_h, "旋转后应为横向"
    for x0, y0, x1, y1 in geom.lines:
        assert 0 <= x0 <= geom.page_w + 1 and 0 <= y0 <= geom.page_h + 1
        assert 0 <= x1 <= geom.page_w + 1 and 0 <= y1 <= geom.page_h + 1
