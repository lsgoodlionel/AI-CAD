"""C-03 坐标归一化测试：等比缩放到 [0,1]、保持长宽比、可逆、退化处理。"""
from __future__ import annotations

from core.model3d.preprocess.normalize import normalize_doc
from core.model3d.preprocess.schema import Primitive, PrimitiveDoc


def _doc(*prims: Primitive) -> PrimitiveDoc:
    return PrimitiveDoc(primitives=tuple(prims))


def test_normalizes_into_unit_box():
    doc = _doc(
        Primitive(id=0, type="line", points=((100.0, 200.0), (300.0, 400.0))),
    )
    norm, params = normalize_doc(doc)
    pts = [pt for p in norm.primitives for pt in p.points]
    xs = [x for x, _ in pts]
    ys = [y for _, y in pts]
    assert min(xs) == 0.0 and min(ys) == 0.0
    assert max(max(xs), max(ys)) <= 1.0 + 1e-9


def test_preserves_aspect_ratio():
    # 宽 400、高 200 → 等比缩放，较长边=1，较短边=0.5
    doc = _doc(
        Primitive(id=0, type="line", points=((0.0, 0.0), (400.0, 200.0))),
    )
    norm, params = normalize_doc(doc)
    (x0, y0), (x1, y1) = norm.primitives[0].points
    assert abs(x1 - 1.0) < 1e-9      # 长边归一化到 1
    assert abs(y1 - 0.5) < 1e-9      # 短边保持比例 0.5
    assert params.scale == 1.0 / 400.0


def test_params_are_invertible():
    doc = _doc(
        Primitive(id=0, type="line", points=((100.0, 50.0), (500.0, 250.0))),
    )
    norm, params = normalize_doc(doc)
    # 反变换应还原原始坐标
    for orig_p, norm_p in zip(doc.primitives, norm.primitives):
        for (ox, oy), (nx, ny) in zip(orig_p.points, norm_p.points):
            rx = nx / params.scale + params.offset_x
            ry = ny / params.scale + params.offset_y
            assert abs(rx - ox) < 1e-6 and abs(ry - oy) < 1e-6


def test_empty_doc_returns_identity():
    norm, params = normalize_doc(_doc())
    assert norm.primitives == ()
    assert params.scale == 1.0


def test_degenerate_single_point_only_translates():
    doc = _doc(Primitive(id=0, type="text", points=((42.0, 42.0),), content="x"))
    norm, params = normalize_doc(doc)
    # 退化：extent=0 → scale=1，仅平移到原点
    assert norm.primitives[0].points[0] == (0.0, 0.0)
    assert params.scale == 1.0


def test_immutability_original_unchanged():
    doc = _doc(Primitive(id=0, type="line", points=((10.0, 10.0), (20.0, 20.0))))
    normalize_doc(doc)
    assert doc.primitives[0].points == ((10.0, 10.0), (20.0, 20.0))
