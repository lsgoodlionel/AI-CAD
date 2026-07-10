"""A-14/A-15：几何原语携带 CAD 图层名与 INSERT 块名（并行列表索引对齐）测试。"""
import io

import pytest

from core.model3d import DrawingGeometry, extract_pdf_geometry
from core.model3d.geometry_extractor import extract_dxf_geometry


# --- 构造样例 DXF ----------------------------------------------------------

def _make_layered_dxf() -> bytes:
    """含 S-COLU / A-WALL 图层实体 + 一个 INSERT 块引用的 DXF。

    - A-WALL 图层：一条墙线（LINE）
    - S-COLU 图层：一个闭合方形（LWPOLYLINE，柱轮廓）
    - 块 COL_BLK：内含 (0,0)-(300,300) 闭合方形，INSERT 于 (1000, 2000)
    """
    import ezdxf

    doc = ezdxf.new()
    doc.layers.add("A-WALL")
    doc.layers.add("S-COLU")
    msp = doc.modelspace()

    msp.add_line((0, 0), (8400, 0), dxfattribs={"layer": "A-WALL"})
    msp.add_lwpolyline(
        [(100, 100), (600, 100), (600, 600), (100, 600)],
        close=True,
        dxfattribs={"layer": "S-COLU"},
    )

    block = doc.blocks.new(name="COL_BLK")
    block.add_lwpolyline(
        [(0, 0), (300, 0), (300, 300), (0, 300)],
        close=True,
        dxfattribs={"layer": "S-COLU"},
    )
    msp.add_blockref("COL_BLK", insert=(1000, 2000))

    buf = io.StringIO()
    doc.write(buf)
    return buf.getvalue().encode()


# --- 索引对齐契约 ----------------------------------------------------------

def _assert_aligned(geom: DrawingGeometry) -> None:
    assert len(geom.line_layers) == len(geom.lines)
    assert len(geom.rect_layers) == len(geom.rects)
    assert len(geom.rect_blocks) == len(geom.rects)
    assert len(geom.poly_layers) == len(geom.polys)
    assert len(geom.poly_blocks) == len(geom.polys)


@pytest.mark.unit
def test_dxf_parallel_lists_aligned():
    geom = extract_dxf_geometry(_make_layered_dxf())
    _assert_aligned(geom)


@pytest.mark.unit
def test_dxf_collects_layer_names():
    geom = extract_dxf_geometry(_make_layered_dxf())
    # A-WALL 墙线的图层被采集到 line_layers
    assert "A-WALL" in geom.line_layers
    # S-COLU 柱轮廓（闭合折线）的图层被采集到 poly_layers
    assert "S-COLU" in geom.poly_layers


@pytest.mark.unit
def test_dxf_insert_block_expanded_with_name():
    geom = extract_dxf_geometry(_make_layered_dxf())
    # 块内闭合方形被展开为多边形，并记录块名 COL_BLK
    assert "COL_BLK" in geom.poly_blocks
    # 块内实体图层仍被采集（块定义中为 S-COLU）
    idx = geom.poly_blocks.index("COL_BLK")
    assert geom.poly_layers[idx] == "S-COLU"


@pytest.mark.unit
def test_dxf_insert_transform_applied():
    geom = extract_dxf_geometry(_make_layered_dxf())
    idx = geom.poly_blocks.index("COL_BLK")
    poly = geom.polys[idx]
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    # 块内 (0,0)-(300,300) 经 insert=(1000,2000) 平移 → (1000,2000)-(1300,2300)
    assert min(xs) == pytest.approx(1000, abs=1)
    assert max(xs) == pytest.approx(1300, abs=1)
    assert min(ys) == pytest.approx(2000, abs=1)
    assert max(ys) == pytest.approx(2300, abs=1)


@pytest.mark.unit
def test_dxf_scaled_rotated_insert_transform():
    """带缩放的 INSERT：virtual_entities 应用 xscale/yscale 变换。"""
    import ezdxf

    doc = ezdxf.new()
    doc.layers.add("S-COLU")
    msp = doc.modelspace()
    block = doc.blocks.new(name="SQ")
    block.add_lwpolyline(
        [(0, 0), (100, 0), (100, 100), (0, 100)],
        close=True,
        dxfattribs={"layer": "S-COLU"},
    )
    msp.add_blockref(
        "SQ", insert=(500, 500),
        dxfattribs={"xscale": 2.0, "yscale": 2.0},
    )
    buf = io.StringIO()
    doc.write(buf)
    geom = extract_dxf_geometry(buf.getvalue().encode())

    _assert_aligned(geom)
    idx = geom.poly_blocks.index("SQ")
    poly = geom.polys[idx]
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    # (0..100)*2 + 平移 500 → 500..700
    assert min(xs) == pytest.approx(500, abs=1)
    assert max(xs) == pytest.approx(700, abs=1)
    assert min(ys) == pytest.approx(500, abs=1)
    assert max(ys) == pytest.approx(700, abs=1)


# --- PDF 无图层：并行列表填空串且对齐 --------------------------------------

def _make_pdf() -> bytes:
    import fitz

    doc = fitz.open()
    page = doc.new_page(width=842, height=595)
    shape = page.new_shape()
    shape.draw_line(fitz.Point(50, 50), fitz.Point(500, 50))
    shape.finish(color=(0, 0, 0))
    shape.draw_rect(fitz.Rect(100, 100, 130, 130))
    shape.finish(color=(0, 0, 0), fill=(0, 0, 0))
    shape.commit()
    data = doc.tobytes()
    doc.close()
    return data


@pytest.mark.unit
def test_pdf_parallel_lists_empty_strings_and_aligned():
    geom = extract_pdf_geometry(_make_pdf())
    _assert_aligned(geom)
    assert all(layer == "" for layer in geom.line_layers)
    assert all(layer == "" for layer in geom.rect_layers)
    assert all(block == "" for block in geom.rect_blocks)
    assert all(block == "" for block in geom.poly_blocks)
