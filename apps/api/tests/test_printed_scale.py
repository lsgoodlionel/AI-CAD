"""图框上印的比例（档案 OCR）进识别器 —— 排在「猜的」前面。

**实测**（大歌剧院南区一层结构平面图 .03C / .05C，图框印 `1:150`）：
矢量文字是轮廓化的读不到，落库变换是 1:10 / 1:15（不可信被挡掉），
于是识别器落到**缺省 1:100** —— 构件整体缩小 1.5 倍，与同层按 1:150
识别的 .04C 对不上。而档案 OCR 早就读到了图框里的 `1: 150`。

**全库对照**（落库变换可信、且档案读得到印刷比例的图）：

| 工程 | 印刷值与可信落库值一致 | 缺省 1:100 与之一致 |
|---|---|---|
| 大歌剧院 | **1081/1172 = 92%** | 84/1172 = 7% |
| 第二工程 | 30/68 = 44% | 30/68 = 44% |

印刷值在一个工程上远好于缺省、在另一个上不差 —— 所以它**排在猜测前面**。

**已知反例**（必须一起读）：`.01C 一层结构平面总图`图框也印 `1:150`，
但图上柱的尺寸只有同层分图的 0.6 倍（中位边长 6.9pt vs 11.2pt），
真实比例约 1:250。图框比例在「总图」上可能是抄来的。
即便如此，1:150 离真值（×1.67）也比缺省 1:100（×2.5）近。
"""
from __future__ import annotations

import asyncio
import inspect

import pytest

from core.model3d.element_recognizer import resolve_scale
from core.model3d.scale_evidence import printed_scale_from_archive

S_1_100 = 100 * 25.4 / 72 / 1000
S_1_150 = 150 * 25.4 / 72 / 1000
S_1_2 = 2 * 25.4 / 72 / 1000
PAGE_W = 3370.0


def _item(content, bbox=None):
    return {"content": content, "location_json": {"bbox": bbox} if bbox else None}


# ── 档案条目 → 印刷主比例 ─────────────────────────────────────────

@pytest.mark.unit
def test_title_block_scale_beats_a_detail_scale_in_the_drawing_body():
    """图框带里的 1:150 压过图面中间两处节点详图的 1:20（票权 5 vs 2）。"""
    items = [
        _item("1: 150", [2025.0, 1476.4, 2043.7, 1485.7]),   # 图框带（右下）
        _item("节点详图 1:20", [800.0, 600.0, 860.0, 610.0]),
        _item("1:20", [900.0, 700.0, 930.0, 710.0]),
    ]
    got = printed_scale_from_archive(items, extent=(2124.0, 1498.0))
    assert got == pytest.approx(S_1_150)


@pytest.mark.unit
def test_legacy_ocr_frame_is_normalised_by_the_drawings_own_extent():
    """2026-08-31 前写入的档案 bbox 按「最长边 2160」缩放，不是页面点。

    按页面点判「在不在图框带」会把图框里的 `1: 150`（x=2034）当成图面中部
    （3370 宽的 60%）。按本图 OCR 词条自身的范围归一化就与坐标框无关。
    """
    title = _item("1: 150", [2025.0, 1476.4, 2043.7, 1485.7])
    body = [_item("1:50", [700.0, 500.0, 720.0, 510.0]),
            _item("1:50", [1000.0, 800.0, 1020.0, 810.0])]
    assert printed_scale_from_archive([title, *body], extent=(2124.0, 1498.0)) \
        == pytest.approx(S_1_150)


@pytest.mark.unit
def test_tie_without_a_title_block_winner_is_not_guessed():
    """平票不猜 —— 两个比例各一票时说不出哪个是主比例。"""
    items = [_item("1:100"), _item("1:50")]
    assert printed_scale_from_archive(items) is None


@pytest.mark.unit
def test_nothing_readable_gives_none():
    assert printed_scale_from_archive([]) is None
    assert printed_scale_from_archive([_item("坡度 i=1:8")]) is None  # 1:8 不在 §6.0.4 表内


# ── 优先级：明文（矢量）> 印刷（档案 OCR）> 可信落库 > 猜测 ─────────

@pytest.mark.unit
def test_printed_scale_beats_the_default_guess():
    """**核心用例**：.03C/.05C 从缺省 1:100 改为图框印的 1:150。"""
    got = resolve_scale(S_1_100, None, PAGE_W, detected_is_guess=True,
                        printed_scale=S_1_150)
    assert got == pytest.approx(S_1_150)


@pytest.mark.unit
def test_printed_scale_used_when_the_trusted_stored_value_agrees():
    got = resolve_scale(S_1_100, S_1_150 * 1.03, PAGE_W, detected_is_guess=True,
                        printed_scale=S_1_150)
    assert got == pytest.approx(S_1_150)


@pytest.mark.unit
def test_printed_scale_yields_to_a_disagreeing_trusted_stored_value():
    """**同一张图只能有一套比例**（代码审查指出）：轴线、档案标签、工程坐标定位
    都按落库变换换算。印刷值与可信落库值冲突时让位，不让构件与自己的轴线错开。

    全库（可信落库 × 读得到印刷值）分歧的：大歌剧院 91 张、第二工程 38 张 ——
    谁对说不清，不在这里裁决。
    """
    absurd_guess = 1.489326      # 换算 5019 米，不可用 → 旧逻辑本就落到落库值
    got = resolve_scale(absurd_guess, S_1_100, PAGE_W, detected_is_guess=True,
                        printed_scale=S_1_150)
    assert got == pytest.approx(S_1_100)


@pytest.mark.unit
def test_vector_text_scale_still_wins():
    """矢量文字读到的比例不让位 —— 同是明文，且坐标系与几何一致。"""
    got = resolve_scale(S_1_100, None, PAGE_W, detected_is_guess=False,
                        printed_scale=S_1_150)
    assert got == pytest.approx(S_1_100)


@pytest.mark.unit
def test_printed_scale_implausible_for_a_plan_is_ignored():
    """图框读出 1:2 → 3370pt 只画 2.4 米，不可能是平面图：不采信，回到原逻辑。"""
    got = resolve_scale(S_1_100, None, PAGE_W, detected_is_guess=True,
                        printed_scale=S_1_2)
    assert got == pytest.approx(S_1_100)


@pytest.mark.unit
def test_printed_scale_reaches_resolve_scale_through_recognize():
    """整条传递链都要守 —— 「参数接了不传等于没接」。"""
    from core.model3d.element_recognizer import _recognize, recognize

    assert "printed_scale" in inspect.signature(recognize).parameters
    assert "printed_scale" in inspect.signature(_recognize).parameters
    assert "printed_scale" in inspect.getsource(recognize).split("return _recognize")[1][:200]
    assert "printed_scale=printed_scale" in inspect.getsource(_recognize)


@pytest.mark.unit
def test_circle_detection_uses_the_same_printed_scale():
    """桩（圆检测）与柱必须同一个比例 —— 实测过两套坐标混进一个列表（6362 米）。"""
    from core.model3d.circle_detector import detect_pile_columns, resolve_detection_frame

    scale, _ = resolve_detection_frame(S_1_100, (None, None), None, None, PAGE_W,
                                       detected_is_guess=True, printed_scale=S_1_150)
    assert scale == pytest.approx(S_1_150)
    assert "printed_scale" in inspect.signature(detect_pile_columns).parameters


# ── 建模接线 ─────────────────────────────────────────────────────

@pytest.mark.unit
def test_with_printed_scales_returns_new_dicts():
    from services.printed_scale import PRINTED_SCALE_KEY, with_printed_scales

    drawings = [{"id": "d1"}, {"id": "d2"}]
    out = with_printed_scales(drawings, {"d1": S_1_150})
    assert out[0][PRINTED_SCALE_KEY] == pytest.approx(S_1_150)
    assert PRINTED_SCALE_KEY not in out[1]
    assert drawings == [{"id": "d1"}, {"id": "d2"}], "原列表不得被改动"


@pytest.mark.unit
def test_load_printed_scales_reads_ocr_rows_and_extent():
    from services.printed_scale import load_printed_scales

    class FakeDb:
        async def fetch_all(self, sql, params):
            if "max(" in sql.lower():
                return [{"did": "d1", "mx": 2124.0, "my": 1498.0}]
            return [{"did": "d1", "content": "1: 150", "source_kind": "auto",
                     "location_json": {"bbox": [2025.0, 1476.4, 2043.7, 1485.7]}},
                    {"did": "d1", "content": "1:20", "source_kind": "auto",
                     "location_json": {"bbox": [800.0, 600.0, 860.0, 610.0]}}]

    got = asyncio.run(load_printed_scales(FakeDb(), "p1"))
    assert got == {"d1": pytest.approx(S_1_150)}


@pytest.mark.unit
def test_verified_scale_overrides_the_ocr_vote():
    """人审核定的比例永远优先 —— 即便 OCR 在图框里读出了别的值（票权 5）。"""
    from services.printed_scale import load_printed_scales

    s_1_50 = 50 * 25.4 / 72 / 1000

    class FakeDb:
        async def fetch_all(self, sql, params):
            if "max(" in sql.lower():
                return [{"did": "d1", "mx": 2124.0, "my": 1498.0}]
            return [{"did": "d1", "content": "1: 150", "source_kind": "auto",
                     "location_json": {"bbox": [2025.0, 1476.4, 2043.7, 1485.7]}},
                    {"did": "d1", "content": "1:50", "source_kind": "verified",
                     "location_json": None}]

    assert asyncio.run(load_printed_scales(FakeDb(), "p1")) == {"d1": pytest.approx(s_1_50)}


@pytest.mark.unit
def test_load_printed_scales_degrades_to_empty_on_db_error():
    """档案不可用时建模照常走原路径（诚实降级），不抛。"""
    from services.printed_scale import load_printed_scales

    class BrokenDb:
        async def fetch_all(self, sql, params):
            raise RuntimeError("no table")

    assert asyncio.run(load_printed_scales(BrokenDb(), "p1")) == {}


@pytest.mark.unit
def test_recognize_one_passes_the_drawings_printed_scale(monkeypatch):
    """_recognize_one 必须把图纸上挂的印刷比例送进识别线程。"""
    from services import model_elements
    from services.printed_scale import PRINTED_SCALE_KEY

    captured: dict = {}

    def fake_sync(*args):
        captured["args"] = args
        return {"elements": {}, "axes": {}}

    monkeypatch.setattr(model_elements, "_recognize_sync", fake_sync)
    drawing = {"id": "d1", "file_key": "a.pdf", "title": "一层结构平面图",
               PRINTED_SCALE_KEY: S_1_150}

    async def run():
        loop = asyncio.get_running_loop()
        return await model_elements._recognize_one(
            loop, None, drawing, "structure", lambda _key: b"%PDF")

    asyncio.run(run())
    assert S_1_150 in captured["args"]
