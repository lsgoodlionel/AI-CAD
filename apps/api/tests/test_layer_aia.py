"""图层名 → 构件类型:AIA 规范支持 —— 第二工程让这条路首次有真实数据。

**背景**:大歌剧院 **0 图层**,Phase C 的 `auto_label` + `layer_class_map`
整条路建好后从未在真实数据上生效。轨道交通 **93.1% 有图层**,
且图元 **100% 带 layer 字段**(实测 51688/51689),图层名遵循
**AIA CAD Layer Guidelines**(国际通用标准,非某工程特有):

    S-BEAM-I        结构-梁
    S-COLS-HATCH    结构-柱
    C-SHET-TTLB     土建-图框-标题块

这让构件识别可以从**几何猜测**升级为**读图层**。

**两个实测缺陷**:

1. AIA 代码被 AutoCAD 的 **xref 绑定前缀**包裹(`PLAN_F01$0$0S-BEAM-I`),
   现有 prefixes 匹配全部落空 —— beam **零命中**,而该图有 4240 个梁图元;
2. **`C-SHET-TTLB`(图框)被判为 window** —— window 规则里的 `C-` 前缀
   (中文「窗」拼音)撞上 AIA 的 **C = Civil 学科代码**。
   这正是 window 命中数异常高达 49670 的原因。
"""
from __future__ import annotations

import pytest

from core.model3d.layer_conventions import classify_by_layer, normalize_layer_name


# ── xref 前缀剥离 ───────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.parametrize("raw,expected", [
    ("PLAN_F01$0$0S-BEAM-I", "S-BEAM-I"),
    ("COLS_F01$0$0S-COLS-HATCH", "S-COLS-HATCH"),
    ("S-F01-L$0$0S-BEAM-TEXT", "S-BEAM-TEXT"),
    ("S-BEAM-I", "S-BEAM-I"),          # 无前缀时原样
    ("", ""),
])
def test_xref_prefix_is_stripped(raw, expected):
    """`$N$N` 是 AutoCAD **xref 绑定**的分隔符(通用约定,非工程特有)。"""
    assert normalize_layer_name(raw) == expected


@pytest.mark.unit
def test_beam_is_recognised_through_the_xref_prefix():
    """**核心用例**:实测 4240 个梁图元此前零命中。"""
    assert classify_by_layer("PLAN_F01$0$0S-BEAM-I") == "beam"
    assert classify_by_layer("S-F01-L$0$0S-BEAM-TEXT") == "beam"


@pytest.mark.unit
def test_column_is_recognised_through_the_xref_prefix():
    assert classify_by_layer("COLS_F01$0$0S-COLS-HATCH") == "column"


@pytest.mark.unit
def test_wall_is_recognised_through_the_xref_prefix():
    assert classify_by_layer("COLS_F01$0$0S-WALL-LINE") == "wall"


# ── AIA 学科代码 C = Civil,不是「窗」──────────────────────────

@pytest.mark.unit
def test_title_block_is_not_a_window():
    """**回归**:`C-SHET-TTLB` 是图框标题块,被判成 window 了。"""
    assert classify_by_layer("C-SHET-TTLB") != "window"


@pytest.mark.unit
def test_real_windows_still_match():
    """不得误伤真窗:AIA 用 `A-GLAZ`/`A-WIND`,中文用「窗」。"""
    assert classify_by_layer("A-GLAZ") == "window"
    assert classify_by_layer("A-WIND-FULL") == "window"
    assert classify_by_layer("建筑-窗") == "window"


@pytest.mark.unit
def test_empty_layer_is_safe():
    """大歌剧院 0 图层 —— 空输入必须安全返回,不能抛也不能瞎猜。"""
    assert classify_by_layer(None) is None
    assert classify_by_layer("") is None
