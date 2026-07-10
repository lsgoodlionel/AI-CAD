"""A-12 图纸切图预处理器（喂 VLM）测试。

覆盖：PDF/DXF 两路产出、标题栏右下角命中、输出尺寸在模型上限内、
大图切片触发、坏字节/未知扩展名优雅降级。
"""
import io

import pytest

from services.vlm_preprocess import (
    MAX_VLM_PX,
    _locate_title_block,
    preprocess_for_vlm,
)


def _png_size(png: bytes) -> tuple[int, int]:
    from PIL import Image

    with Image.open(io.BytesIO(png)) as image:
        return image.size


def _make_pdf(width: float = 842, height: float = 595) -> bytes:
    """构造矢量 PDF：图面区少量文字 + 右下角密集图签文字块。"""
    import fitz

    doc = fitz.open()
    page = doc.new_page(width=width, height=height)
    # 图面区（左上）稀疏文字
    page.insert_text(fitz.Point(60, 40), "1:100")
    page.insert_text(fitz.Point(120, 300), "PLAN")
    # 右下角图签密集文字块（corner 区：nx>=0.55, ny>=0.62）
    tx = width * 0.77
    for i, label in enumerate(
        ["图号 结施-01", "专业 结构", "比例 1:100", "日期 2026", "设计 张三", "审核 李四"]
    ):
        page.insert_text(fitz.Point(tx, height * 0.72 + i * 14), label)
    data = doc.tobytes()
    doc.close()
    return data


@pytest.mark.unit
def test_pdf_produces_title_block_and_overview():
    result = preprocess_for_vlm(_make_pdf(), "pdf")
    assert result["title_block_png"], "应产出标题栏裁剪"
    assert result["overview_png"], "应产出整图缩略图"

    ov_w, ov_h = _png_size(result["overview_png"])
    assert max(ov_w, ov_h) <= MAX_VLM_PX, "overview 最长边须 ≤ 模型上限"
    tb_w, tb_h = _png_size(result["title_block_png"])
    assert max(tb_w, tb_h) <= MAX_VLM_PX, "标题栏裁剪最长边须 ≤ 模型上限"
    assert tb_w > 0 and tb_h > 0


@pytest.mark.unit
def test_pdf_title_block_hits_bottom_right():
    """标题栏定位应命中右下区域（bbox 中心 nx>0.5 且 ny>0.5）。"""
    from core.model3d.geometry_extractor import extract_pdf_geometry

    data = _make_pdf()
    geom = extract_pdf_geometry(data)
    # 复刻 _pdf_norm_points 的归一化（A1 页尺寸 842x595）
    points = [(x / 842, y / 595) for x, y, _ in geom.texts]
    bbox = _locate_title_block(points)
    assert bbox is not None, "密集图签块应被定位"
    cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
    assert cx > 0.5 and cy > 0.5, f"标题栏中心应在右下: {bbox}"


@pytest.mark.unit
def test_ext_case_and_dot_insensitive():
    result = preprocess_for_vlm(_make_pdf(), ".PDF")
    assert result["overview_png"]


@pytest.mark.unit
def test_large_pdf_triggers_tiles():
    """A0 量级大图应触发高分辨率切片，每片在上限内。"""
    result = preprocess_for_vlm(_make_pdf(width=1189, height=841), "pdf")
    tiles = result["tiles"]
    assert tiles, "大图应产出切片"
    for tile in tiles:
        w, h = _png_size(tile)
        assert max(w, h) <= MAX_VLM_PX


@pytest.mark.unit
def test_normal_pdf_has_no_tiles():
    result = preprocess_for_vlm(_make_pdf(), "pdf")
    assert result["tiles"] is None


@pytest.mark.unit
def test_locate_returns_none_when_too_few_texts():
    assert _locate_title_block([(0.8, 0.9), (0.85, 0.92)]) is None


@pytest.mark.unit
def test_locate_prefers_bottom_right_corner():
    corner = [(0.7, 0.7), (0.75, 0.75), (0.8, 0.8), (0.85, 0.85), (0.9, 0.9)]
    scattered = [(0.1, 0.1), (0.2, 0.2)]
    bbox = _locate_title_block(corner + scattered)
    assert bbox is not None
    assert bbox[0] >= 0.5 and bbox[1] >= 0.5


def _make_dxf() -> bytes:
    """小 DXF：图框线 + 右下角图签文字（世界坐标 y 向上，右下=max x, min y）。"""
    import ezdxf

    doc = ezdxf.new()
    msp = doc.modelspace()
    msp.add_lwpolyline(
        [(0, 0), (8400, 0), (8400, 5900), (0, 5900)], close=True
    )
    # 右下角图签（大 x、小 y）
    for i, label in enumerate(["结施-01", "结构", "1:100", "2026", "张三", "李四"]):
        msp.add_text(label, dxfattribs={"insert": (6800, 300 + i * 120)})
    buf = io.StringIO()
    doc.write(buf)
    return buf.getvalue().encode()


@pytest.mark.unit
def test_dxf_produces_overview_and_title_block():
    result = preprocess_for_vlm(_make_dxf(), "dxf")
    assert result["overview_png"], "DXF 应产出整图缩略图"
    ov_w, ov_h = _png_size(result["overview_png"])
    assert max(ov_w, ov_h) <= MAX_VLM_PX
    # 标题栏至少走到回退裁剪，非空
    assert result["title_block_png"]
    tb_w, tb_h = _png_size(result["title_block_png"])
    assert max(tb_w, tb_h) <= MAX_VLM_PX


@pytest.mark.unit
def test_broken_bytes_degrade_gracefully():
    for ext in ("pdf", "dxf", "dwg"):
        result = preprocess_for_vlm(b"not a real file", ext)
        assert set(result) == {"title_block_png", "overview_png", "tiles"}
        # 不抛异常即达标；坏字节下各字段为空/None
        assert isinstance(result["title_block_png"], bytes)


@pytest.mark.unit
def test_unknown_ext_returns_empty_result():
    result = preprocess_for_vlm(b"whatever", "png")
    assert result == {"title_block_png": b"", "overview_png": b"", "tiles": None}
