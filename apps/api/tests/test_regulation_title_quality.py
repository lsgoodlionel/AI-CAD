"""规范书标题与标准号的质量守卫。

**实测暴露**：GB 55023-2022《施工脚手架通用规范》导入后，
书名变成了序言里的一句话「为适应国际技术法规与技术标准通行规则，2016年以来，」——
标准号倒是对的。后果直指需求本身：规范库要能被**总说明里的引用检索到**、
要能**跨版本比对**，而一本叫做半句话的书两样都做不到。
"""
import pytest


# ── 标题 ──────────────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.parametrize("prose", [
    "为适应国际技术法规与技术标准通行规则，2016年以来，",
    "本规范是根据住房和城乡建设部的要求编制的。",
    "1 总则；2 基本规定；3 材料",
])
def test_prose_is_not_a_title(prose):
    """带句读的一律不是标题——正文句子最容易被误抽成标题。"""
    from services.regulation_importer import is_plausible_book_title

    assert not is_plausible_book_title(prose)


@pytest.mark.unit
@pytest.mark.parametrize("title", [
    "施工脚手架通用规范",
    "GB 55023-2022《施工脚手架通用规范》",
    "混凝土结构通用规范",
    "建筑与市政工程抗震通用规范",
])
def test_real_titles_pass(title):
    from services.regulation_importer import is_plausible_book_title

    assert is_plausible_book_title(title)


@pytest.mark.unit
def test_overlong_and_empty_rejected():
    from services.regulation_importer import is_plausible_book_title

    assert not is_plausible_book_title("")
    assert not is_plausible_book_title("   ")
    assert not is_plausible_book_title("规范" * 40)


@pytest.mark.unit
def test_bracketed_filename_title_wins_over_extraction():
    """文件名带《…》时那就是权威标题——抽取结果不得覆盖。

    人工上传的规范文件名是**人写的**，比从正文里猜更可靠。
    """
    from services.regulation_importer import resolve_book_title

    assert resolve_book_title(
        filename_title="GB 55023-2022《施工脚手架通用规范》",
        extracted="为适应国际技术法规与技术标准通行规则，2016年以来，",
    ) == "GB 55023-2022《施工脚手架通用规范》"


@pytest.mark.unit
def test_extraction_used_when_filename_is_uninformative():
    """文件名没有书名号时，合格的抽取结果可以用。"""
    from services.regulation_importer import resolve_book_title

    assert resolve_book_title(
        filename_title="scan_001", extracted="施工脚手架通用规范",
    ) == "施工脚手架通用规范"
    # 抽取不合格就保留原文件名，不写脏值
    assert resolve_book_title(
        filename_title="scan_001", extracted="本规范共分 8 章，主要内容包括：",
    ) == "scan_001"


# ── 标准号 ────────────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.parametrize("raw,want", [
    ("GB55023-2022", "GB 55023-2022"),
    ("GB 55023-2022", "GB 55023-2022"),
    ("GB/T50001-2017", "GB/T 50001-2017"),
    ("gb 50010-2010", "GB 50010-2010"),
    ("JGJ  130-2011", "JGJ 130-2011"),
])
def test_std_no_normalised_for_cross_reference(raw, want):
    """总说明里写的是 `GB 55023-2022`（带空格），而抽取出来常是
    `GB55023-2022`。不归一化，引用就永远匹配不上库里的书。"""
    from services.regulation_importer import normalize_std_no

    assert normalize_std_no(raw) == want


@pytest.mark.unit
def test_std_no_unknown_shape_kept_as_is():
    """认不出的形状原样保留——宁可不动，也不要猜着改。"""
    from services.regulation_importer import normalize_std_no

    assert normalize_std_no("企业标准 Q/ABC 001") == "企业标准 Q/ABC 001"
    assert normalize_std_no(None) is None


@pytest.mark.unit
def test_strip_file_extension():
    from services.regulation_importer import strip_file_extension

    assert strip_file_extension("GB 55023-2022《施工脚手架通用规范》.pdf") \
        == "GB 55023-2022《施工脚手架通用规范》"
    assert strip_file_extension("/tmp/regs/a.PDF") == "a"
    assert strip_file_extension("noext") == "noext"


@pytest.mark.unit
def test_mandatory_check_uses_resolved_title_not_raw_extraction():
    """强条判定只能从**书名**得出，而书名要用解析后的那个。

    实测：抽取把序言半句话当书名，`is_mandatory_standard()` 认不出，
    GB 55023 通用规范 75 条里 52 条被判成非强条——
    而这本书按定义全文强制。文件名里明明写着《施工脚手架通用规范》。
    """
    from services.regulation_importer import (
        is_mandatory_standard, resolve_book_title, strip_file_extension)

    filename_title = strip_file_extension("GB 55023-2022《施工脚手架通用规范》.pdf")
    raw_extraction = "为适应国际技术法规与技术标准通行规则，2016年以来，"

    assert not is_mandatory_standard(raw_extraction)          # 认不出
    assert is_mandatory_standard(
        resolve_book_title(filename_title, raw_extraction))   # 解析后认得出
