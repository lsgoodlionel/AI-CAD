"""规范条文切分（OCR 文本）—— 按实测形态重写。

**实测**（GB55008 OCR 全文 13985 字 / 870 行）：

    2.0.3混凝土结构用普通钢筋、预应力筋应具有符合工程结构
    在承载能力极限状态和正常使用极限状态下需求的强度和延
    伸率。
    2.0.4混凝土结构用普通钢筋…强度设计值取值应符合
    2                          ← **页码，孤立成行插在句子中间**
    下列规定：
    1结构混凝土强度设计值应按其强度标准值除以材料分项    ← **子项编号**
    系数确定，且材料分项系数取值不应小于1.4；

三个必须处理的特征：

1. **条文号紧贴正文无空格**（`2.0.3混凝土…`）—— 不能按空格切
2. **页码孤立成行插在句中** —— 必须剔除，否则句子被腰斩
3. **子项用单数字编号**（`1结构混凝土…`）—— 属于上一条，不是新条文

条文号形态为 `N.N.N`（GB/T 1.1 三级编号），子项是裸数字，两者可区分。
"""
from __future__ import annotations

import pytest

from services.regulation_importer import (
    is_page_number_line, split_ocr_articles,
)


@pytest.mark.unit
def test_page_number_lines_detected():
    """**页码孤立成行** —— 纯数字且短。"""
    for line in ("2", "37", "  18  ", "125"):
        assert is_page_number_line(line), line


@pytest.mark.unit
def test_content_lines_not_page_numbers():
    """**不得误删正文** —— 带文字的行不是页码。"""
    for line in ("2.0.3混凝土结构用普通钢筋", "1结构混凝土强度设计值",
                 "C30", "不应小于1.4；"):
        assert not is_page_number_line(line), line


@pytest.mark.unit
def test_splits_on_three_level_numbers():
    """**核心用例**:按 `N.N.N` 切分,子项编号不切。"""
    text = "\n".join([
        "2.0.3混凝土结构用普通钢筋、预应力筋应具有符合工程结构",
        "需求的强度和延伸率。",
        "2.0.4混凝土结构用普通钢筋的强度设计值取值应符合",
        "下列规定：",
        "1结构混凝土强度设计值应按其强度标准值除以材料分项",
        "系数确定，且材料分项系数取值不应小于1.4；",
    ])
    got = split_ocr_articles(text)
    assert [a["article_no"] for a in got] == ["2.0.3", "2.0.4"]
    # 子项并入所属条文
    assert "1结构混凝土强度设计值" in got[1]["text"]
    assert "1.4" in got[1]["text"]


@pytest.mark.unit
def test_page_number_does_not_break_sentence():
    """**页码剔除后句子要接上** —— 它曾把一条腰斩成两半。"""
    text = "\n".join([
        "2.0.4混凝土结构用普通钢筋的强度设计值取值应符合",
        "2",
        "下列规定：",
    ])
    got = split_ocr_articles(text)
    assert len(got) == 1
    assert "2" not in got[0]["text"].replace("2.0.4", "")
    assert "下列规定" in got[0]["text"]


@pytest.mark.unit
def test_line_breaks_inside_article_are_joined():
    """**行内断行要接上** —— OCR 按视觉行输出,一句常跨多行。"""
    text = "\n".join([
        "2.0.3混凝土结构用普通钢筋、预应力筋应具有符合工程结构",
        "在承载能力极限状态下需求的强度和延伸率。",
    ])
    got = split_ocr_articles(text)
    assert len(got) == 1
    assert "工程结构在承载能力" in got[0]["text"]


@pytest.mark.unit
def test_toc_region_excluded():
    """**目录不进条文** —— 目录行也形如 `4.3 构造要求`。"""
    text = "\n".join([
        "4.3 构造要求",
        "5.1个人防护",
        "6检查与验收····· 12",
        "2.0.1为保障混凝土结构安全，制定本规范。",
    ])
    got = split_ocr_articles(text)
    assert [a["article_no"] for a in got] == ["2.0.1"]


@pytest.mark.unit
def test_empty():
    assert split_ocr_articles("") == []
    assert split_ocr_articles(None) == []
