"""从图纸说明里抽取规范引用。

**需求 4**：「总说明里提到的规范**必须下载到本地**，不断积累成规范库，
实时对比更新最新版本，作为建模审图基础数据库之一，和总说明一起指导工作」。

要做到这件事，先得知道**说明到底引用了哪些规范**——这也把本轮两个交付
连了起来：56 万字的成篇说明，和 31 本入库的规范。
"""
import pytest


# ── 抽取 ──────────────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.parametrize("text,want", [
    ("应符合《混凝土结构设计规范》GB 50010-2010 的规定", "GB 50010-2010"),
    ("按GB50011-2010执行", "GB 50011-2010"),
    ("详见 GB/T 50001-2017", "GB/T 50001-2017"),
    ("依据 JGJ 130-2011 施工", "JGJ 130-2011"),
    ("符合《建筑防火通用规范》GB55037-2022", "GB 55037-2022"),
    ("按CJJ 1-2008 的规定", "CJJ 1-2008"),
])
def test_standard_numbers_are_extracted_and_normalised(text, want):
    """**归一化是关键**：说明里写 `GB50010-2010`（无空格）、
    库里存 `GB 50010-2010`，不归一就永远匹配不上。"""
    from services.regulation_reference import extract_references

    refs = extract_references(text)
    assert want in [r["std_no"] for r in refs]


@pytest.mark.unit
def test_year_is_optional():
    """说明里常只写编号不写年份——不能因此漏掉。"""
    from services.regulation_reference import extract_references

    refs = extract_references("应满足 GB 50016 的要求")
    assert refs[0]["std_no"] == "GB 50016"
    assert refs[0]["year"] is None


@pytest.mark.unit
def test_title_in_book_marks_is_captured():
    """书名号里的名称一并带出——库里按名称也能对上。"""
    from services.regulation_reference import extract_references

    refs = extract_references("应符合《混凝土结构设计规范》GB 50010-2010")
    assert refs[0]["title"] == "混凝土结构设计规范"


@pytest.mark.unit
def test_duplicates_within_one_text_are_merged():
    """一篇说明里同一本规范会被引用多次——去重后记出现次数。"""
    from services.regulation_reference import extract_references

    refs = extract_references(
        "按 GB 50010-2010 配筋；锚固长度按 GB50010-2010 表 8.3.1")
    assert len(refs) == 1
    assert refs[0]["count"] == 2


@pytest.mark.unit
@pytest.mark.parametrize("noise", [
    "轴号 1-A 与 2-B",
    "标高 -2.350",
    "构件 KZ1 (3A)",
    "管径 DN100",
    "图号 A-201555010",
    "混凝土 C30-C50",
])
def test_non_standard_strings_are_not_extracted(noise):
    """图上到处是「字母-数字」，误抽一条就往规范库里塞一个不存在的标准。"""
    from services.regulation_reference import extract_references

    assert extract_references(noise) == []


# ── 与规范库比对 ──────────────────────────────────────────────

@pytest.mark.unit
def test_reference_matches_library_ignoring_spacing():
    """`GB50010-2010`（说明）↔ `GB 50010-2010`（库）必须算命中。"""
    from services.regulation_reference import match_against_library

    result = match_against_library(
        [{"std_no": "GB 50010-2010", "title": None, "year": "2010", "count": 1}],
        [{"std_no": "GB50010-2010", "title": "混凝土结构设计规范"}])
    assert result["matched"][0]["std_no"] == "GB 50010-2010"
    assert result["missing"] == []


@pytest.mark.unit
def test_missing_standards_are_reported():
    """**这才是需求要的产物**：说明提到但库里没有的，要列出来去补。"""
    from services.regulation_reference import match_against_library

    result = match_against_library(
        [{"std_no": "GB 50016-2014", "title": None, "year": "2014", "count": 3},
         {"std_no": "GB 50010-2010", "title": None, "year": "2010", "count": 1}],
        [{"std_no": "GB 50010-2010", "title": "混凝土结构设计规范"}])
    assert [m["std_no"] for m in result["missing"]] == ["GB 50016-2014"]


@pytest.mark.unit
def test_version_mismatch_is_flagged_not_counted_as_missing():
    """库里有同一本但**年份不同** —— 这是「实时对比更新最新版本」的入口，
    既不能当命中（版本不对），也不能当缺失（书是有的）。"""
    from services.regulation_reference import match_against_library

    result = match_against_library(
        [{"std_no": "GB 50010-2010", "title": None, "year": "2010", "count": 1}],
        [{"std_no": "GB 50010-2015", "title": "混凝土结构设计规范"}])
    assert result["matched"] == [] and result["missing"] == []
    assert result["version_mismatch"][0]["library_std_no"] == "GB 50010-2015"


@pytest.mark.unit
def test_missing_sorted_by_citation_count():
    """按被引用次数排序——先补引用最多的那本。"""
    from services.regulation_reference import match_against_library

    result = match_against_library(
        [{"std_no": "GB 1", "title": None, "year": None, "count": 1},
         {"std_no": "GB 2", "title": None, "year": None, "count": 9}], [])
    assert [m["std_no"] for m in result["missing"]] == ["GB 2", "GB 1"]


@pytest.mark.unit
def test_ocr_truncated_number_without_year_is_rejected():
    """**实测**：真实说明里抽出 `GB 5`（8 次，标着《建筑抗震设计规范》）——
    原文是 `GB 50011`，OCR 把 `0` 认成字母 `O` 后编号被截断。

    没有年份佐证的超短编号一律丢弃：往规范库塞一个不存在的标准，
    比漏掉一条更糟——它会一直挂在「待下载」清单上误导人。
    """
    from services.regulation_reference import extract_references

    assert extract_references("应符合《建筑抗震设计规范》GB 5OO11 的规定") == []


@pytest.mark.unit
def test_short_number_with_year_is_kept():
    """`CJJ 1-2008` 是真实存在的——有年份佐证就不该被上一条误杀。"""
    from services.regulation_reference import extract_references

    refs = extract_references("按 CJJ 1-2008 的规定")
    assert refs[0]["std_no"] == "CJJ 1-2008"


# ── 带年份/不带年份的合并 ──────────────────────────────────────

@pytest.mark.unit
def test_yearless_reference_merges_into_the_dated_one():
    """**实测**：同一篇说明里 `GB 50204`（7 次）与 `GB 50204-2015`（4 次）
    分列两行——同一本规范被拆成两条，补库清单虚长，计数也散了。

    有年份的那条是更完整的信息，无年份的并进去。
    """
    from services.regulation_reference import consolidate_references

    merged = consolidate_references([
        {"std_no": "GB 50204", "title": None, "year": None, "count": 7},
        {"std_no": "GB 50204-2015", "title": "混凝土结构工程施工质量验收规范",
         "year": "2015", "count": 4},
    ])
    assert len(merged) == 1
    assert merged[0]["std_no"] == "GB 50204-2015"
    assert merged[0]["count"] == 11
    assert merged[0]["title"] == "混凝土结构工程施工质量验收规范"


@pytest.mark.unit
def test_two_different_years_stay_separate():
    """同一编号的两个版本必须保留——「实时对比更新最新版本」要靠它。"""
    from services.regulation_reference import consolidate_references

    merged = consolidate_references([
        {"std_no": "GB 50010-2002", "title": None, "year": "2002", "count": 1},
        {"std_no": "GB 50010-2010", "title": None, "year": "2010", "count": 6},
    ])
    assert len(merged) == 2


@pytest.mark.unit
def test_yearless_with_two_candidate_years_goes_to_the_newest():
    """无年份的引用并到**最新版本**——说明通常指现行版。"""
    from services.regulation_reference import consolidate_references

    merged = consolidate_references([
        {"std_no": "GB 50010", "title": None, "year": None, "count": 5},
        {"std_no": "GB 50010-2002", "title": None, "year": "2002", "count": 1},
        {"std_no": "GB 50010-2010", "title": None, "year": "2010", "count": 6},
    ])
    by_no = {m["std_no"]: m for m in merged}
    assert by_no["GB 50010-2010"]["count"] == 11
    assert by_no["GB 50010-2002"]["count"] == 1
    assert "GB 50010" not in by_no


@pytest.mark.unit
def test_yearless_alone_is_kept_as_is():
    """没有带年份的同伴时原样保留——不能因为信息不全就丢掉。"""
    from services.regulation_reference import consolidate_references

    merged = consolidate_references(
        [{"std_no": "GB 50981", "title": None, "year": None, "count": 7}])
    assert merged[0]["std_no"] == "GB 50981" and merged[0]["count"] == 7


@pytest.mark.unit
def test_implausible_year_is_dropped_not_kept_as_a_version():
    """**实测**：清单里出现 `GB 50010-9428`（6 次）——OCR 把年份认花了。

    假年份比没有年份更糟：它会被当成一个真实存在的版本，
    既挂在补库清单上，又让真正的 `GB 50010-2010` 合并不进来。
    """
    from services.regulation_reference import extract_references

    refs = extract_references("按 GB 50010-9428 配筋")
    assert [r["std_no"] for r in refs] == ["GB 50010"]
    assert refs[0]["year"] is None


@pytest.mark.unit
@pytest.mark.parametrize("year", ["1985", "2010", "2022"])
def test_plausible_years_are_kept(year):
    from services.regulation_reference import extract_references

    refs = extract_references(f"按 GB 50010-{year} 配筋")
    assert refs[0]["year"] == year


# ── 项目级覆盖 API ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_project_coverage_reports_missing_worklist():
    """整项目的产物就是需求要的清单：说明引用了什么、库里缺什么。"""
    from services.regulation_reference import project_regulation_coverage

    class DB:
        async def fetch_all(self, sql, params=None):
            if "drawing_extracted_info" in sql:
                return [{"content": "应符合《混凝土结构设计规范》GB 50010-2010 "
                                    "及 GB 50016-2014 的规定"},
                        {"content": "再次引用 GB50010-2010"}]
            return [{"std_no": "GB 50010-2010", "title": "混凝土结构设计规范"}]

    result = await project_regulation_coverage(DB(), "p1")
    assert result["summary"] == {
        "spec_blocks": 2, "referenced": 2, "citations": 3,
        "library_books": 1, "matched": 1, "missing": 1, "version_mismatch": 0}
    assert result["missing"][0]["std_no"] == "GB 50016-2014"
    assert result["matched"][0]["count"] == 2


@pytest.mark.unit
def test_title_only_attaches_within_the_same_line():
    """**实测**：`GB 50010-2010` 被标成「地基处理技术规范」——
    实际是《混凝土结构设计规范》。说明里书名与编号常分行排（两栏版面
    尤其如此），跨行取最近的书名号就会张冠李戴。

    补库清单上挂错书名会直接让人去下错的规范，宁可不写。
    """
    from services.regulation_reference import extract_references

    refs = extract_references("《建筑地基处理技术规范》JGJ 79-2012\nGB 50010-2010")
    by_no = {r["std_no"]: r for r in refs}
    assert by_no["JGJ 79-2012"]["title"] == "建筑地基处理技术规范"
    assert by_no["GB 50010-2010"]["title"] is None


@pytest.mark.unit
def test_same_line_title_still_attaches():
    from services.regulation_reference import extract_references

    refs = extract_references("应符合《混凝土结构设计规范》GB 50010-2010 的规定")
    assert refs[0]["title"] == "混凝土结构设计规范"


@pytest.mark.unit
def test_title_attaches_across_full_width_brackets():
    """**真实写法是「《名称》（GB50010-2010）」**——全角括号把书名与编号
    隔开，不容纳它就一本书名都取不到。"""
    from services.regulation_reference import extract_references

    refs = extract_references("应符合《混凝土结构设计规范》（GB50010-2010）的规定")
    assert refs[0]["title"] == "混凝土结构设计规范"


@pytest.mark.unit
def test_full_width_hyphen_in_year_is_recognised():
    """**实测**：`（GB50204－2015）` 用的是全角连字符 `－`(U+FF0D)，
    只认半角时年份丢失，`GB 50204` 与 `GB 50204-2015` 被拆成两条。"""
    from services.regulation_reference import extract_references

    refs = extract_references("《混凝土结构工程施工质量验收规范》（GB50204－2015）")
    assert refs[0]["std_no"] == "GB 50204-2015"


@pytest.mark.unit
def test_title_does_not_leak_across_a_newline():
    """`$` 在 Python 里**也匹配结尾换行之前**——用它写「不得跨行」根本没生效，
    实测两栏对照表里 `GB 50010-2010` 仍被贴上上一行的《地基处理技术规范》。
    """
    from services.regulation_reference import extract_references

    refs = extract_references(
        "《基坑工程施工监测规程》\nDG/TJ08-2001-2006\n《地基处理技术规范》\nGB 50010-2010")
    by_no = {r["std_no"]: r for r in refs}
    assert by_no["GB 50010-2010"]["title"] is None
