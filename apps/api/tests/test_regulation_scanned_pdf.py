"""扫描件规范必须走 OCR —— 实测强制性通用规范全是扫描件。

**实测**（GB55008-2021《混凝土结构通用规范》，住建部官方 PDF）：

    页数 26 | 每页文本 46 字 | 每页图片 6 张 | 绘图 0
    提取全文仅 1246 字，切分 26 条 —— **100% 是「住房城乡建设部信息公开
    浏览专用」水印**，正文 0 条。

正文全是图像，没有文本层。现有三级链（docling → pymupdf4llm → pymupdf）
都依赖文本层，对这类文件全部落空。

**而 OCR 在规范扫描页上表现优异**（与工程图上的小字完全不同）：

    dpi=200: 31 token, 均置信 **0.997**, 3 秒
    "1结构混凝土强度设计值应按其强度标准值除以材料分项系数确定，
     且材料分项系数取值不应小于1.4；"

规范正文是印刷体、版面规整，所以判据是：**文本层产出过少即判为扫描件，
转 OCR**。
"""
from __future__ import annotations

import pytest

from services.regulation_importer import (
    MIN_TEXT_CHARS_PER_PAGE, is_scanned_pdf, strip_watermark,
)


@pytest.mark.unit
def test_scanned_pdf_detected_by_text_density():
    """**核心判据**:每页文本字数过少 → 扫描件。"""
    assert is_scanned_pdf(text_chars=1246, page_count=26)      # 实测值：48 字/页
    assert not is_scanned_pdf(text_chars=52000, page_count=26)  # 2000 字/页


@pytest.mark.unit
def test_threshold_is_per_page_not_total():
    """**必须按页算** —— 一本 500 页的规范总字数再多，
    每页只有水印仍是扫描件。"""
    assert is_scanned_pdf(text_chars=MIN_TEXT_CHARS_PER_PAGE * 10, page_count=500)
    assert not is_scanned_pdf(text_chars=MIN_TEXT_CHARS_PER_PAGE * 10, page_count=5)


@pytest.mark.unit
def test_zero_pages_is_not_scanned():
    """**页数为 0 不判扫描件** —— 那是解析失败，不该触发 OCR 重试。"""
    assert not is_scanned_pdf(text_chars=0, page_count=0)


@pytest.mark.unit
def test_watermark_lines_are_stripped():
    """**水印必须剥掉** —— 否则每页都混进「浏览专用」，污染条文。"""
    text = ("住房城乡建设部信息公开\n浏览专用\n"
            "4.1.1 结构混凝土强度设计值应按其强度标准值除以材料分项系数确定。\n"
            "浏览专用\n")
    cleaned = strip_watermark(text)
    assert "浏览专用" not in cleaned
    assert "信息公开" not in cleaned
    assert "4.1.1" in cleaned and "材料分项系数" in cleaned


@pytest.mark.unit
def test_normal_text_untouched():
    """**不得误删正文** —— 只剥已知水印行，不做模糊匹配。"""
    text = "3.2.1 本规范适用于混凝土结构的设计与施工。"
    assert strip_watermark(text) == text


@pytest.mark.unit
def test_empty_input():
    assert strip_watermark("") == ""
    assert strip_watermark(None) == ""


# ── 接线：扫描件转 OCR ──────────────────────────────────────

@pytest.mark.unit
def test_ocr_fallback_is_wired_for_scanned_pdf(monkeypatch):
    """**接线用例**:文本层产出过少时,转 OCR 而不是把水印当正文交出去。"""
    import services.regulation_importer as mod

    monkeypatch.setattr(mod, "extract_with_docling", lambda *a, **k: None)
    monkeypatch.setattr(mod, "_extract_text_layer",
                        lambda *a, **k: ("住房城乡建设部信息公开\n浏览专用", 26))
    monkeypatch.setattr(mod, "ocr_pdf_text",
                        lambda *a, **k: "4.1.1 结构混凝土强度设计值应按…")

    got = mod.extract_text_from_pdf(b"%PDF-fake", "GB55008.pdf")
    assert "4.1.1" in got
    assert "浏览专用" not in got


@pytest.mark.unit
def test_text_layer_wins_when_rich_enough(monkeypatch):
    """**文本层够用就不跑 OCR** —— OCR 慢得多,不该无谓触发。"""
    import services.regulation_importer as mod

    rich = "正文" * 3000
    monkeypatch.setattr(mod, "extract_with_docling", lambda *a, **k: None)
    monkeypatch.setattr(mod, "_extract_text_layer", lambda *a, **k: (rich, 3))

    def _boom(*a, **k):
        raise AssertionError("文本层够用时不该调用 OCR")

    monkeypatch.setattr(mod, "ocr_pdf_text", _boom)
    assert mod.extract_text_from_pdf(b"%PDF-fake", "x.pdf") == rich


@pytest.mark.unit
def test_ocr_failure_degrades_to_text_layer(monkeypatch):
    """**OCR 失败不阻断** —— 退回文本层（哪怕只有水印），
    让调用方看到「提取到的就是这些」，而不是整本导入失败。"""
    import services.regulation_importer as mod

    monkeypatch.setattr(mod, "extract_with_docling", lambda *a, **k: None)
    monkeypatch.setattr(mod, "_extract_text_layer", lambda *a, **k: ("水印", 26))

    def _fail(*a, **k):
        raise RuntimeError("OCR 后端不可用")

    monkeypatch.setattr(mod, "ocr_pdf_text", _fail)
    assert mod.extract_text_from_pdf(b"%PDF-fake", "x.pdf") == "水印"
