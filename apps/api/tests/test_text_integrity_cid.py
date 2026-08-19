"""两种**最典型**的 PDF 乱码形态此前未被覆盖。

现有判据认三类码位（控制字符 / 私用区 / CJK 扩展 A），
但漏了 PDF 文字提取里最常见的两种：

| 样本 | 含义 | 修复前 |
|---|---|---|
| `�` | **U+FFFD 替换字符** —— 编码失败的标准标志 | 判为可信 |
| `(cid:12)` | **ToUnicode CMap 缺失**时 pdfminer/PyMuPDF 的典型输出 | 判为可信 |

`(cid:NN)` 尤其要紧：它不是单个坏字符，而是**一串可打印 ASCII**，
逐字符判永远判不出 —— 必须按**模式**识别。
"""
from __future__ import annotations

import pytest

from core.model3d.text_integrity import is_trustworthy_text, text_verdict


@pytest.mark.unit
def test_replacement_char_is_mojibake():
    """U+FFFD 是编码失败的标准标志。"""
    assert not is_trustworthy_text("��")
    assert not is_trustworthy_text("标高����")


@pytest.mark.unit
def test_cid_pattern_is_mojibake():
    """`(cid:NN)` —— **一串可打印 ASCII**,逐字符判不出,必须按模式。"""
    assert not is_trustworthy_text("(cid:12)(cid:34)")
    assert not is_trustworthy_text("(cid:3)")
    verdict = text_verdict("(cid:12)(cid:34)")
    assert verdict["reason"] == "mojibake"


@pytest.mark.unit
def test_normal_text_with_parentheses_still_passes():
    """**不得误伤正常括号文字** —— 图名里括号极常见。"""
    for good in ("地下一层平面图（一）", "主梁配筋图(四)", "C1(玻璃)",
                 "cid 是个普通词", "(1:100)"):
        assert is_trustworthy_text(good), good


@pytest.mark.unit
def test_mixed_text_mostly_cid_is_rejected():
    """正文里混入大量 cid 片段 → 不可信。"""
    assert not is_trustworthy_text("(cid:5)(cid:9)(cid:2)(cid:7)标高")
