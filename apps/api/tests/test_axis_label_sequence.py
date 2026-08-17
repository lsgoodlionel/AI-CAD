"""轴号序列展开与方向排序单测(人工批量标定轴线)。"""
import pytest

from services.axis_label_sequence import (
    ALPHA_AXIS, assign_labels, expand_labels, label_kind, order_lines,
    parse_sub_axis,
)


def _line(x1, y1, x2, y2):
    return {"x1_norm": x1, "y1_norm": y1, "x2_norm": x2, "y2_norm": y2}


# ── 轴号类型判别 ────────────────────────────────────────────────

def test_label_kind_recognizes_numeric_and_alpha():
    assert label_kind("1") == "numeric"
    assert label_kind("12") == "numeric"
    assert label_kind("A") == "alpha"
    assert label_kind("b") == "alpha"
    assert label_kind("AA") == "alpha"


def test_label_kind_recognizes_sub_axis_labels():
    """实际工程图常见附加轴号:1-1 / 2-10 / A-1 / B-20 / A/3。"""
    for label in ("1-1", "2-10", "A-1", "B-20", "A/3", "1 - 2"):
        assert label_kind(label) == "sub", label


def test_parse_sub_axis_splits_base_separator_sequence():
    assert parse_sub_axis("1-1") == ("1", "-", 1)
    assert parse_sub_axis("2-10") == ("2", "-", 10)
    assert parse_sub_axis("A/3") == ("A", "/", 3)
    assert parse_sub_axis("b-20") == ("B", "-", 20)


def test_parse_sub_axis_rejects_illegal_base_or_shape():
    assert parse_sub_axis("I-1") is None      # 主轴号用了禁用字母
    assert parse_sub_axis("1-A") is None      # 分号必须是数字
    assert parse_sub_axis("AB-1") is None     # 主轴号混合字母
    assert parse_sub_axis("1") is None


def test_expand_labels_sub_axis_increments_only_the_sub_part():
    """1-1 顺推是 1-2、1-3,不是 2-1——主轴号不动。"""
    assert expand_labels("1-1", None, 4) == ["1-1", "1-2", "1-3", "1-4"]
    assert expand_labels("2-8", None, 3) == ["2-8", "2-9", "2-10"]
    assert expand_labels("A-1", "A-5", 5) == ["A-1", "A-2", "A-3", "A-4", "A-5"]


def test_expand_labels_sub_axis_rejects_mismatched_base():
    with pytest.raises(ValueError, match="主轴号须一致"):
        expand_labels("1-1", "2-3", 3)


def test_expand_labels_sub_axis_still_validates_count():
    with pytest.raises(ValueError, match="与填写的终止轴号"):
        expand_labels("1-1", "1-9", 3)


def test_label_kind_rejects_forbidden_letters_and_garbage():
    # I/O/Z 制图标准禁用,不认作合法字母轴号
    assert label_kind("I") == "unknown"
    assert label_kind("O") == "unknown"
    assert label_kind("Z") == "unknown"
    assert label_kind("1A") == "unknown"
    assert label_kind("") == "unknown"


# ── 序列展开 ────────────────────────────────────────────────────

def test_expand_labels_numeric_from_start_without_end():
    assert expand_labels("3", None, 4) == ["3", "4", "5", "6"]


def test_expand_labels_alpha_skips_i_o_z():
    seq = expand_labels("G", None, 5)
    assert seq == ["G", "H", "J", "K", "L"]      # 跳过 I
    assert "I" not in ALPHA_AXIS


def test_expand_labels_alpha_wraps_to_double_letters():
    seq = expand_labels("W", None, 5)
    assert seq == ["W", "X", "Y", "AA", "BB"]


def test_expand_labels_validates_end_matches_count():
    assert expand_labels("1", "5", 5) == ["1", "2", "3", "4", "5"]
    # 选中条数与起止轴号对不上 → 报错而非静默错配
    with pytest.raises(ValueError, match="与填写的终止轴号"):
        expand_labels("1", "5", 4)


def test_expand_labels_rejects_mixed_types_and_bad_start():
    with pytest.raises(ValueError, match="类型不一致"):
        expand_labels("1", "C", 3)
    with pytest.raises(ValueError, match="非法"):
        expand_labels("I", None, 2)
    with pytest.raises(ValueError, match="非法"):
        expand_labels("1-A", None, 2)


def test_expand_labels_rejects_empty_and_oversized_selection():
    with pytest.raises(ValueError, match="未选中"):
        expand_labels("1", None, 0)
    with pytest.raises(ValueError, match="上限"):
        expand_labels("1", None, 401)


# ── 方向排序 ────────────────────────────────────────────────────

def test_order_lines_left_to_right_and_reverse():
    lines = [_line(0.8, 0, 0.8, 1), _line(0.2, 0, 0.2, 1), _line(0.5, 0, 0.5, 1)]
    assert [l["x1_norm"] for l in order_lines(lines, "left_to_right")] == [0.2, 0.5, 0.8]
    assert [l["x1_norm"] for l in order_lines(lines, "right_to_left")] == [0.8, 0.5, 0.2]


def test_order_lines_top_to_bottom_uses_image_y_downward():
    lines = [_line(0, 0.7, 1, 0.7), _line(0, 0.1, 1, 0.1)]
    # 归一化坐标 y 向下为正 → 「从上到下」= y 升序
    assert [l["y1_norm"] for l in order_lines(lines, "top_to_bottom")] == [0.1, 0.7]
    assert [l["y1_norm"] for l in order_lines(lines, "bottom_to_top")] == [0.7, 0.1]


def test_order_lines_does_not_mutate_input():
    lines = [_line(0.9, 0, 0.9, 1), _line(0.1, 0, 0.1, 1)]
    order_lines(lines, "left_to_right")
    assert lines[0]["x1_norm"] == 0.9


def test_order_lines_rejects_unknown_direction():
    with pytest.raises(ValueError, match="未知命名方向"):
        order_lines([_line(0, 0, 0, 1)], "diagonal")


# ── 端到端派标签 ────────────────────────────────────────────────

def test_assign_labels_orders_then_names_and_attaches_spacing():
    lines = [_line(0.7, 0, 0.7, 1), _line(0.3, 0, 0.3, 1), _line(0.5, 0, 0.5, 1)]
    refs = assign_labels(
        lines, start="1", end="3", direction="x",
        direction_order="left_to_right", spacing_mm=[8400, 8400],
    )
    assert [r["label"] for r in refs] == ["1", "2", "3"]
    assert [r["x1_norm"] for r in refs] == [0.3, 0.5, 0.7]
    # 第 1 条没有「上一条」,轴距从第 2 条起
    assert [r["spacing_to_prev_mm"] for r in refs] == [None, 8400, 8400]


def test_assign_labels_right_to_left_reverses_naming():
    lines = [_line(0.2, 0, 0.2, 1), _line(0.8, 0, 0.8, 1)]
    refs = assign_labels(
        lines, start="1", end=None, direction="x", direction_order="right_to_left",
    )
    assert [(r["label"], r["x1_norm"]) for r in refs] == [("1", 0.8), ("2", 0.2)]
