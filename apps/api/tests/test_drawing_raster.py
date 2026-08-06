"""图纸栅格化单测(标注统一走位图)。"""
from services.drawing_raster import (
    MAX_RASTER_PX, RASTER_DPI, effective_dpi, ensure_pdf_raster, raster_key,
    render_pdf_png,
)


def test_raster_key_is_stable_and_separate_from_cad_assets():
    key = raster_key("p1", "d1")
    assert key == "projects/p1/raster/d1.png"
    assert "model_assets" not in key      # 与 CAD 预览资产分开,避免互相覆盖


def test_effective_dpi_keeps_default_for_normal_pages():
    assert effective_dpi(842, 595) == RASTER_DPI       # A4 横


def test_effective_dpi_keeps_full_resolution_for_a0():
    """A0(3370pt)在 150dpi 下 7020px,低于上限——不该被降,否则图框小字看不清。"""
    assert effective_dpi(3370, 2384) == RASTER_DPI


def test_effective_dpi_scales_down_only_truly_oversized_sheets():
    # 实测存在 5084×2412pt 的加长图,150dpi 要到 10592px:仍在上限内
    assert effective_dpi(5084, 2412) == RASTER_DPI
    # 更夸张的图才降,且降完不超上限
    dpi = effective_dpi(20000, 5000)
    assert dpi < RASTER_DPI
    assert 20000 * dpi / 72.0 <= MAX_RASTER_PX + 1


def test_effective_dpi_never_returns_unusable_value():
    assert effective_dpi(999999, 999999) >= 36
    assert effective_dpi(0, 0) == RASTER_DPI


def test_render_pdf_png_degrades_on_bad_input():
    assert render_pdf_png(b"not a pdf") is None


def test_render_pdf_png_preserves_aspect_ratio():
    """等比是硬要求:一旦拉伸,前端「同除显示高度」的归一化坐标就对不上 page_h。"""
    import fitz

    doc = fitz.open()
    doc.new_page(width=800, height=400)
    png = render_pdf_png(bytes(doc.tobytes()), dpi=72)
    doc.close()
    assert png is not None

    import io

    from PIL import Image
    with Image.open(io.BytesIO(png)) as img:
        assert abs(img.width / img.height - 800 / 400) < 0.01


def test_ensure_pdf_raster_returns_existing_key_without_rerender(monkeypatch):
    import services.drawing_raster as mod

    calls: list[str] = []
    monkeypatch.setattr("core.storage.object_exists", lambda k: True)
    monkeypatch.setattr("core.storage.get_file_bytes",
                        lambda k: calls.append("read") or b"")
    assert ensure_pdf_raster("p1", "d1", "a.pdf") == raster_key("p1", "d1")
    assert calls == []                    # 幂等:已存在就不重渲
    assert mod.RASTER_DPI == RASTER_DPI


def test_ensure_pdf_raster_returns_none_when_render_fails(monkeypatch):
    monkeypatch.setattr("core.storage.object_exists", lambda k: False)
    monkeypatch.setattr("core.storage.get_file_bytes", lambda k: b"not a pdf")
    assert ensure_pdf_raster("p1", "d1", "a.pdf") is None


# ── CAD 等比栅格 ────────────────────────────────────────────────

def test_cad_figsize_preserves_aspect_for_wide_and_tall():
    from services.drawing_raster import CAD_LONG_EDGE_IN, cad_figsize

    w, h = cad_figsize(2000, 1000)
    assert w == CAD_LONG_EDGE_IN and abs(w / h - 2.0) < 1e-6
    w, h = cad_figsize(1000, 2000)
    assert h == CAD_LONG_EDGE_IN and abs(h / w - 2.0) < 1e-6


def test_cad_figsize_falls_back_on_degenerate_extent():
    from services.drawing_raster import cad_figsize

    assert cad_figsize(0, 0)[0] > 0
    assert cad_figsize(-1, 5)[1] > 0


def test_render_dxf_png_degrades_on_bad_input():
    from services.drawing_raster import render_dxf_png

    assert render_dxf_png(b"not a dxf") is None
