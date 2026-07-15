"""D-17 · docling 候选前段离线测试。

覆盖：
  1. docling 未安装（本仓库真实状态）时 `is_available`/`extract_with_docling`
     优雅降级，不抛异常。
  2. 用 ``sys.modules`` 注入假 docling 包，验证「可用」路径下的成功提取、
     空结果降级、异常降级三种分支（不依赖真实安装 docling）。
  3. `services/regulation_importer.py::extract_text_from_pdf` 的优先级链：
     docling 命中直接用；docling 未命中（None）无声落回既有
     pymupdf4llm→pymupdf 降级路径，默认行为不变。

全程离线，不联网、不装重依赖。
"""
from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

import fitz
import pytest

from core.regulation import docling_extract
from services import regulation_importer


@pytest.fixture(autouse=True)
def _reset_docling_singleton():
    """每个测试前后重置模块级单例/负例缓存，避免测试间状态串扰。"""
    docling_extract._converter_singleton = None
    docling_extract._load_failed = False
    sys.modules.pop("docling", None)
    sys.modules.pop("docling.document_converter", None)
    yield
    docling_extract._converter_singleton = None
    docling_extract._load_failed = False
    sys.modules.pop("docling", None)
    sys.modules.pop("docling.document_converter", None)


def _sample_pdf_bytes(text: str = "sample regulation text") -> bytes:
    doc = fitz.open()
    page = doc.new_page(width=200, height=200)
    page.insert_text((20, 20), text)
    return doc.tobytes()


def _install_fake_docling(convert_side_effect):
    """在 sys.modules 注入一个假 docling 包，convert() 行为由调用方控制。"""
    fake_converter_cls = MagicMock(side_effect=lambda: MagicMock(convert=MagicMock(side_effect=convert_side_effect)))

    docling_pkg = types.ModuleType("docling")
    docling_document_converter_mod = types.ModuleType("docling.document_converter")
    docling_document_converter_mod.DocumentConverter = fake_converter_cls
    docling_pkg.document_converter = docling_document_converter_mod

    sys.modules["docling"] = docling_pkg
    sys.modules["docling.document_converter"] = docling_document_converter_mod


# ── docling 真实未安装时的优雅降级（本仓库当前真实状态） ──────────

def test_is_available_false_when_docling_not_installed():
    assert docling_extract.is_available() is False


def test_extract_with_docling_returns_none_when_unavailable():
    result = docling_extract.extract_with_docling(_sample_pdf_bytes(), "GB50010-2010.pdf")
    assert result is None


def test_is_available_caches_negative_result():
    """未安装时第一次探测失败后应缓存负例，第二次调用直接短路返回 False。"""
    assert docling_extract.is_available() is False
    assert docling_extract._load_failed is True
    # 第二次调用不应再尝试 import（缓存命中，行为仍为 False）
    assert docling_extract.is_available() is False


# ── 用注入的假 docling 包验证「可用」路径的三种分支 ──────────────

def test_extract_with_docling_success_path_returns_markdown():
    def _convert(path):
        result = MagicMock()
        result.document.export_to_markdown.return_value = "# 4.2.3 条文标题\n\n条文正文内容"
        return result

    _install_fake_docling(_convert)

    assert docling_extract.is_available() is True
    text = docling_extract.extract_with_docling(_sample_pdf_bytes(), "spec.pdf")
    assert text == "# 4.2.3 条文标题\n\n条文正文内容"


def test_extract_with_docling_empty_markdown_degrades_to_none():
    def _convert(path):
        result = MagicMock()
        result.document.export_to_markdown.return_value = "   "
        return result

    _install_fake_docling(_convert)

    text = docling_extract.extract_with_docling(_sample_pdf_bytes(), "spec.pdf")
    assert text is None


def test_extract_with_docling_conversion_exception_degrades_to_none():
    def _convert(path):
        raise RuntimeError("simulated docling conversion failure")

    _install_fake_docling(_convert)

    text = docling_extract.extract_with_docling(_sample_pdf_bytes(), "spec.pdf")
    assert text is None


def test_extract_with_docling_converter_construction_failure_is_unavailable():
    """DocumentConverter() 构造即失败（如缺权重/环境问题）也应优雅降级。"""
    docling_pkg = types.ModuleType("docling")
    docling_document_converter_mod = types.ModuleType("docling.document_converter")

    def _raise_on_construct():
        raise RuntimeError("simulated construction failure")

    docling_document_converter_mod.DocumentConverter = _raise_on_construct
    docling_pkg.document_converter = docling_document_converter_mod
    sys.modules["docling"] = docling_pkg
    sys.modules["docling.document_converter"] = docling_document_converter_mod

    assert docling_extract.is_available() is False
    assert docling_extract.extract_with_docling(_sample_pdf_bytes()) is None


# ── services/regulation_importer.py 优先级链接线 ──────────────────

def test_extract_text_from_pdf_uses_docling_when_available(monkeypatch):
    monkeypatch.setattr(
        regulation_importer,
        "extract_with_docling",
        lambda file_bytes, filename="": "docling markdown output",
    )
    text = regulation_importer.extract_text_from_pdf(_sample_pdf_bytes(), "spec.pdf")
    assert text == "docling markdown output"


def test_extract_text_from_pdf_falls_back_when_docling_returns_none(monkeypatch):
    """docling 不可用（返回 None）时无声落回既有 pymupdf4llm→pymupdf 降级链，
    本仓库开发环境未装 pymupdf4llm，因此应落到 fitz 原始 page.get_text()。
    """
    monkeypatch.setattr(
        regulation_importer,
        "extract_with_docling",
        lambda file_bytes, filename="": None,
    )
    text = regulation_importer.extract_text_from_pdf(_sample_pdf_bytes("sample regulation text"), "spec.pdf")
    assert "sample regulation text" in text


def test_extract_text_dispatches_pdf_with_filename(monkeypatch):
    """extract_text 入口应把 filename 透传给 extract_text_from_pdf（供 docling 用作
    临时文件后缀/元数据推断），而不是丢弃。"""
    captured: dict[str, str] = {}

    def _fake_extract_from_pdf(file_bytes, filename=""):
        captured["filename"] = filename
        return "ok"

    monkeypatch.setattr(regulation_importer, "extract_text_from_pdf", _fake_extract_from_pdf)
    result = regulation_importer.extract_text(_sample_pdf_bytes(), "GB50010-2010.pdf")
    assert result == "ok"
    assert captured["filename"] == "GB50010-2010.pdf"


def test_extract_with_docling_wrapper_delegates_to_core_module(monkeypatch):
    """regulation_importer.extract_with_docling 是对 core.regulation.docling_extract
    的薄包装，验证委托关系不漂移（避免两处出现同名函数却各写各的实现）。"""
    calls = []

    def _fake_core_extract(file_bytes, filename="document.pdf"):
        calls.append((file_bytes, filename))
        return "delegated"

    monkeypatch.setattr(docling_extract, "extract_with_docling", _fake_core_extract)
    result = regulation_importer.extract_with_docling(b"abc", "spec.pdf")
    assert result == "delegated"
    assert calls == [(b"abc", "spec.pdf")]
