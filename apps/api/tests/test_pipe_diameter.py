"""管道公称直径识别（GB/T 1047《管道元件 DN 的定义和选用》）。

**发现经过**：本想做会审清单里的「系统编号」（19 条），
查机电图纸高频编号时发现真正大量出现的是**管径**：

    DN100  178 次    DN150  163 次    DN50  155 次
    DN25   150 次    DN32   145 次

而管径此前是**硬编码**的（见 CLAUDE.md「构件截面表替换硬编码梁高/板厚/管径」）——
图上白纸黑字标着 DN100，模型里却用估算值。

`DN` = Nominal Diameter（公称尺寸），是管道元件的**规格主键**，
不是实测内径也不是外径，而是一个标准化的整数系列。
"""
from __future__ import annotations

import pytest

from core.model3d.pipe_diameter import parse_pipe_diameter


@pytest.mark.unit
def test_standard_dn_values():
    """GB/T 1047 标准系列。"""
    for text, mm in [("DN15", 15.0), ("DN25", 25.0), ("DN50", 50.0),
                     ("DN100", 100.0), ("DN150", 150.0), ("DN300", 300.0)]:
        got = parse_pipe_diameter(text)
        assert got is not None and got.dn_mm == mm, text


@pytest.mark.unit
def test_case_and_spacing_tolerated():
    """`dn100` / `DN 100` / `De100` 都是同一回事。"""
    for text in ("dn100", "DN 100", "Dn100", "DN100mm"):
        got = parse_pipe_diameter(text)
        assert got is not None and got.dn_mm == 100.0, text


@pytest.mark.unit
def test_de_and_d_prefixes():
    """`De` 是**外径**(塑料管常用)、`D` 也见于图上 —— 记下口径来源。"""
    got = parse_pipe_diameter("De110")
    assert got is not None and got.dn_mm == 110.0 and got.kind == "outer"
    got2 = parse_pipe_diameter("DN110")
    assert got2 is not None and got2.kind == "nominal"


@pytest.mark.unit
def test_non_standard_values_are_kept_but_flagged():
    """**非标值照收但标记** —— 判不出不等于不存在,
    图上真写了 `DN137` 就该留档,由人去判是不是笔误。"""
    got = parse_pipe_diameter("DN137")
    assert got is not None and got.dn_mm == 137.0
    assert not got.is_standard
    assert parse_pipe_diameter("DN100").is_standard


@pytest.mark.unit
def test_implausible_sizes_rejected():
    """**尺寸要合理** —— 管道不会是 0 或几米粗。"""
    for text in ("DN0", "DN5000", "DN99999"):
        assert parse_pipe_diameter(text) is None, text


@pytest.mark.unit
def test_not_a_diameter():
    """**形近串不收**:`DNA`、门编号 `M1124`、断路器 `C65H`。"""
    for text in ("DNA", "M1124", "C65H", "DN", "", None, "标高", "DN-"):
        assert parse_pipe_diameter(text) is None, text


@pytest.mark.unit
def test_de_series_has_its_own_standard():
    """**实测疏漏**:`De75` 出现 70 次被标成非标 ——
    塑料管外径有**独立于 DN 的标准系列**(GB/T 13663 给水用聚乙烯管材)。

    DN 系列:…40 / 50 / 65 / 80 / 100…
    De 系列:…40 / 50 / 63 / 75 / 90 / 110…   <- 63/75/90/110 是 De 独有

    用 DN 的表去判 De,会把整条 De 系列判成非标。
    """
    for value in (20, 25, 32, 40, 50, 63, 75, 90, 110, 160):
        got = parse_pipe_diameter(f"De{value}")
        assert got is not None and got.is_standard, f"De{value}"


@pytest.mark.unit
def test_dn_series_unaffected():
    """DN 用自己的表 —— `DN65`/`DN80` 是 DN 独有,De 里没有。"""
    for value in (65, 80, 125, 200):
        got = parse_pipe_diameter(f"DN{value}")
        assert got is not None and got.is_standard, f"DN{value}"
    # De 里没有 65/80，标为非标是对的
    assert not parse_pipe_diameter("De65").is_standard
