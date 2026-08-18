"""矢量文字够用时跳过 OCR —— 又快又准。

**实测背景**(第二工程):单图档案抽取 **50 秒**,全量 1798 张需 18 小时,
CPU 已 800% 满载(瓶颈是算力不是并发)。而这批图 **33.7%** 有充足矢量文字 ——
**矢量文字比 OCR 又快又准**(OCR 有识别错误,矢量是原始数据),
却仍然无条件跑了 OCR。

**判据必须同时看数量与质量**:
- 只看数量不行 —— 实测 65.6% 的页只有 4~6 个页脚水印 span,
  数量像"有文字",实际什么信息都没有;
- 只看质量不行 —— 坏 CMap 的乱码会被 `text_integrity` 滤掉,
  剩下的可信文字可能太少。
"""
from __future__ import annotations

import pytest

from services.drawing_info_extractor import (
    MIN_VECTOR_TEXTS_TO_SKIP_OCR, should_skip_ocr,
)


def _texts(n: int, content: str = "地下连续墙配筋图"):
    return [(0.0, float(i), content) for i in range(n)]


@pytest.mark.unit
def test_rich_vector_text_skips_ocr():
    """**核心用例**:矢量文字充足 ⇒ 跳过 OCR。"""
    assert should_skip_ocr(_texts(MIN_VECTOR_TEXTS_TO_SKIP_OCR + 5))


@pytest.mark.unit
def test_watermark_only_page_still_needs_ocr():
    """**实测 65.6%**:只有页脚水印的页,数量像有文字,实际无信息。"""
    watermark = [(0.0, 0.0, "2020-06-10"), (0.0, 1.0, "上海建工四建集团有限公司"),
                 (0.0, 2.0, "2020-06-22"), (0.0, 3.0, "上海建浩工程顾问有限公司")]
    assert not should_skip_ocr(watermark)


@pytest.mark.unit
def test_no_text_needs_ocr():
    """大歌剧院走的正是这条路(矢量文字取不到)。"""
    assert not should_skip_ocr([])
    assert not should_skip_ocr(None)


@pytest.mark.unit
def test_mojibake_does_not_count_as_vector_text():
    """坏 CMap 的乱码不算数 —— 否则会跳过 OCR 而拿一堆垃圾入库。"""
    garbage = [(0.0, float(i), "ᐛぁ䇴䇗⭨㓝")
               for i in range(MIN_VECTOR_TEXTS_TO_SKIP_OCR + 20)]
    assert not should_skip_ocr(garbage)


@pytest.mark.unit
def test_threshold_is_above_typical_watermark_count():
    """阈值必须高于水印 span 数(实测 4~6),否则判据形同虚设。"""
    assert MIN_VECTOR_TEXTS_TO_SKIP_OCR > 6


@pytest.mark.unit
def test_mixed_page_counts_only_trustworthy_texts():
    """混合页按**可信**文字计数。"""
    mixed = _texts(MIN_VECTOR_TEXTS_TO_SKIP_OCR - 1) + [
        (0.0, 99.0, "ᐛぁ䇴䇗")]
    assert not should_skip_ocr(mixed), "可信文字仍不够,不能跳"
