"""C-03 块展开器测试：嵌套块 / MINSERT 阵列 / 块名溯源 / 变换正确性。

用 ezdxf 内存构造 DXF（无需真实文件），断言展开后图元数、块名/图层溯源，
以及对 C-02 缺口的修复（线段携带块名）。
"""
from __future__ import annotations

import io

import ezdxf
import pytest

from core.model3d.preprocess.block_expander import expand_blocks


def _dxf_bytes(doc) -> bytes:
    buf = io.StringIO()
    doc.write(buf)
    return buf.getvalue().encode("utf-8")


@pytest.fixture()
def nested_block_dxf() -> bytes:
    """外层块 KZ1 内嵌套子块 STIRRUP（内含线段），插入到模型空间。"""
    doc = ezdxf.new()
    inner = doc.blocks.new(name="STIRRUP")
    inner.add_line((0, 0), (100, 0), dxfattribs={"layer": "S-REBAR"})

    outer = doc.blocks.new(name="KZ1")
    outer.add_lwpolyline(
        [(0, 0), (400, 0), (400, 400), (0, 400), (0, 0)],
        dxfattribs={"layer": "S-COLU"},
    )
    outer.add_blockref("STIRRUP", (50, 50))  # 嵌套块引用

    msp = doc.modelspace()
    msp.add_blockref("KZ1", (1000, 1000), dxfattribs={"layer": "S-COLU"})
    return _dxf_bytes(doc)


def test_nested_block_expands_all_geometry(nested_block_dxf):
    doc = expand_blocks(nested_block_dxf)
    # 外层柱轮廓（1 条闭合 lwpolyline）+ 嵌套箍筋线（1 条）
    assert doc.counts["polyline"] >= 1
    assert doc.counts["line"] >= 1


def test_block_name_provenance_on_all_primitives_including_lines(nested_block_dxf):
    """C-02 缺口修复：嵌套块内线段也携带顶层块名 KZ1（弱标签溯源）。"""
    doc = expand_blocks(nested_block_dxf)
    line = next(p for p in doc.primitives if p.type == "line")
    assert line.block == "KZ1"          # 沿用顶层 INSERT 块名
    assert line.layer == "S-REBAR"      # 保留自身图层


def test_layer_preserved_on_polyline(nested_block_dxf):
    doc = expand_blocks(nested_block_dxf)
    poly = next(p for p in doc.primitives if p.type == "polyline")
    assert poly.layer == "S-COLU"
    assert poly.block == "KZ1"


def test_minsert_array_expands_each_cell():
    """MINSERT 阵列（2×3）应逐格展开，得到 6 份块内容。"""
    doc = ezdxf.new()
    blk = doc.blocks.new(name="DOT")
    blk.add_line((0, 0), (10, 0), dxfattribs={"layer": "GRID"})
    msp = doc.modelspace()
    ref = msp.add_blockref("DOT", (0, 0))
    ref.grid(size=(2, 3), spacing=(100, 100))  # row_count=2, col_count=3
    data = _dxf_bytes(doc)

    result = expand_blocks(data)
    lines = [p for p in result.primitives if p.type == "line"]
    assert len(lines) == 6
    assert all(p.block == "DOT" for p in lines)


def test_insert_scale_transform_applied():
    """INSERT 缩放应作用到展开坐标（虚拟实体已含变换）。"""
    doc = ezdxf.new()
    blk = doc.blocks.new(name="UNIT")
    blk.add_line((0, 0), (1, 0))
    msp = doc.modelspace()
    msp.add_blockref("UNIT", (0, 0), dxfattribs={"xscale": 10, "yscale": 10})
    result = expand_blocks(_dxf_bytes(doc))
    line = next(p for p in result.primitives if p.type == "line")
    (x0, _), (x1, _) = line.points
    assert abs(abs(x1 - x0) - 10.0) < 1e-6  # 长度被放大 10 倍


def test_top_level_entity_has_empty_block():
    doc = ezdxf.new()
    doc.modelspace().add_line((0, 0), (5, 5), dxfattribs={"layer": "S-BEAM"})
    result = expand_blocks(_dxf_bytes(doc))
    line = next(p for p in result.primitives if p.type == "line")
    assert line.block == ""
    assert line.layer == "S-BEAM"


def test_text_in_block_keeps_block_name_and_content():
    doc = ezdxf.new()
    blk = doc.blocks.new(name="TAG")
    blk.add_text("KZ1", dxfattribs={"layer": "TEXT"}).set_placement((0, 0))
    doc.modelspace().add_blockref("TAG", (500, 500))
    result = expand_blocks(_dxf_bytes(doc))
    text = next(p for p in result.primitives if p.type == "text")
    assert text.content == "KZ1"
    assert text.block == "TAG"


def test_solid_becomes_closed_filled_polygon():
    doc = ezdxf.new()
    doc.modelspace().add_solid([(0, 0), (10, 0), (10, 10), (0, 10)])
    result = expand_blocks(_dxf_bytes(doc))
    solid = next(p for p in result.primitives if p.type == "polyline")
    assert solid.closed is True
    assert solid.filled is True


def test_open_polyline_not_marked_closed():
    doc = ezdxf.new()
    doc.modelspace().add_lwpolyline(
        [(0, 0), (10, 0), (20, 10)], close=False, dxfattribs={"layer": "A-WALL"}
    )
    result = expand_blocks(_dxf_bytes(doc))
    poly = next(p for p in result.primitives if p.type == "polyline")
    assert poly.closed is False
    assert poly.layer == "A-WALL"


def test_page_size_from_geometry_bounds():
    doc = ezdxf.new()
    doc.modelspace().add_line((0, 0), (300, 200))
    result = expand_blocks(_dxf_bytes(doc))
    assert result.page_w >= 300.0 - 1e-6
    assert result.page_h >= 200.0 - 1e-6


def test_garbage_input_degrades_gracefully():
    result = expand_blocks(b"not a dxf")
    assert result.primitives == ()
    assert result.warnings  # 有降级 warning，不抛异常
