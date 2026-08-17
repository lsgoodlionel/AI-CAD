"""一图多视图 ≠ §8.0.5 分区。

**实测错误**:`A-20-02A 南立面图` 一张纸画了两幅立面(南立面图一/二),
系统把第二幅当成独立分区、按 §8.0.5 规则**从 1 重新编号**:

| 分区 | 系统推导 | 图纸真值 |
|---|---|---|
| 0 | `1`~`13` | `1-1`~`1-13` ✅ |
| 1 | **`1`~`12`** | **`1-13`~`1-24`** ❌ 整段错 |

两幅在 `1-13` 处搭接重复一根轴线,是**同一分区的连续序列**。

**判别指纹(实测)**:

| 图 | 各区的数字/字母轴线数 | 判定 |
|---|---|---|
| A-01-02A 正交轴网定位图 | 24/**15**、16/**15**、15/**14** | 真分区(**双向**) |
| A-20-02A 南立面图 | 13/**0**、12/**0** | 分幅(**单向**) |
| A-30-07A 剖面图 | 7/**0**、6/**0** | 分幅(**单向**) |

立面/剖面只有**一个方向**的轴线投影 —— §8.0.5 的分区是平面上的分区,
必然双向。**单向就是分幅的指纹。**
"""
from __future__ import annotations

import pytest

from services.multi_view_split import (
    SPLIT_VIEW_WARNING, is_split_view, order_split_zones, renumber_split_views,
)


def _zone(index: int, numeric: int, alpha: int,
          extent: tuple[float, float, float, float]) -> dict:
    return {"index": index, "numeric_axes": numeric, "alpha_axes": alpha,
            "extent": list(extent), "zone_label": None}


# ── 判别 ────────────────────────────────────────────────────────

@pytest.mark.unit
def test_elevation_with_only_numeric_bands_is_a_split_view():
    """实测 A-20-02A:13/0 + 12/0。"""
    zones = [_zone(0, 13, 0, (877, 1203, 2855, 1204)),
             _zone(1, 12, 0, (304, 2254, 2406, 2255))]
    assert is_split_view(zones) is True


@pytest.mark.unit
def test_section_with_only_numeric_bands_is_a_split_view():
    """实测 A-30-07A:7/0 + 6/0。"""
    assert is_split_view([_zone(0, 7, 0, (711, 2201, 2526, 2201)),
                          _zone(1, 6, 0, (778, 1051, 2488, 1051))]) is True


@pytest.mark.unit
def test_real_plan_zones_are_not_split_views():
    """实测 A-01-02A:24/15、16/15、15/14 —— **双向**,是真分区。"""
    zones = [_zone(0, 24, 15, (846, 1611, 2606, 2179)),
             _zone(1, 16, 15, (1607, 187, 2697, 965)),
             _zone(2, 15, 14, (1505, 778, 2572, 1575))]
    assert is_split_view(zones) is False


@pytest.mark.unit
def test_single_zone_is_never_a_split_view():
    """一个分区谈不上分幅。"""
    assert is_split_view([_zone(0, 13, 0, (0, 0, 100, 1))]) is False


@pytest.mark.unit
def test_mixed_zones_are_not_split_views():
    """只要有一个区是双向的,就不能整体当分幅处理。"""
    assert is_split_view([_zone(0, 13, 0, (0, 0, 100, 1)),
                          _zone(1, 12, 8, (0, 50, 100, 80))]) is False


@pytest.mark.unit
def test_alpha_only_zones_also_count():
    """字母向单向同理(横向立面)。"""
    assert is_split_view([_zone(0, 0, 9, (0, 0, 100, 1)),
                          _zone(1, 0, 7, (0, 50, 100, 51))]) is True


@pytest.mark.unit
def test_empty_input():
    assert is_split_view([]) is False


# ── 排序 ────────────────────────────────────────────────────────

@pytest.mark.unit
def test_zones_are_ordered_top_to_bottom():
    """立面分幅按**图面自上而下**排列(第一幅在上)。

    实测 A-20-02A:分区0 在 y≈1203(上),分区1 在 y≈2254(下)。
    PDF 坐标 y 向下增大,所以 y 小的在上、排在前。
    """
    zones = [_zone(0, 12, 0, (304, 2254, 2406, 2255)),   # 故意乱序
             _zone(1, 13, 0, (877, 1203, 2855, 1204))]
    assert [z["index"] for z in order_split_zones(zones)] == [1, 0]


@pytest.mark.unit
def test_zones_on_the_same_row_are_ordered_left_to_right():
    """同一行的多幅按左→右(§8.0.3 横向从左至右)。"""
    zones = [_zone(0, 4, 0, (2842, 982, 3025, 983)),
             _zone(1, 4, 0, (732, 982, 1286, 983))]
    assert [z["index"] for z in order_split_zones(zones)] == [1, 0]


# ── 连续编号 ────────────────────────────────────────────────────

@pytest.mark.unit
def test_renumber_makes_a_continuous_sequence_with_overlap():
    """**核心用例**:两幅在搭接处重复一根轴线。

    实测 A-20-02A:上幅 13 条、下幅 12 条,真值是 `1`~`24` 共 24 条,
    检出 25 个圈 —— 差 1,正是搭接那一根。
    """
    zones = [_zone(0, 13, 0, (877, 1203, 2855, 1204)),
             _zone(1, 12, 0, (304, 2254, 2406, 2255))]
    got = renumber_split_views(zones, overlap=1)
    assert got[0]["start"] == 1 and got[0]["end"] == 13
    assert got[1]["start"] == 13 and got[1]["end"] == 24
    assert got[1]["end"] == 24, "两幅合计应为 24 条唯一轴线"


@pytest.mark.unit
def test_renumber_without_overlap():
    zones = [_zone(0, 13, 0, (877, 1203, 2855, 1204)),
             _zone(1, 12, 0, (304, 2254, 2406, 2255))]
    got = renumber_split_views(zones, overlap=0)
    assert got[1]["start"] == 14 and got[1]["end"] == 25


@pytest.mark.unit
def test_renumber_declares_the_assumed_overlap():
    """搭接根数是**假设**,必须记下来让人能核。"""
    got = renumber_split_views(
        [_zone(0, 13, 0, (0, 0, 100, 1)), _zone(1, 12, 0, (0, 50, 100, 51))],
        overlap=1)
    assert all(z["overlap_assumed"] == 1 for z in got[1:])


@pytest.mark.unit
def test_renumber_is_a_noop_for_real_zones():
    """真分区不该被串号 —— 各自从 1 开始才对(§8.0.5)。"""
    zones = [_zone(0, 24, 15, (846, 1611, 2606, 2179)),
             _zone(1, 15, 14, (1505, 778, 2572, 1575))]
    assert renumber_split_views(zones, overlap=1) == []


# ── 警告 ────────────────────────────────────────────────────────

@pytest.mark.unit
def test_warning_text_explains_the_situation():
    """警告要说清楚**为什么**,不能只说「疑似」。"""
    assert "分幅" in SPLIT_VIEW_WARNING
    assert "8.0.5" in SPLIT_VIEW_WARNING


# ── 「同一视图分幅」vs「多个独立视图」────────────────────────────

@pytest.mark.unit
def test_numbering_is_a_suggestion_not_a_conclusion():
    """串号只是**建议**,不会改写轴号本身。

    **实测限制**:`A-30-07A` 一页画了**四个独立剖面**
    (7-7/8-8/9-9/10-10),它们各有各的轴号,**不该串**。
    而 `A-20-02A` 是同一立面分两幅,**应该串**。

    两者的几何形态一样(都是单向多区),**光靠轴网分不开**——
    要分开得读各幅的图名(「南立面图(二)」vs「8-8剖面图」),
    那是 OCR 的事。

    所以本模块**只给建议、不改轴号**:`renumber_split_views` 的产出
    存在 `split_view_numbering` 里供人核,轴号仍按各幅各自从 1 编。
    """
    from services.axis_recognition import recognize

    circles = ([{"cx": 400.0 + i * 90.0, "cy": 600.0, "diameter_pt": 28.0}
                for i in range(6)]
               + [{"cx": 500.0 + i * 90.0, "cy": 1600.0, "diameter_pt": 28.0}
                  for i in range(5)])
    got = recognize(circles, strokes=[], segments=[], page_w=3370.0,
                    page_h=2384.0, read_text=lambda leader: [])
    assert got["is_split_view"] is True
    assert got["split_view_numbering"], "要给出建议"
    # **轴号本身没有被串号改写** —— 各幅仍各自从 1 开始
    labels = [a["label"] for a in got["axes"]]
    assert labels.count("1") == 2, "两幅各有一个 `1`,说明未强行串号"


@pytest.mark.unit
def test_warning_says_the_numbering_needs_confirmation():
    """警告要讲明串号需人工确认,以及它对多独立视图不适用。"""
    assert "建议" in SPLIT_VIEW_WARNING
    assert "独立" in SPLIT_VIEW_WARNING
