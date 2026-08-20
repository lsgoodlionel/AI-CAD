"""规范引用的列级配对。

**实测未解决项（§8.34）**：规范引用常排成两栏对照表，
左栏书名、右栏编号：

    《基坑工程施工监测规程》     DG/TJ08-2001-2006
    《地基处理技术规范》         JGJ 79-2012
    《混凝土结构设计规范》       GB 50010-2010

按栏重建会把两列串成一列，于是「最近的书名」跨了行——
实测 `GB 50010-2010` 被贴上上一行的《地基处理技术规范》
（实为《混凝土结构设计规范》）。**补库清单上挂错书名会让人去下错的规范。**

当时的处置是「只在同一行才关联书名，取不到就留空」——
保守但丢信息。用 x 坐标聚类做列级配对能把这些书名找回来。
"""
import pytest


def _t(text, x, y):
    return {"text": text, "x": float(x), "y": float(y)}


@pytest.mark.unit
def test_two_column_table_is_paired_by_row():
    """左栏书名、右栏编号，**按 y 对齐配对**而不是按阅读顺序。"""
    from services.regulation_reference import pair_references_by_column

    tokens = [
        _t("《基坑工程施工监测规程》", 100, 200),
        _t("DG/TJ08-2001-2006", 400, 201),
        _t("《地基处理技术规范》", 100, 220),
        _t("JGJ 79-2012", 400, 219),
        _t("《混凝土结构设计规范》", 100, 240),
        _t("GB 50010-2010", 400, 241),
    ]
    pairs = {p["std_no"]: p["title"] for p in pair_references_by_column(tokens)}
    assert pairs["JGJ 79-2012"] == "地基处理技术规范"
    assert pairs["GB 50010-2010"] == "混凝土结构设计规范"


@pytest.mark.unit
def test_row_offset_within_tolerance_still_pairs():
    """两栏的基线不会像素级对齐——几个点的偏差要容忍。"""
    from services.regulation_reference import pair_references_by_column

    pairs = pair_references_by_column([
        _t("《混凝土结构设计规范》", 100, 240),
        _t("GB 50010-2010", 400, 245),
    ])
    assert pairs[0]["title"] == "混凝土结构设计规范"


@pytest.mark.unit
def test_far_apart_rows_do_not_pair():
    """**宁可不配对，也不要配错**：挂错书名会让人去下错的规范。"""
    from services.regulation_reference import pair_references_by_column

    pairs = pair_references_by_column([
        _t("《混凝土结构设计规范》", 100, 100),
        _t("GB 50010-2010", 400, 900),
    ])
    assert pairs[0]["title"] is None


@pytest.mark.unit
def test_same_line_title_and_number_still_pair():
    """一行内写全的写法（`《名称》（GB50010-2010）`）也要认。"""
    from services.regulation_reference import pair_references_by_column

    pairs = pair_references_by_column([
        _t("《混凝土结构设计规范》（GB50010-2010）", 100, 200)])
    assert pairs[0]["std_no"] == "GB 50010-2010"
    assert pairs[0]["title"] == "混凝土结构设计规范"


@pytest.mark.unit
def test_titles_without_a_number_are_not_invented():
    """有书名没编号时不能凭空造一个编号。"""
    from services.regulation_reference import pair_references_by_column

    assert pair_references_by_column([_t("《某某规范》", 100, 200)]) == []


@pytest.mark.unit
def test_tokens_without_position_are_ignored():
    from services.regulation_reference import pair_references_by_column

    assert pair_references_by_column([{"text": "GB 50010-2010"}]) == []
    assert pair_references_by_column(None) == []
