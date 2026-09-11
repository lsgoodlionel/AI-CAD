"""训练/检测的渲染 DPI 按图纸**自己的比例**定，而不是全库一个常数。

**为什么**：`export_yolo_dataset.py` 用 `DPI = 100` 渲染全库。算一下
一根 0.6m 见方的柱在图上有多大：

    比例      图上尺寸    DPI=100    DPI=150    DPI=200
    1:50      12.00mm      47.2px     70.9px     94.5px
    1:100      6.00mm      23.6px     35.4px     47.2px
    1:150      4.00mm     **15.7px**  23.6px     31.5px
    1:200      3.00mm      11.8px     17.7px     23.6px
    1:300      2.00mm       7.9px     11.8px     15.7px

YOLO 可靠检测下限约 16~20px。**而歌剧院的主力比例正是 1:150**
（实测 1068 张，全库最多）—— DPI=100 下它的柱只有 15.7px，压在下限上。
`DPI = 100` 这个常数是按 1:100 定的，对主力比例不够。

这正是 CLAUDE.md 记的第一条教训：**渲染分辨率要匹配问题所在的尺度**。
比例批就栽在同一件事上（1872×1324 缩到 420px，门只剩 7px）。

**推导而不是拍脑袋**：要让 0.6m 的柱至少 `target_px` 像素，

    (600 / denom) / 25.4 × dpi ≥ target_px
    ⇒ dpi ≥ target_px × 25.4 / 600 × denom ≈ 1.016 × denom   （target=24）

即 **DPI 约等于比例分母**。上限由像素预算兜住（与 `circle_detector`
的 `render_scale_for` 同一条纪律：超预算降采样，降级可见）。
"""
from __future__ import annotations

import pytest

from core.model3d.render_budget import (
    MAX_RENDER_MEGAPIXELS,
    dpi_for_scale,
    element_px_at,
)


@pytest.mark.unit
@pytest.mark.parametrize("denom,expect_min", [(50, 20), (100, 24), (150, 24), (200, 24)])
def test_目标构件尺寸达到可检测下限(denom, expect_min):
    """按算出的 DPI 渲染，0.6m 的柱必须达到目标像素数。"""
    dpi = dpi_for_scale(denom)
    px = element_px_at(0.6, denom, dpi)
    assert px >= expect_min, f"1:{denom} 下柱只有 {px:.1f}px，低于 {expect_min}"


@pytest.mark.unit
def test_主力比例比旧常数有实质提升():
    """1:150 是全库最多的比例（1068 张），旧常数在它上面压在下限。"""
    old_px = element_px_at(0.6, 150, 100)
    new_px = element_px_at(0.6, 150, dpi_for_scale(150))
    assert old_px < 16, f"前提：旧 DPI 下 1:150 的柱只有 {old_px:.1f}px"
    assert new_px >= 24, f"新 DPI 下应达 24px，实得 {new_px:.1f}"


@pytest.mark.unit
def test_大比例图不被无限放大():
    """1:500 需要 DPI 508，但像素预算必须兜住 —— 否则一张 A0 图会爆内存。"""
    dpi = dpi_for_scale(500, page_w_pt=3370.0, page_h_pt=2384.0)  # A0
    px_w = 3370.0 / 72.0 * dpi
    px_h = 2384.0 / 72.0 * dpi
    assert px_w * px_h <= MAX_RENDER_MEGAPIXELS * 1e6 * 1.01, (
        f"A0 图在 DPI {dpi} 下是 {px_w * px_h / 1e6:.1f} MP，超出预算")


@pytest.mark.unit
def test_小比例详图不被降到低于旧常数():
    """1:50 的详图本来就够大，不该因为「按比例定」反而比旧常数还低。"""
    assert dpi_for_scale(50) >= 50, "详图的 DPI 不该低到看不清"


@pytest.mark.unit
@pytest.mark.parametrize("bad", [None, 0, -1, float("nan")])
def test_比例缺失时回落到旧常数而不是崩掉(bad):
    """缺失不得阻断（`MODELING_PIPELINE_BLUEPRINT.md` §7）。

    全库实测只有 69.6% 的图能解析出合法比例分母 —— 剩下三成必须有兜底。
    """
    dpi = dpi_for_scale(bad)
    assert dpi >= 100, f"比例缺失应回落到不低于旧常数 100，实得 {dpi}"


@pytest.mark.unit
def test_降级要可见():
    """被像素预算压低 DPI 时要说得出来，不能静默。"""
    dpi, capped = dpi_for_scale(500, page_w_pt=3370.0, page_h_pt=2384.0,
                                return_capped=True)
    assert capped is True, "A0 + 1:500 必然触预算，要报告出来"
    plain, not_capped = dpi_for_scale(100, page_w_pt=842.0, page_h_pt=595.0,
                                      return_capped=True)
    assert not_capped is False
    assert plain > 0 and dpi > 0


# ── 真的能渲染出来 ──────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.parametrize("dpi", [288, 288.0, 437.3, 152.4])
def test_render_clip_accepts_float_dpi_and_hits_exact_size(dpi):
    """`dpi_for_scale` / `render_dpi_for_crop` 返回的是**浮点** DPI。

    PyMuPDF 的 `get_pixmap(dpi=…)` 只收整数，给浮点直接抛 TypeError ——
    而金标准生成器与 YOLO 导出都把渲染包在宽泛 `except` 里，于是**每一格都
    静默失败**：col3 批扫了 7520 个候选、出 0 格、退出码 0。

    改用缩放矩阵渲染：浮点照收，且尺寸就是算出来的那个，不被取整。
    """
    import fitz

    from core.model3d.render_budget import render_clip

    doc = fitz.open()
    page = doc.new_page(width=842, height=595)
    pix = render_clip(page, fitz.Rect(0, 0, 120, 120), dpi)
    assert abs(pix.width - 120 / 72 * dpi) <= 1, f"宽 {pix.width}，应为 {120 / 72 * dpi:.1f}"
