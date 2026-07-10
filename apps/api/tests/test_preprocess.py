"""C-02 预处理器测试：DrawingGeometry → SVG + 图元 JSON。

用合成 ``DrawingGeometry`` 夹具（确定性、无需真实文件），覆盖：
图元转换正确性、图层/块透传、SVG 合法性、索引对齐鲁棒性、优雅降级。
"""
from __future__ import annotations

from xml.dom import minidom

import pytest

from core.model3d.preprocess import (
    SCHEMA_VERSION,
    preprocess_drawing,
    preprocess_geometry,
)
from core.model3d.preprocess.primitive_json import geometry_to_primitives
from core.model3d.types import DrawingGeometry


@pytest.fixture()
def geom() -> DrawingGeometry:
    """一张含 1 线 + 1 柱矩形(带块) + 1 闭合多边形 + 1 标高文本的合成图。"""
    g = DrawingGeometry(page_w=500.0, page_h=800.0)
    g.lines.append((50.0, 100.0, 400.0, 100.0))
    g.line_layers.append("S-BEAM")

    g.rects.append((60.0, 60.0, 40.0, 40.0, True))
    g.rect_layers.append("S-COLU")
    g.rect_blocks.append("KZ1")

    g.polys.append([(10.0, 10.0), (30.0, 10.0), (30.0, 30.0), (10.0, 10.0)])
    g.poly_layers.append("A-WALL")
    g.poly_blocks.append("")

    g.texts.append((410.0, 100.0, "+4.200"))
    return g


# ── 图元转换 ─────────────────────────────────────────────────────────────

def test_counts_match_geometry(geom):
    doc = geometry_to_primitives(geom)
    assert doc.counts == {"line": 1, "rect": 1, "polyline": 1, "text": 1}
    assert doc.page_w == 500.0 and doc.page_h == 800.0
    assert doc.schema_version == SCHEMA_VERSION


def test_line_primitive_points(geom):
    doc = geometry_to_primitives(geom)
    line = next(p for p in doc.primitives if p.type == "line")
    assert line.points == ((50.0, 100.0), (400.0, 100.0))
    assert line.layer == "S-BEAM"


def test_rect_expands_to_four_corners_and_keeps_block(geom):
    doc = geometry_to_primitives(geom)
    rect = next(p for p in doc.primitives if p.type == "rect")
    assert rect.points == ((60.0, 60.0), (100.0, 60.0), (100.0, 100.0), (60.0, 100.0))
    assert rect.layer == "S-COLU"
    assert rect.block == "KZ1"
    assert rect.filled is True


def test_polyline_closed_detection(geom):
    doc = geometry_to_primitives(geom)
    poly = next(p for p in doc.primitives if p.type == "polyline")
    assert poly.closed is True  # 首尾同点
    assert poly.layer == "A-WALL"


def test_text_primitive(geom):
    doc = geometry_to_primitives(geom)
    text = next(p for p in doc.primitives if p.type == "text")
    assert text.content == "+4.200"
    assert text.points == ((410.0, 100.0),)


def test_primitive_ids_are_unique(geom):
    doc = geometry_to_primitives(geom)
    ids = [p.id for p in doc.primitives]
    assert len(ids) == len(set(ids))


# ── SVG 序列化 ───────────────────────────────────────────────────────────

def test_svg_is_well_formed_xml(geom):
    result = preprocess_geometry(geom, source_ext="dxf")
    parsed = minidom.parseString(result.svg)  # 抛异常即非法 XML
    assert parsed.documentElement.tagName == "svg"


def test_svg_carries_layer_and_block_provenance(geom):
    result = preprocess_geometry(geom, source_ext="dxf")
    assert 'data-layer="S-COLU"' in result.svg
    assert 'data-block="KZ1"' in result.svg


def test_svg_escapes_text_content():
    g = DrawingGeometry(page_w=100, page_h=100)
    g.texts.append((10.0, 10.0, "A<B&C"))
    result = preprocess_geometry(g)
    minidom.parseString(result.svg)  # 含特殊字符仍是合法 XML
    assert "&lt;" in result.svg and "&amp;" in result.svg


# ── 索引对齐鲁棒性 ───────────────────────────────────────────────────────

def test_missing_parallel_layer_degrades_to_empty_string():
    """并行列表短于几何列表时不越界，降级为空串。"""
    g = DrawingGeometry(page_w=100, page_h=100)
    g.lines.append((0.0, 0.0, 10.0, 10.0))
    # 故意不 append line_layers
    doc = geometry_to_primitives(g)
    assert doc.primitives[0].layer == ""


# ── 端到端 + 降级 ────────────────────────────────────────────────────────

def test_preprocess_drawing_unknown_ext_degrades():
    result = preprocess_drawing(b"garbage", "xyz")
    assert result.doc.counts == {"line": 0, "rect": 0, "polyline": 0, "text": 0}
    assert any("不支持" in w for w in result.doc.warnings)


def test_preprocess_drawing_bad_pdf_degrades_without_raising():
    # 非法 PDF 字节：不抛异常，降级为空文档
    result = preprocess_drawing(b"%PDF-broken", "pdf")
    assert result.source_ext == "pdf"
    assert isinstance(result.svg, str)


def test_result_to_dict_round_trips_schema(geom):
    result = preprocess_geometry(geom, source_ext="dxf")
    d = result.to_dict()
    assert d["doc"]["schema_version"] == SCHEMA_VERSION
    assert d["doc"]["counts"]["rect"] == 1
    assert d["source_ext"] == "dxf"
    assert d["doc"]["primitives"][0]["points"]
