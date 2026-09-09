"""柱/板轮廓环序 → 算量的端到端回归。

实测缺陷：60 张结构/建筑平面图上 2497 个柱候选里 407 个（非细条候选的
18.6%）鞋带面积**恰好为 0**，全部是 4 个角点、包围盒 0.49×0.63m
（正是真柱尺寸档），成因是环自交而非退化图形。
`services/model_qto.py:_column_quantity` 用鞋带面积×层高算混凝土量，
面积为 0 ⇒ 该柱混凝土量 0、模板量失真，而算量要喂进创效提案走三审。
"""
import pytest

from core.model3d import DrawingGeometry, recognize
from core.model3d.element_recognizer import SCALE_1_100_M_PER_PT
from services.model_qto import compute_quantities

PAGE_W, PAGE_H = 842.0, 595.0
PT_PER_M = 1.0 / SCALE_1_100_M_PER_PT
COL_W_M, COL_H_M = 0.49, 0.63


def _bowtie_column_poly(x: float, y: float) -> list[tuple[float, float]]:
    """矩形柱被画成「底边 + 顶边」两条线段时 `path_points` 的点序。

    `geometry_extractor._collect_pdf_drawings` 对每条线段
    `path_points.extend([a, b])` —— 两条对边给出 [A,B,D,C]，即 8 字形。
    """
    w, h = COL_W_M * PT_PER_M, COL_H_M * PT_PER_M
    a, b = (x, y), (x + w, y)              # 底边
    d, c = (x, y + h), (x + w, y + h)      # 顶边
    return [a, b, d, c]


def _plan_with_bowtie_columns(count: int = 4) -> DrawingGeometry:
    geom = DrawingGeometry(page_w=PAGE_W, page_h=PAGE_H)
    ox, oy = 100.0, 100.0
    span = 8.4 * PT_PER_M
    for i in range(3):
        x = ox + i * span
        geom.lines.append((x, 30.0, x, PAGE_H - 30.0))
        geom.lines.append((30.0, oy + i * span * 0.5, PAGE_W - 30.0, oy + i * span * 0.5))
    for k in range(count):
        geom.polys.append(_bowtie_column_poly(ox + k * span * 0.3, oy))
        geom.poly_layers.append("")
        geom.poly_blocks.append("")
    geom.texts.append((60.0, 40.0, "1:100"))
    geom.texts.append((400.0, 20.0, "一层墙柱结构平面图"))
    return geom


def _area(outline) -> float:
    n = len(outline)
    total = sum(outline[i][0] * outline[(i + 1) % n][1]
                - outline[(i + 1) % n][0] * outline[i][1] for i in range(n))
    return abs(total) / 2.0


@pytest.mark.unit
def test_bowtie_polys_yield_columns_with_nonzero_area():
    result = recognize(_plan_with_bowtie_columns(), "structure", "d-bowtie")
    columns = result.as_dict()["columns"]
    assert columns, "自交环的柱不该被整体丢掉"
    for column in columns:
        assert _area(column["outline"]) > 0.0, "柱轮廓鞋带面积不得为 0"


@pytest.mark.unit
def test_bowtie_columns_keep_real_bounding_box():
    """修环序不得改变真实范围（尺寸检查用的就是包围盒）。"""
    result = recognize(_plan_with_bowtie_columns(1), "structure", "d-bbox")
    outline = result.as_dict()["columns"][0]["outline"]
    xs = [p[0] for p in outline]
    ys = [p[1] for p in outline]
    assert max(xs) - min(xs) == pytest.approx(COL_W_M, abs=0.02)
    assert max(ys) - min(ys) == pytest.approx(COL_H_M, abs=0.02)


@pytest.mark.unit
def test_qto_gives_nonzero_concrete_for_bowtie_columns():
    """真正要紧的下游：算量。面积为 0 ⇒ 混凝土量 0，直接进创效提案。"""
    elements = recognize(_plan_with_bowtie_columns(), "structure", "d-qto").as_dict()
    quantities = compute_quantities(elements, story_height_m=4.5)
    columns = [q for q in quantities if q.element_type == "column"]
    assert columns
    for q in columns:
        assert q.net_volume_m3 > 0.0
        # 0.49×0.63×4.5 ≈ 1.39 m³，给足余量只断言量级正确
        assert 0.5 < q.net_volume_m3 < 3.0
