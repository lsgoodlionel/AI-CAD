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


@pytest.mark.unit
def test_std_no_does_not_swallow_the_file_extension():
    """**实测**：文件名把标准号写在末尾时（`《…》GB55015-2021.pdf`），
    抽出的标准号是 `GB55015-2021.pdf`。

    后果直指需求 4：说明里引用的 `GB 55015-2021` 与库里的
    `GB55015-2021.pdf` 对不上，本该命中的被算成「版本不一致」，
    「哪些规范还没入库」这张清单就是错的。
    """
    from services.regulation_importer import infer_book_metadata

    meta = infer_book_metadata(
        "", "《建筑节能与可再生能源利用通用规范》GB55015-2021.pdf")
    assert meta["std_no"] == "GB55015-2021"


@pytest.mark.unit
def test_resolved_title_is_always_written_back():
    """**我的短路写错了**：原逻辑「解析结果与文件名一致就不回写」，
    假设了书行的现有标题本来就是文件名。

    但 `create_book_from_pdf` 建档时用的是**抽取出的标题**——
    端到端实测那正是序言里的一句话。于是解析结果 == 文件名 → 不回写 →
    书名永远停在那句话上。

    结论：解析出的标题**一律回写**，不做这种「看起来省一次更新」的短路。
    """
    from services.regulation_importer import build_title_update_fields

    fields = build_title_update_fields(
        {"title": "为适应国际技术法规与技术标准通行规则，2016年以来，"},
        filename="端到端测试规范GB55010-2021.pdf")
    assert fields["title"] == "端到端测试规范GB55010-2021"


@pytest.mark.unit
def test_bracketed_filename_still_wins():
    from services.regulation_importer import build_title_update_fields

    fields = build_title_update_fields(
        {"title": "正文里的一句话，很长很长"},
        filename="GB 55023-2022《施工脚手架通用规范》.pdf")
    assert fields["title"] == "GB 55023-2022《施工脚手架通用规范》"


@pytest.mark.unit
def test_no_filename_keeps_a_plausible_extraction():
    from services.regulation_importer import build_title_update_fields

    fields = build_title_update_fields({"title": "施工脚手架通用规范"}, filename="")
    assert fields["title"] == "施工脚手架通用规范"


@pytest.mark.unit
def test_upload_endpoint_applies_the_same_title_guard():
    """**同一条判据不能只在流水线里生效**。

    端到端实测：上传接口用 `infer_book_metadata` 的原始标题建档，
    得到「为适应国际技术法规与技术标准通行规则，2016年以来，」。
    流水线随后虽会更正，但接口的即时响应是错的，
    流水线失败时书名也就永久停在那句话上。

    这与模型侧那条「只给有世界坐标的建筑图开口子」是同一种病：
    **一条只对部分对象生效的判据，多半不是判据，是历史。**
    """
    import inspect

    import routers.regulations as reg

    source = inspect.getsource(reg.create_book_from_pdf)
    assert "resolve_book_title" in source or "build_title_update_fields" in source, \
        "建档路径没有应用标题守卫"
    assert "normalize_std_no" in source, "建档路径没有归一化标准号"
