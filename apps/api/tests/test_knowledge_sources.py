"""识图标准资料知识化 —— 清单 / 抽取 / Markdown 的单元测试。

不依赖原始 PDF（那些是本机资料，CI 上没有），只测**纯逻辑**：
清单自洽性、文本清洗、侧边栏判别、Markdown 的诚实性约束。
"""
from __future__ import annotations

import pytest

from core.knowledge import markdown_writer, source_registry as sr, text_extract as te


# ── 清单自洽 ──────────────────────────────────────────────

@pytest.mark.unit
def test_keys_are_unique():
    keys = [s.key for s in sr.SOURCES]
    assert len(keys) == len(set(keys))


@pytest.mark.unit
def test_std_no_unique_where_present():
    """图集号是入库主键（`regulation_books.std_no` 唯一），重了会互相覆盖。"""
    numbers = [s.std_no for s in sr.SOURCES if s.std_no]
    assert len(numbers) == len(set(numbers))


@pytest.mark.unit
def test_every_source_declares_how_it_was_identified():
    """扫描件的书名是 OCR 封面读出来的，**必须留痕**：
    文件名 `std_191290.pdf` 完全不含书名线索。"""
    for s in sr.SOURCES:
        assert s.identified_by.strip(), s.key


@pytest.mark.unit
def test_extract_methods_are_known():
    for s in sr.SOURCES:
        assert s.extract_method in ("ocr", "text_layer", "epub"), s.key


@pytest.mark.unit
def test_superseded_links_point_into_the_registry():
    """`superseded_by` 指向的必须是清单里真实存在的一本。"""
    by_std = {s.std_no for s in sr.SOURCES if s.std_no}
    for s in sr.SOURCES:
        if s.superseded_by:
            assert s.superseded_by in by_std, s.key


@pytest.mark.unit
def test_filter_and_sort_are_stable():
    ordered = sr.all_sources(max_priority=2)
    assert ordered == sorted(ordered, key=lambda s: (s.priority, s.key))
    assert all(s.priority <= 2 for s in ordered)


# ── 文本清洗 ──────────────────────────────────────────────

@pytest.mark.unit
def test_sanitize_strips_control_chars_but_keeps_newlines():
    """**实测**：闾成德书的文本层夹着 7 个 NUL，一路穿到入库才炸
    （`invalid byte sequence for encoding "UTF8": 0x00`）。"""
    assert te.sanitize("A\x00B\x07C") == "ABC"
    assert te.sanitize("行一\n行二\t尾") == "行一\n行二\t尾"
    assert te.sanitize("") == ""


# ── 侧边栏（图集竖排章节条）──────────────────────────────

@pytest.mark.unit
def test_vertical_edge_text_is_split_out_as_sidebar():
    tokens = [
        {"text": "平法制图规则", "bbox": [5, 100, 20, 400]},    # 贴左边、竖排
        {"text": "正文一行", "bbox": [200, 100, 400, 115]},
    ]
    body, side = te._split_sidebar(tokens, page_w=800, page_h=600)
    assert [t["text"] for t in body] == ["正文一行"]
    assert side == ["平法制图规则"]


@pytest.mark.unit
def test_tall_text_in_the_middle_is_not_sidebar():
    """只按高宽比会误伤正文里的窄表格 —— 还必须贴边。"""
    tokens = [{"text": "窄列", "bbox": [390, 100, 405, 400]}]
    body, side = te._split_sidebar(tokens, page_w=800, page_h=600)
    assert side == [] and len(body) == 1


@pytest.mark.unit
def test_sidebar_keeps_only_repeated_entries():
    """章节条上下印多遍；竖排被横读产生的乱码每次都不一样、只出现一次。"""
    got = te._clean_sidebar(["平法制图规则", "平法制图规则", "法特医夫贝污制图"])
    assert got == ["平法制图规则"]


@pytest.mark.unit
def test_single_sidebar_entry_is_kept():
    """只有一条时无从判别复现性 —— 原样保留，不猜。"""
    assert te._clean_sidebar(["总则"]) == ["总则"]


# ── Markdown 的诚实性约束 ────────────────────────────────

def _fake_source(**kw):
    base = dict(key="demo", filename="demo.pdf", std_no="XX-1", title="示例图集",
                kind="atlas", discipline="structure", pages=3,
                extract_method="ocr", identified_by="OCR 封面", priority=1)
    base.update(kw)
    return sr.KnowledgeSource(**base)


@pytest.mark.unit
def test_missing_pages_are_reported_not_filled():
    """缺页要如实标出。**半成品必须看起来像半成品** ——
    用空串补齐会让它看起来像完本。"""
    source = _fake_source()
    pages = [{"index": 0, "text": "第一页正文内容足够长以免被判近空页面文字",
              "confidence": 0.97}]
    stats = markdown_writer.build_stats(source, pages)
    assert stats["missing_pages"] == [1, 2]

    md = markdown_writer.render_markdown(source, pages, stats)
    assert "missing_pages: [1, 2]" in md
    assert "缺 2 页" in md


@pytest.mark.unit
def test_low_confidence_pages_are_named_not_averaged_away():
    source = _fake_source(pages=2)
    pages = [{"index": 0, "text": "清楚的一页" * 8, "confidence": 0.98},
             {"index": 1, "text": "模糊的一页" * 8, "confidence": 0.42}]
    md = markdown_writer.render_markdown(source, pages)
    assert "low_confidence_pages: [1]" in md
    assert "低置信 0.42" in md


@pytest.mark.unit
def test_provenance_note_states_ocr_origin():
    """OCR 得到的文字**不能当规范原文引用** —— 来路必须写在正文最前面。"""
    md = markdown_writer.render_markdown(
        _fake_source(pages=1),
        [{"index": 0, "text": "内容" * 40, "confidence": 0.9}])
    assert "扫描件 OCR" in md and "未经人工逐字校对" in md


@pytest.mark.unit
def test_sidebar_becomes_page_heading():
    md = markdown_writer.render_markdown(
        _fake_source(pages=1),
        [{"index": 0, "text": "正文" * 40, "confidence": 0.99,
          "sidebar": ["平法制图规则"]}])
    assert "〔平法制图规则〕" in md
