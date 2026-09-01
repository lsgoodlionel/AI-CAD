"""图例表切分 + 图例名称分类 的单元测试。

图例表是训练标记数据的**强标签**来源（符号图 ↔ 中文名同一行），
所以这里重点测「配错行 / 配错列 / 名称串味」这三类会污染标签的错误。
"""
from __future__ import annotations

import pytest

from core.knowledge import label_map, legend_table as lt


def _cell(row, col, text="", ink=0.05, cov=0.0, bbox=(0, 0, 40, 40)):
    return lt.Cell(row=row, col=col, bbox=bbox, text=text,
                   ink_ratio=ink, text_coverage=cov)


# ── 格内文字拼接 ─────────────────────────────────────────

@pytest.mark.unit
def test_same_line_spans_are_ordered_by_x_not_y():
    """**实测**：`金 属` 两字同一行，但 OCR 给的 y0 差几个像素，
    按 (y, x) 排会读成 `属金`。行带按纵向重叠分组，对像素抖动免疫。"""
    spans = [(102.0, 130.0, 200.0, "属"), (100.0, 128.0, 150.0, "金")]
    assert lt._join_spans(spans) == "金属"


@pytest.mark.unit
def test_different_lines_keep_top_to_bottom_order():
    spans = [(200.0, 228.0, 10.0, "下"), (100.0, 128.0, 10.0, "上")]
    assert lt._join_spans(spans) == "上下"


# ── 表头识别 ─────────────────────────────────────────────

@pytest.mark.unit
def test_header_needs_both_a_symbol_and_a_text_column():
    """定不到表头就**不出数据** —— 列序在不同书里不一致
    （材料图例是 `名称|图例|说明`，电气符号表是 `序号|符号|说明`），
    假设第二列是符号会系统性标错。"""
    only_symbol = [_cell(0, 0, "图例")]
    assert lt.classify_columns(only_symbol) == (-1, {})


@pytest.mark.unit
def test_header_row_and_roles_are_detected():
    cells = [_cell(0, 0, "名称"), _cell(0, 1, "图例"), _cell(0, 2, "说明")]
    row, roles = lt.classify_columns(cells)
    assert row == 0
    assert roles == {0: "name", 1: "symbol", 2: "note"}


# ── 列配对 ───────────────────────────────────────────────

@pytest.mark.unit
def test_repeated_column_groups_are_all_paired():
    """一页常横向重复多组（`名称|图例|说明|名称|图例|说明`）。
    只认第一个符号列会**丢掉右半张表**。"""
    roles = {0: "name", 1: "symbol", 2: "note",
             3: "name", 4: "symbol", 5: "note"}
    groups = lt._pair_columns(roles)
    assert [g["symbol"] for g in groups] == [1, 4]
    assert [g["name"] for g in groups] == [0, 3]


@pytest.mark.unit
def test_note_column_is_the_name_when_the_table_has_none():
    """电气图形符号表是 `序号|符号|说明` 三栏，说明栏写的正是符号名
    （「中性线」「屏蔽导体」）—— 这是表结构，不是兜底失败。"""
    groups = lt._pair_columns({0: "index", 1: "symbol", 2: "note"})
    assert groups[0]["name"] == 2
    assert groups[0]["name_role"] == "note_column"


@pytest.mark.unit
def test_entries_pair_symbol_with_name_in_the_same_row():
    cells = [_cell(0, 0, "名称"), _cell(0, 1, "图例"),
             _cell(1, 0, "自然土壤", cov=0.6), _cell(1, 1, "", ink=0.08),
             _cell(2, 0, "夯实土壤", cov=0.6), _cell(2, 1, "", ink=0.07)]
    out = lt.extract_entries("bk", 3, 0, cells, rotated=False)
    assert [e.name for e in out] == ["自然土壤", "夯实土壤"]
    assert all(e.page_index == 3 for e in out)


# ── 名称质量 ─────────────────────────────────────────────

@pytest.mark.unit
def test_long_or_enumerated_names_are_flagged_not_dropped():
    """合并单元格被网格切碎后，说明的片段会落到名称位置。
    标记而不丢弃 —— 让下游按需过滤，也让人看得见问题在哪。"""
    assert lt.is_plausible_name("钢筋混凝土")
    assert not lt.is_plausible_name("（1）包括各种金属")
    assert not lt.is_plausible_name("包括平板玻璃磨砂玻璃夹丝玻璃钢化玻璃等等")


# ── 旋转判定 ─────────────────────────────────────────────

@pytest.mark.unit
def test_rotation_needs_enough_samples():
    """样本太少不下结论 —— 宁可当未旋转，也别把正常页转坏。"""
    few = [{"b": [0, 0, 10, 90]}] * 5
    assert lt.is_rotated(few) is False


@pytest.mark.unit
def test_tall_tokens_indicate_a_rotated_page():
    tall = [{"b": [0, 0, 10, 90]}] * 20
    wide = [{"b": [0, 0, 90, 10]}] * 20
    assert lt.is_rotated(tall) is True
    assert lt.is_rotated(wide) is False


# ── 名称 → 分类 ──────────────────────────────────────────

@pytest.mark.unit
def test_materials_map_to_material_not_to_a_component():
    """「混凝土」「普通砖」是剖面填充图案，**不是构件**。
    给它们编一个构件类别会污染训练标签。"""
    for name, sub in (("钢筋混凝土", "reinforced_concrete"),
                      ("普通砖", "brick"), ("玻璃", "glass")):
        m = label_map.map_label(name)
        assert m.domain == "material" and m.taxonomy is None
        assert m.subclass == sub


@pytest.mark.unit
def test_components_map_into_the_nine_class_taxonomy():
    for name, kind in (("隔断", "wall"), ("土墙", "wall"),
                       ("防火门", "door"), ("楼梯", "slab")):
        m = label_map.map_label(name)
        assert m.taxonomy == kind, name
        assert m.taxonomy in label_map.TAXONOMY_KINDS


@pytest.mark.unit
def test_electrical_terms_win_over_generic_component_characters():
    """**实测**：`端子板` 含「板」，曾被「板 → slab」抢先命中成了楼板。
    专业名词必须排在通配字之前。"""
    m = label_map.map_label("端子板")
    assert m.domain == "electrical" and m.taxonomy == "equipment"


@pytest.mark.unit
def test_circuit_symbols_have_no_spatial_taxonomy():
    """系统图上的电路元件没有空间实体，映射成构件会给建模
    引入根本不存在的东西。"""
    for name in ("PNP 半导体管", "三相笼型感应电动机", "热断电器，动断触点"):
        m = label_map.map_label(name)
        assert m.domain == "electrical"
        assert m.taxonomy is None and m.subclass == "circuit_symbol", name


@pytest.mark.unit
def test_spatial_electrical_devices_do_map_to_equipment():
    for name in ("配电箱", "单极拉线开关", "避雷针"):
        m = label_map.map_label(name)
        assert m.taxonomy == "equipment", name


@pytest.mark.unit
def test_unknown_names_stay_unmapped():
    """认不出就如实留白，**不猜**。"""
    m = label_map.map_label("某个查无此物的名字")
    assert m.domain == "unmapped" and m.taxonomy is None


@pytest.mark.unit
def test_note_is_only_used_when_the_name_says_nothing():
    """说明里常提到别的构件（「包括各种自然土壤」），先用它会带偏。"""
    m = label_map.map_label("石材", note="包括岩层、砌体、铺地、贴面等材料")
    assert m.matched_by.startswith("name:")
