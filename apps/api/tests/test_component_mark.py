"""平法构件编号识别 —— 从「人看图的角度」推演出的能力缺口。

**来源**：19 个专业 133 条会审检查项里，「**编号**」出现 **38 条**，
是仅次于说明/做法/详图的第四高频要素。人核图的主要抓手正是它：
看到平面图标 `KZ1`，就去柱表查它的配筋与截面。

**两个数据库实测**（档案层现存、但从未被识别为构件编号）：

| | 大歌剧院 | 轨道交通 |
|---|---:|---:|
| 柱（KZ/XZ…） | 489 | 117 |
| 梁（KL/LL…） | 3962 | 555 |
| 墙（Q/AZ…） | 936 | 177 |
| 板（LB/WB…） | 3819 | 497 |

编号直接标明构件类型 —— 比几何猜测可靠得多，
且是**跨图关联的主键**（平面图 `KZ1` ↔ 柱表 `KZ1`）。

**必须防的误判**（实测抓到）：`C65H-C16A/2P+V` 出现 **488 次**，
那是施耐德**断路器型号**，不是窗；`C30` 是混凝土强度等级。
"""
from __future__ import annotations

import pytest

from core.model3d.component_mark import parse_component_mark


@pytest.mark.unit
def test_column_marks():
    """22G101：KZ 框架柱 / KZZ 框架核心柱 / XZ 芯柱 / LZ 梁上柱。"""
    for text, kind in [("KZ1", "column"), ("KZ12", "column"),
                       ("KZZ3", "column"), ("XZ2", "column"),
                       ("LZ1", "column"), ("GZ5", "column")]:
        got = parse_component_mark(text)
        assert got is not None and got.kind == kind, f"{text} → {got}"


@pytest.mark.unit
def test_beam_marks_with_span_suffix():
    """梁编号带**跨数后缀**:`KL1(3)` = 1 号框架梁 3 跨,`(3A)` 含悬挑。"""
    for text in ("KL1", "KL1(3)", "KL12(3A)", "WKL2(2B)", "L3", "XL1", "LL5"):
        got = parse_component_mark(text)
        assert got is not None and got.kind == "beam", f"{text} → {got}"


@pytest.mark.unit
def test_wall_and_slab_marks():
    for text, kind in [("Q1", "wall"), ("AZ3", "wall"), ("GBZ2", "wall"),
                       ("YBZ1", "wall"), ("LB1", "slab"), ("WB2", "slab"),
                       ("YXB3", "slab")]:
        got = parse_component_mark(text)
        assert got is not None and got.kind == kind, f"{text} → {got}"


@pytest.mark.unit
def test_door_window_marks():
    """门窗编号:`M1` 序号式、`M1124` 宽高编码式(1100x2400)。"""
    for text, kind in [("M1", "door"), ("M1124", "door"), ("M0824", "door"),
                       ("C1", "window"), ("C1518", "window"),
                       ("FM1", "door"), ("MC1", "door")]:
        got = parse_component_mark(text)
        assert got is not None and got.kind == kind, f"{text} → {got}"


@pytest.mark.unit
def test_electrical_model_numbers_are_rejected():
    """**实测 488 次的误判源**:`C65H-C16A/2P+V` 是断路器型号。"""
    for text in ("C65H-C16A/2P+V", "C65H-C20A/1P+N", "C16A/1P+N",
                 "NSX100F/3P", "iC65N-C10/1P"):
        assert parse_component_mark(text) is None, text


@pytest.mark.unit
def test_material_grades_are_rejected():
    """`C30` 混凝土强度、`M10` 砂浆强度 —— **不是构件编号**。"""
    for text in ("C30", "C25", "C35", "C40", "C50", "M10", "M7.5", "M5"):
        assert parse_component_mark(text) is None, text


@pytest.mark.unit
def test_rebar_specs_are_rejected():
    """钢筋规格 `C12`/`A8`(HRB400 简写)不是构件编号。"""
    for text in ("C12@200", "A8@150", "C20@100", "2C25"):
        assert parse_component_mark(text) is None, text


@pytest.mark.unit
def test_plain_text_and_empty():
    for text in ("", None, "平面图", "标高", "1:100", "±0.000", "KZ", "123"):
        assert parse_component_mark(text) is None, text


# ── 真实数据暴露的四类误判（两个工程实测）──────────────────────

@pytest.mark.unit
def test_sequence_zero_is_rejected():
    """**实测误判**:`M0` / `Q0` —— 构件编号从 **1** 起,没有 0 号。"""
    for text in ("M0", "Q0", "KZ0", "LB0", "C0"):
        assert parse_component_mark(text) is None, text


@pytest.mark.unit
def test_lowercase_is_rejected():
    """**实测误判**:`q35` —— 平法编号在图上是**大写**;
    小写多是图层名或代号,不该靠 `.upper()` 强行认下。"""
    for text in ("q35", "kz1", "kl2(3)", "lb1"):
        assert parse_component_mark(text) is None, text


@pytest.mark.unit
def test_structural_sequence_upper_bound():
    """**实测误判**:`L1200` 更像尺寸(长 1200mm)、`LB123`/`C769` 序号过大。

    结构构件序号实际很少过百,取 **999** 作硬上限;
    四位数几乎必然是尺寸或宽高编码,不是结构构件序号。
    """
    for text in ("L1200", "KZ1234", "LB1441", "Q9999"):
        assert parse_component_mark(text) is None, text
    # 三位以内照常
    assert parse_component_mark("KZ12") is not None
    assert parse_component_mark("L999") is not None


@pytest.mark.unit
def test_door_window_four_digit_size_code_still_allowed():
    """**门窗例外**:`M1124` 是宽高编码(1100x2400),四位合法。

    但要落在合理范围:宽高各两位、且都 >= 04(0.4 米)。

    序号式则限 **199** —— 一个工程的门窗**种类**可能上百不会近千,
    实测 `C769` 落在区间外(更像尺寸或别的编码)。
    **这是经验阈值不是国标**:定高放进噪声,定低漏掉超大项目,取 199 兼顾。
    """
    for text in ("M1124", "M0824", "C1518", "C1215"):
        assert parse_component_mark(text) is not None, text
    for text in ("C769", "M0000", "C0102"):
        assert parse_component_mark(text) is None, text


@pytest.mark.unit
def test_steel_grades_are_rejected():
    """**实测误判**:`Q235` 是钢材牌号(GB/T 700 碳素结构钢 /
    GB/T 1591 低合金高强度结构钢),不是剪力墙编号。

    与 `C30` 混凝土、`M10` 砂浆同类 —— **材料牌号与构件编号形态相同**,
    只能靠取值集合分开。
    """
    for text in ("Q195", "Q215", "Q235", "Q275", "Q345", "Q355",
                 "Q390", "Q420", "Q460"):
        assert parse_component_mark(text) is None, text
    # 真墙编号不受影响
    for text in ("Q1", "Q2", "Q8", "Q12"):
        assert parse_component_mark(text) is not None, text
