"""几何原语提取（PDF/DXF → DrawingGeometry）测试"""
import pytest

from core.model3d import DrawingGeometry, extract_pdf_geometry
from core.model3d.geometry_extractor import extract_dxf_geometry


def _make_pdf() -> bytes:
    """程序化构造矢量 PDF：1 条线 + 1 个填充矩形 + 1 段文本"""
    import fitz

    doc = fitz.open()
    page = doc.new_page(width=842, height=595)  # A1 横向缩样
    shape = page.new_shape()
    shape.draw_line(fitz.Point(50, 50), fitz.Point(500, 50))
    shape.finish(color=(0, 0, 0))
    shape.draw_rect(fitz.Rect(100, 100, 130, 130))
    shape.finish(color=(0, 0, 0), fill=(0, 0, 0))
    shape.commit()
    page.insert_text(fitz.Point(60, 40), "1:100")
    data = doc.tobytes()
    doc.close()
    return data


@pytest.mark.unit
def test_pdf_extracts_lines_rects_texts():
    geom = extract_pdf_geometry(_make_pdf())
    assert geom.page_w == pytest.approx(842, abs=1)
    assert geom.lines, "应提取到线段"
    filled_rects = [r for r in geom.rects if r[4]]
    assert filled_rects, "应提取到填充矩形"
    assert any("1:100" in t[2] for t in geom.texts)


@pytest.mark.unit
def test_pdf_broken_bytes_degrade_to_empty():
    geom = extract_pdf_geometry(b"not a pdf")
    assert isinstance(geom, DrawingGeometry)
    assert geom.primitive_count() == 0


@pytest.mark.unit
def test_pdf_page_index_out_of_range_returns_empty():
    geom = extract_pdf_geometry(_make_pdf(), page_index=99)
    assert geom.primitive_count() == 0


def _make_dxf() -> bytes:
    import ezdxf
    import io as _io

    doc = ezdxf.new()
    msp = doc.modelspace()
    msp.add_line((0, 0), (8400, 0))
    msp.add_lwpolyline([(0, 0), (500, 0), (500, 500), (0, 500)], close=True)
    msp.add_text("1:100", dxfattribs={"insert": (10, 10)})
    buf = _io.StringIO()
    doc.write(buf)
    return buf.getvalue().encode()


@pytest.mark.unit
def test_dxf_extracts_entities():
    geom = extract_dxf_geometry(_make_dxf())
    assert geom.lines
    assert geom.polys, "闭合 LWPOLYLINE 应记为多边形"
    assert any("1:100" in t[2] for t in geom.texts)


@pytest.mark.unit
def test_dxf_broken_bytes_degrade_to_empty():
    geom = extract_dxf_geometry(b"\x00\x01 not dxf")
    assert geom.primitive_count() == 0
