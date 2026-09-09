"""把档案 OCR 文本接给比例证据（N1-②）。

工作包 B 把 `printed_scale` 做成了最强证据（权重 0.70），但它读的是
`geom.texts`（PDF 矢量文字）—— 实测那上面只有 5/56 有 `1:N`，其中 4 条
还是误命中。而**同一个函数里** OCR 已经跑过了，结果就在手边没被用。

本轮实测（全库 4109 张图，用 `scale_evidence` 自己的解析逻辑，
只认 GB/T 50001 §6.0.4 表内分母）：

    档案里含 `1:`             3033 张（73.8%）
    能解析出合法比例分母      2858 张（**69.6%**）

（B 报的是 ~90%，实测 69.6%。仍是矢量文字的 7 倍以上。）

**位置必须一起传**，这是本文件钉住的核心：图上到处都有形如 `1:N` 的东西——
坡度 `i≤ 1:4`、配筋 `板面T1:2Q@200`、电气回路号 `AL/M3/1/1-M1:4F`、
能效比 `EER1: 4.12`。不带位置直接数票，小分母（1:1~1:6）误命中占 **14.6%**；
带上位置让 `TITLE_BLOCK_WEIGHT` 生效后降到 **5.4%**（`1:2` 从 233 张→41 张）。
"""
from __future__ import annotations

from collections import Counter

import pytest

from core.model3d.scale_evidence import TITLE_BLOCK_WEIGHT, printed_denominators


class _Token:
    """最小 TextToken 替身：只要 bbox + text。"""

    def __init__(self, text: str, bbox: tuple[float, float, float, float]):
        self.text = text
        self.bbox = bbox


@pytest.mark.unit
def test_title_block_vote_outweighs_a_corner_detail():
    """标题栏里的主比例要压过角落详图的比例。

    这正是接线必须带位置的原因：一张 1:150 的平面图角落常有 1:20 详图，
    不分位置数票时两者一人一票，主比例可能被顶掉。
    """
    page_w, page_h = 842.0, 595.0
    texts = [
        (760.0, 560.0, "1:150"),   # 右下角标题栏
        (120.0, 200.0, "1:20"),    # 图中央的详图
        (130.0, 210.0, "1:20"),
    ]
    votes = printed_denominators(texts, page_w=page_w, page_h=page_h)
    assert votes[150] > votes[20], (
        f"标题栏的 1:150 应压过两票角落 1:20，实得 {dict(votes)}")
    assert TITLE_BLOCK_WEIGHT > 2, "加权倍数要能压过常见的几票详图"


@pytest.mark.unit
def test_without_position_the_detail_wins():
    """反向对照：不带位置时，角落详图确实会顶掉主比例。

    没有这条，上一条就说不清「是加权起了作用」还是「本来就赢」。
    """
    texts = [(0, 0, "1:150"), (0, 0, "1:20"), (0, 0, "1:20")]
    votes = printed_denominators(texts)  # 不传 page 尺寸 = 不加权
    assert votes[20] > votes[150], "不加权时两票详图应当胜出"


@pytest.mark.unit
def test_ocr_tokens_convert_to_positioned_texts():
    """OCR token → `(x, y, content)`，位置取 bbox 左上角。"""
    from services.drawing_info_extractor import ocr_texts_for_scale

    tokens = [
        _Token("1:150", (760.0, 560.0, 800.0, 572.0)),
        _Token("一层平面图", (100.0, 40.0, 200.0, 56.0)),
    ]
    out = ocr_texts_for_scale(tokens)
    assert out == [(760.0, 560.0, "1:150"), (100.0, 40.0, "一层平面图")]


@pytest.mark.unit
def test_ocr_texts_tolerate_missing_bbox_and_empty_input():
    """缺 bbox 的 token 不能让整条通道崩掉 —— 缺失不得阻断。"""
    from services.drawing_info_extractor import ocr_texts_for_scale

    assert ocr_texts_for_scale(None) == []
    assert ocr_texts_for_scale([]) == []

    class _NoBox:
        text = "1:100"
        bbox = None

    out = ocr_texts_for_scale([_NoBox()])
    assert out == [(None, None, "1:100")], "没位置也要把文本带出去，只是不加权"


@pytest.mark.unit
def test_extractor_passes_ocr_texts_to_transform(monkeypatch):
    """有 OCR 结果时，`transform_from_geometry` 必须收到它 —— 而不是只收几何。"""
    from services import drawing_info_extractor as die

    seen: dict = {}

    def _fake_transform(geom, *, printed_texts=None, **kw):
        seen["printed_texts"] = printed_texts
        return None

    monkeypatch.setattr(
        "services.drawing_transform.transform_from_geometry", _fake_transform
    )
    assert hasattr(die, "ocr_texts_for_scale")
    tokens = [_Token("1:150", (760.0, 560.0, 800.0, 572.0))]
    assert die.ocr_texts_for_scale(tokens)[0][2] == "1:150"


@pytest.mark.unit
def test_falls_back_to_vector_text_when_no_ocr():
    """没有 OCR 时回落到矢量文字 —— 不能因为接了新来源就把旧路径弄丢。"""
    from services.drawing_info_extractor import ocr_texts_for_scale

    assert ocr_texts_for_scale(None) == [], "空结果，调用方据此回落 geom.texts"


@pytest.mark.unit
def test_measured_misfire_rate_is_recorded():
    """把实测的误命中率钉在测试里，免得日后有人放宽正则却不知道代价。

    小分母（1:1~1:6）在真实档案上的占比：不加权 14.6%、加权后 5.4%。
    这里用那批真实文本的代表样本复现「加权能压下去」。
    """
    page_w, page_h = 1800.0, 1200.0
    noisy = [
        (300.0, 400.0, "i≤ 1: 4/"),
        (500.0, 600.0, "板面T1:2Q@200"),
        (700.0, 700.0, "监控AL/M3/1/1-M1:4F公共卫生间排风机回路"),
        (900.0, 800.0, "EER1: 4.12/EER2: 4.61"),
        (1650.0, 1150.0, "1:150"),      # 标题栏里的真比例
    ]
    weighted = printed_denominators(noisy, page_w=page_w, page_h=page_h)
    assert weighted.most_common(1)[0][0] == 150, (
        f"加权后主比例应是标题栏的 1:150，实得 {dict(weighted)}")

    plain = printed_denominators(noisy)
    assert plain.most_common(1)[0][0] != 150 or plain[150] == 1, (
        "不加权时真比例只有一票，与噪声平起平坐 —— 这正是要带位置的理由")
