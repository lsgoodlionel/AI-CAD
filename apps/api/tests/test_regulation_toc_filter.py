"""规范目录页不是条文 —— 实测入库前 4 条全是目录碎片。

**实测**（GB55023 施工脚手架通用规范，OCR 后切分入库）：

    条文号 4    → "4 项目建设水平或技术水平的高低程度…"
    条文号 4.3  → "4.3 4.4构造要求\\n5搭设、使用与拆除\\n5.1个人防护"
    条文号 11   → "11 5.4拆除\\n12\\n6检查与验收·"

**这些是目录页**，且页码（`11`/`12`）被当成了条文号。

根因：切分判据「按条文编号分段」为**有排版结构的文本层**设计，
对 OCR 出的连续文本失效 —— 目录里满是 `4.3 构造要求`、`5.1 个人防护`
这类形似条文号的行。

规范正文的特征（GB/T 1.1 标准编写规则）：
条文号后跟**完整句子**（含「应/不应/宜/不宜/必须」等义务词或句号），
而目录行是**短标题 + 页码**。
"""
from __future__ import annotations

import pytest

from services.regulation_importer import is_toc_line, looks_like_article


@pytest.mark.unit
def test_toc_lines_detected():
    """**目录行**:短标题、无义务词、常带页码。"""
    for line in ("4.4构造要求", "5搭设、使用与拆除", "5.1个人防护",
                 "6检查与验收·······  12", "1总则 ····· 1",
                 "4.3 4.4构造要求"):
        assert is_toc_line(line), line


@pytest.mark.unit
def test_real_articles_not_flagged_as_toc():
    """**不得误伤正文** —— 正文有义务词或完整句子。"""
    for line in ("1.0.1为保障施工脚手架安全、适用，制定本规范。",
                 "3.1.1 结构混凝土用水泥主要控制指标应包括凝结时间、安定性。",
                 "4.2.3 脚手架的立杆间距不应大于1.8m。"):
        assert not is_toc_line(line), line


@pytest.mark.unit
def test_article_requires_obligation_or_sentence():
    """条文要么含**义务词**，要么是**完整句子**（有句号且够长）。"""
    assert looks_like_article("脚手架的立杆间距不应大于1.8m。")
    assert looks_like_article("为保障施工脚手架安全、适用，制定本规范。")
    assert not looks_like_article("构造要求")
    assert not looks_like_article("5.4拆除")


@pytest.mark.unit
def test_page_number_fragments_rejected():
    """**纯页码/序号不是条文** —— `11`、`12` 曾被当成条文号。"""
    for line in ("11", "12", "37", "  18  "):
        assert not looks_like_article(line), line


@pytest.mark.unit
def test_empty():
    assert not looks_like_article("")
    assert not looks_like_article(None)
    assert not is_toc_line("")
