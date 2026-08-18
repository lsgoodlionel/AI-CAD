"""提取文字的**可信度**判定 —— 第二工程引入的全新失效模式。

**两种失败,危险程度相反**:

| 工程 | 现象 | 后果 |
|---|---|---|
| 大歌剧院 | 文字**取不到**(返回空) | 安全 —— 自动降级到 OCR |
| 轨道交通 | 文字**取得到但是错的** | **危险 —— 静默污染档案层** |

成因是未嵌入 ToUnicode CMap 的子集字体:字形画得对,
但字符码→Unicode 的映射丢了。实测有文字的 262 张里 **77 张(29.4%)**
含 ≥3 个乱码字符,典型:

    真实内容  建筑设计资质 证书编号 A131004031
    提取结果  ᐛぁ䇴䇗⭨㓝\\x03䇷Ҝ㕌ਭ\\x03$\\x14\\x16\\x14…

**归档层此前没有任何机制区分这两者** —— 干净中文与乱码一样入库。
"""
from __future__ import annotations

import pytest

from core.model3d.text_integrity import (
    MOJIBAKE_RATIO_THRESHOLD, is_trustworthy_text, mojibake_ratio,
)


# ── 真实乱码样本(取自实测) ───────────────────────────────────────

@pytest.mark.unit
def test_real_mojibake_is_rejected():
    """**核心用例**:实测样本必须判为不可信。"""
    sample = "ᐛぁ䇴䇗⭨㓝\x03䇷Ҝ㕌ਭ\x03$\x14\x16\x14\x13\x13\x17\x13\x16\x14"
    assert not is_trustworthy_text(sample)
    assert mojibake_ratio(sample) > MOJIBAKE_RATIO_THRESHOLD


@pytest.mark.unit
def test_clean_chinese_is_accepted():
    """70.6% 是干净中文,不能误杀。"""
    for text in ("建筑设计资质 证书编号 A131004031",
                 "地下连续墙配筋立面图（一）",
                 "3F平面布置图  比例 1:100"):
        assert is_trustworthy_text(text), text


@pytest.mark.unit
def test_control_characters_count_as_mojibake():
    """`\\x03` 这类控制字符是坏 CMap 的典型残留。"""
    assert mojibake_ratio("正常文字\x03\x04\x05\x06") > 0


@pytest.mark.unit
def test_cjk_ext_a_is_mojibake_in_drawing_context():
    """CJK 扩展 A(㐀-䶵)在工程图纸里几乎不会正常出现。"""
    assert mojibake_ratio("䇴䇗㕌") > 0.9


@pytest.mark.unit
def test_pua_is_mojibake():
    """私用区码位必然是字体子集的产物。"""
    assert mojibake_ratio("") == pytest.approx(1.0)


# ── 边界:短文本与空文本 ─────────────────────────────────────────

@pytest.mark.unit
def test_empty_text_is_not_trustworthy_but_not_mojibake():
    """**空 ≠ 乱码**:空是「取不到」(安全,走 OCR),不该被当成脏数据。"""
    assert mojibake_ratio("") == 0.0
    assert not is_trustworthy_text("")


@pytest.mark.unit
def test_short_text_needs_absolute_count_not_just_ratio():
    """短文本上比例噪声大 —— 一个偶发生僻字不该让整段作废。"""
    assert is_trustworthy_text("图纸目录")
    assert is_trustworthy_text("A-01")


@pytest.mark.unit
def test_ascii_only_text_is_trustworthy():
    """纯英文/数字(图号、比例)不含 CJK,不能因此判为乱码。"""
    assert is_trustworthy_text("SPEC.001  SCALE 1:100")


@pytest.mark.unit
def test_mixed_text_below_threshold_survives():
    """少量生僻字混在正常文字里仍可信 —— 阈值是比例不是存在性。"""
    assert is_trustworthy_text("建筑设计资质证书编号 A131004031 特殊字㐀")


# ── 供档案层使用的判定入口 ───────────────────────────────────────

@pytest.mark.unit
def test_verdict_explains_itself():
    """降级必须可见:说明是「取不到」还是「取到但不可信」。"""
    from core.model3d.text_integrity import text_verdict

    assert text_verdict("")["reason"] == "empty"
    assert text_verdict("ᐛぁ䇴䇗⭨㓝\x03䇷Ҝ㕌ਭ")["reason"] == "mojibake"
    assert text_verdict("地下连续墙配筋图")["reason"] == "ok"
    assert text_verdict("地下连续墙配筋图")["trustworthy"]


# ── 接进几何提取:乱码不得进入 geom.texts ────────────────────────

@pytest.mark.unit
def test_geometry_text_collection_drops_mojibake():
    """**核心接线用例**:坏 CMap 的词不进几何,让下游正常降级到 OCR。

    不过滤的话,`geom.texts` 里会混入「取到但是错的」文字,
    而下游(比例读取、标高抽取、构件标签)全都信任它 ——
    **静默污染**比取不到危险得多。
    """
    from core.model3d.geometry_extractor import _collect_pdf_texts, DrawingGeometry

    class _FakePage:
        def get_text(self, _kind):
            return [
                (0.0, 0.0, 10.0, 10.0, "地下连续墙"),
                (0.0, 0.0, 10.0, 10.0, "ᐛぁ䇴䇗⭨㓝"),      # 坏 CMap
                (0.0, 0.0, 10.0, 10.0, "配筋图"),
            ]

    geom = DrawingGeometry()
    _collect_pdf_texts(_FakePage(), geom)
    contents = [t[2] for t in geom.texts]
    assert "地下连续墙" in contents and "配筋图" in contents
    assert not any("䇴" in c for c in contents), "乱码必须被拦下"


@pytest.mark.unit
def test_geometry_text_keeps_short_codes():
    """短图号/比例不能被误杀。"""
    from core.model3d.geometry_extractor import _collect_pdf_texts, DrawingGeometry

    class _FakePage:
        def get_text(self, _kind):
            return [(0.0, 0.0, 1.0, 1.0, "A-01"),
                    (0.0, 0.0, 1.0, 1.0, "1:100")]

    geom = DrawingGeometry()
    _collect_pdf_texts(_FakePage(), geom)
    assert len(geom.texts) == 2
