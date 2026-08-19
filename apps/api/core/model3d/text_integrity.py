"""提取文字的**可信度**判定 —— 区分「取不到」与「取到但是错的」。

**这两种失败的危险程度相反**:

- 大歌剧院:文字**取不到**(返回空)⇒ 安全,自动降级到 OCR;
- 轨道交通:文字**取得到但是错的** ⇒ **危险,静默污染档案层**。
  实测有文字的 262 张里 77 张(29.4%)是坏 CMap 乱码 ——
  未嵌入 ToUnicode 的子集字体,字形画得对但字符码映射丢了。

归档层此前没有任何机制区分两者,干净中文与乱码一样入库。

**判据是字符码位分布,与语言无关**(不含任何工程/词表假设):
私用区、CJK 扩展 A、控制字符在正常工程图纸里几乎不出现,
它们的密度就是坏 CMap 的指纹。
"""
from __future__ import annotations

import re

from typing import Any

#: 乱码字符占比超过它就判为不可信。
#: 取 0.30:实测坏 CMap 样本几乎全是乱码(>0.9),而正常图纸文字
#: 偶含生僻字远低于此 —— 中间地带很宽,阈值不敏感。
MOJIBAKE_RATIO_THRESHOLD = 0.30

#: 低于这个长度不做比例判定 —— 短文本上比例噪声大,
#: 一个偶发生僻字会让「图纸目录」这种正常标题作废。
MIN_LENGTH_FOR_RATIO = 8


def _is_mojibake_char(ch: str) -> bool:
    """该字符是否为坏 CMap 的指纹。

    三类码位在正常的中文工程图纸里几乎不出现:
    - **控制字符**(除常见空白):`\\x03` 这类是典型残留;
    - **私用区**(U+E000–U+F8FF 及两个补充平面):必然来自字体子集;
    - **CJK 扩展 A**(U+3400–U+4DBF):罕见字,工程图纸不用。
    """
    code = ord(ch)
    if code < 0x20 and ch not in "\t\n\r":
        return True
    if 0xE000 <= code <= 0xF8FF:                      # 私用区
        return True
    if 0xF0000 <= code <= 0x10FFFD:                   # 补充私用区
        return True
    if 0x3400 <= code <= 0x4DBF:                      # CJK 扩展 A
        return True
    if code == 0xFFFD:                                # 替换字符 —— 编码失败的标准标志
        return True
    return False


def mojibake_ratio(text: str) -> float:
    """乱码字符占比(0.0~1.0)。空串 → 0.0(**空不是乱码**)。"""
    body = str(text or "")
    if not body:
        return 0.0
    hits = sum(1 for ch in body if _is_mojibake_char(ch))
    return hits / len(body)


#: `(cid:NN)` —— ToUnicode CMap 缺失时 pdfminer/PyMuPDF 的典型输出。
#: **它不是单个坏字符，而是一串可打印 ASCII**，逐字符判永远判不出，
#: 必须按模式识别。实测这是 PDF 文字提取最常见的两种乱码之一。
_CID_PATTERN = re.compile(r"\(cid:\d+\)")

#: cid 片段占比超过这个比例即判为不可信（正文里偶现一两个不算）。
CID_RATIO_THRESHOLD = 0.3


def cid_ratio(text: str) -> float:
    """`(cid:NN)` 片段占全文字符数的比例。"""
    body = str(text or "")
    if not body:
        return 0.0
    covered = sum(len(m.group(0)) for m in _CID_PATTERN.finditer(body))
    return covered / len(body)


def is_trustworthy_text(text: str) -> bool:
    """这段提取文字能否入库。空串 → False(取不到,应走 OCR)。"""
    body = str(text or "")
    if not body.strip():
        return False
    if cid_ratio(body) > CID_RATIO_THRESHOLD:
        return False
    if len(body) < MIN_LENGTH_FOR_RATIO:
        # 短文本只看有没有**确凿**的乱码字符,不看比例
        return not any(_is_mojibake_char(ch) for ch in body)
    return mojibake_ratio(body) <= MOJIBAKE_RATIO_THRESHOLD


def text_verdict(text: str) -> dict[str, Any]:
    """完整判定 —— **降级必须可见**:说清是取不到还是取到但不可信。"""
    body = str(text or "")
    ratio = mojibake_ratio(body)
    if not body.strip():
        reason = "empty"
    elif not is_trustworthy_text(body):
        reason = "mojibake"
    else:
        reason = "ok"
    return {
        "trustworthy": reason == "ok",
        "reason": reason,
        "mojibake_ratio": round(ratio, 3),
        "length": len(body),
        "explanation": {
            "empty": "未提取到文字（矢量文字缺失）——应降级到 OCR",
            "mojibake": ("提取到文字但字符码映射损坏（未嵌入 ToUnicode CMap "
                         "的子集字体）——**不得入库**，应降级到 OCR"),
            "ok": "文字可信",
        }[reason],
    }
