"""规范知识库的「人机双读」三层。

需求：规范库要**同时满足机器和人类阅读**——
- ① 可阅读的 **PDF 原件**（人看原版排版、图表、签章）
- ② 被识别的**文字版本全文**（人核对识别质量、机器做全文检索）
- ③ 整理处理过的**单个条文**（系统消费：审图、图谱推理、向量检索）

**此前只有第 ③ 层**：31 本书 `file_key` 全为 NULL（导入脚本从本地直读、
没上传 MinIO），全文也从未保留——识别完切成条文就丢了，
于是「识别得对不对」根本无从人工核对。
"""
import pytest


# ── ② 全文留存 ────────────────────────────────────────────────

@pytest.mark.unit
def test_full_text_params_record_provenance():
    """必须记下**这份文字怎么来的**——OCR 出来的和 PDF 文本层直取的
    可信度完全不同，人工核对时要先知道这一点。"""
    from services.regulation_importer import build_full_text_params

    params = build_full_text_params("b1", "第一章 总则\n1.0.1 …", "ocr", 42)
    assert params["book_id"] == "b1"
    assert params["extract_method"] == "ocr"
    assert params["page_count"] == 42
    assert params["text_chars"] == len("第一章 总则\n1.0.1 …")


@pytest.mark.unit
def test_full_text_keeps_original_line_breaks():
    """换行是版面信息——压平了人就没法把文字与 PDF 对照着看。"""
    from services.regulation_importer import build_full_text_params

    text = "第一章\n\n1.0.1 为……\n1.0.2 本规范适用于……"
    assert build_full_text_params("b1", text, "text_layer", 3)["full_text"] == text


@pytest.mark.unit
def test_empty_text_is_stored_as_null_not_empty_string():
    """一个字都没识别出来时存 NULL 而非空串——
    「没识别」和「识别出空白」是两回事，界面要能区分。"""
    from services.regulation_importer import build_full_text_params

    params = build_full_text_params("b1", "   ", "ocr", 1)
    assert params["full_text"] is None and params["text_chars"] == 0


# ── ① PDF 原件 ────────────────────────────────────────────────

@pytest.mark.unit
def test_book_file_key_is_namespaced():
    """规范原件与图纸分开存放——同一桶里混着两类资产，
    权限与清理策略都没法分开做。"""
    from services.regulation_importer import book_file_key

    key = book_file_key("b1", "GB 55023-2022《施工脚手架通用规范》.pdf")
    assert key.startswith("regulations/b1/")
    assert key.endswith(".pdf")


@pytest.mark.unit
def test_file_key_is_safe_for_object_storage():
    """中文书名号、空格、斜杠都要能安全落进对象键。"""
    from services.regulation_importer import book_file_key

    key = book_file_key("b1", "GB/T 50001-2017《房屋建筑制图统一标准》.pdf")
    assert "/" not in key[len("regulations/b1/"):]
    assert " " not in key


@pytest.mark.unit
def test_non_pdf_extension_is_preserved():
    from services.regulation_importer import book_file_key

    assert book_file_key("b1", "规范.docx").endswith(".docx")


@pytest.mark.asyncio
async def test_readable_layers_persist_original_and_full_text(monkeypatch):
    """落第①②层：原件进对象存储、识别全文进库（含来路）。

    此前 31 本书 `file_key` 全为 NULL、全文从未保留——两层都不存在。
    """
    import core.storage as storage
    from services.regulation_importer import _store_readable_layers

    uploaded = {}
    monkeypatch.setattr(storage, "upload_file",
                        lambda data, key, content_type=None: uploaded.update(
                            {"key": key, "size": len(data)}) or key)

    captured = {}

    class DB:
        async def execute(self, sql, params=None):
            captured.update(params or {})

    await _store_readable_layers(DB(), "b1", b"%PDF-1.4 x",
                                 "GB 55023-2022《施工脚手架通用规范》.pdf",
                                 "第一章 总则\n1.0.1 …", "ocr", 42)
    assert uploaded["key"].startswith("regulations/b1/")
    assert captured["file_key"] == uploaded["key"]
    assert captured["extract_method"] == "ocr" and captured["page_count"] == 42
    assert captured["full_text"].startswith("第一章")


@pytest.mark.asyncio
async def test_upload_failure_does_not_block_article_import(monkeypatch):
    """存原件失败**不阻断**条文入库——第③层才是系统消费的关键路径。
    但全文仍要落库，且 `file_key` 留空而非写脏值。"""
    import core.storage as storage
    from services.regulation_importer import _store_readable_layers

    def boom(*a, **kw):
        raise RuntimeError("MinIO 不可达")

    monkeypatch.setattr(storage, "upload_file", boom)
    captured = {}

    class DB:
        async def execute(self, sql, params=None):
            captured.update(params or {})

    await _store_readable_layers(DB(), "b1", b"x", "a.pdf", "正文", "ocr", 1)
    assert captured["file_key"] is None
    assert captured["full_text"] == "正文"
