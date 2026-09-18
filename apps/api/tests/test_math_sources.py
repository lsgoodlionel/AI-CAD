"""数理化教材清单：登记的是实测，不是猜测；派生产物不进版本库。

与 `test_knowledge_sources.py` 同一条纪律 —— 清单是**单一来源**，
页数、文字来路、排除理由都要能回答「凭什么这么写」。
"""
from __future__ import annotations

import pytest

from core.knowledge import math_sources as ms
from core.knowledge.source_registry import SOURCE_ROOT as STANDARDS_ROOT


@pytest.mark.unit
def test_every_source_declares_how_its_text_is_obtained():
    for source in ms.SOURCES:
        assert source.extract_method in ("text_layer", "ocr", "epub"), source.key
        assert source.pages > 0
        assert source.identified_by
        assert source.notes, f"{source.key} 没写用途 —— 登记一本书要说清它凭什么在册"


@pytest.mark.unit
def test_text_layer_claim_is_backed_by_sampled_character_counts():
    """「有没有文本层」是抽样实测出来的，不是按文件类型猜的。

    实测：六本抽样页 500~3000 字符，Knight《Physics》三页全 0 —— 扫描件。
    """
    for source in ms.SOURCES:
        sampled = source.evidence.get("sample_chars")
        assert sampled, f"{source.key} 没留抽样证据"
        if source.extract_method == "ocr":
            assert max(sampled) == 0, f"{source.key} 抽样有文字，不该标 ocr"
        else:
            assert min(sampled) > 0, f"{source.key} 抽样有空页，不该直取文本层"


@pytest.mark.unit
def test_epub_declares_spine_sections_not_reflowed_pages():
    """EPUB 按 spine 节数登记。

    按 fitz 重排出的 412 页登记，统计会报「缺页 376」这种假警报 ——
    缺页提示的价值在于它出现时确实缺了东西。
    """
    epub = [s for s in ms.SOURCES if s.extract_method == "epub"]
    assert epub, "清单里应当有 EPUB（否则本测试失去对象）"
    for source in epub:
        assert source.pages == source.evidence["spine_sections"]
        assert source.pages != source.evidence["fitz_reflowed_pages"]


@pytest.mark.unit
def test_std_no_is_stable_and_unique():
    """`KB-<key>` 是入库主键（`regulation_books.std_no`），重了会互相覆盖。"""
    numbers = [s.std_no for s in ms.SOURCES]
    assert len(set(numbers)) == len(numbers)
    for source in ms.SOURCES:
        assert source.std_no == f"KB-{source.key}"


@pytest.mark.unit
def test_excluded_books_carry_a_reason():
    """排除要写理由 —— 否则读清单的人无从判断「是漏了还是不要」。"""
    assert ms.EXCLUDED
    for title, reason in ms.EXCLUDED:
        assert title and len(reason) >= 8


@pytest.mark.unit
def test_this_corpus_has_its_own_root_and_does_not_shadow_the_standards_one():
    """两批资料共用一套登记结构，但根目录各自独立。

    `KnowledgeSource.root` 就是为此加的：复制一份 dataclass 只会让两边
    的字段慢慢长歪。
    """
    assert ms.SOURCE_ROOT != STANDARDS_ROOT
    for source in ms.SOURCES:
        assert source.root == ms.SOURCE_ROOT
        assert str(source.path).startswith(str(ms.SOURCE_ROOT))


@pytest.mark.unit
def test_by_key_rejects_unknown_keys():
    assert ms.by_key("strang-la").kind == "textbook"
    with pytest.raises(KeyError):
        ms.by_key("does-not-exist")
