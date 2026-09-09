"""识别器接入密排阵列判据：座椅排不再变成柱，柱网一根不少。

实测来源见 `core/model3d/dense_array_filter.py` 模块文档
与 `data/model3d/gold/rule_vs_model_v1.json`。
"""
from core.model3d import DrawingGeometry, recognize
from core.model3d.element_recognizer import SCALE_1_100_M_PER_PT

PAGE_W, PAGE_H = 842.0, 595.0
PT_PER_M = 1.0 / SCALE_1_100_M_PER_PT


def _plan(*, seats: bool) -> DrawingGeometry:
    """结构平面：4 根柱（8.4m 轴距）+ 可选的一排座椅。"""
    geom = DrawingGeometry(page_w=PAGE_W, page_h=PAGE_H)
    ox, oy = 100.0, 100.0
    span = 8.4 * PT_PER_M
    for i in range(3):
        x = ox + i * span
        geom.lines.append((x, 30.0, x, PAGE_H - 30.0))
        y = oy + i * span * 0.5
        geom.lines.append((30.0, y, PAGE_W - 30.0, y))
    col = 0.6 * PT_PER_M
    for i in range(2):
        for j in range(2):
            geom.rects.append((ox + i * span - col / 2,
                               oy + j * span * 0.5 - col / 2, col, col, True))
    if seats:
        # 实测形态：边长 0.36m、间距 0.36m、一排 8 个（二层平面图(五)）
        side = 0.36 * PT_PER_M
        sy = oy + 200.0
        for k in range(8):
            geom.rects.append((ox + k * side, sy, side, side, True))
    geom.texts.append((60.0, 40.0, "1:100"))
    geom.texts.append((400.0, 20.0, "二层平面图"))
    return geom


def _n_columns(geom) -> int:
    return len(recognize(geom, "architecture", "d1", drawing_title="二层平面图").columns)


def test_seat_row_does_not_become_columns():
    """加进一排 8 个座椅，柱数不该变 —— 座椅全部被判为阵列。"""
    base = _n_columns(_plan(seats=False))
    with_seats = _n_columns(_plan(seats=True))
    assert base == 4, f"合成图的基准柱数应为 4，实为 {base}"
    assert with_seats == base


def test_column_grid_intact_without_seats():
    """没有座椅时判据不得改变任何结果（零误伤基线）。"""
    assert _n_columns(_plan(seats=False)) == 4


def test_dropped_arrays_are_visible():
    """被删的候选必须留痕 —— 「降级必须可见」。

    没有这个字段就无法核验判据删对没删对：删掉的东西一旦消失，
    误伤就变成了看不见的损失。
    """
    fe = recognize(_plan(seats=True), "architecture", "d1",
                   drawing_title="二层平面图")
    assert len(fe.dense_arrays) == 8
    assert len(fe.columns) == 4
    # 诊断字段不得混进构件输出，否则座椅会从另一个门回到模型里
    assert "dense_arrays" not in fe.as_dict()


def test_no_arrays_means_empty_diagnostic():
    fe = recognize(_plan(seats=False), "architecture", "d1",
                   drawing_title="二层平面图")
    assert fe.dense_arrays == []
