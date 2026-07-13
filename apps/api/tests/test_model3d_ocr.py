"""Item-2 · 图纸全文 OCR（model3d 域）离线测试。

覆盖：文本分类（标高/轴号/尺寸/楼层/房间/说明）、坐标像素→点换算、
mock 后端整链路、置信过滤、无后端优雅降级。真实 PaddleOCR 推理留待带权重
环境验证；本测试全离线、无重依赖。
"""
import pytest

from core.model3d.ocr.classify import classify_text
from core.model3d.ocr.mock_backend import MockOcrBackend
from core.model3d.ocr.service import run_ocr
from core.model3d.ocr.types import OcrResult, TextToken


# ── 分类器（结构化的核心） ──────────────────────────────────────

@pytest.mark.parametrize(
    "text,kind,value",
    [
        ("±0.000", "elevation", 0.0),
        ("+3.600", "elevation", 3.6),
        ("-1.500", "elevation", -1.5),
        ("标高 3.600", "elevation", 3.6),
        ("一层", "level_name", None),
        ("地下二层", "level_name", None),
        ("屋面", "level_name", None),
        ("1", "axis", None),
        ("12", "axis", None),
        ("A", "axis", None),
        ("1/A", "axis", None),
        ("3600", "dimension", 3600.0),
        ("150", "dimension", 150.0),
        ("会议室", "room_name", None),
        ("本图未注明定位轴线均居中", "note", None),
        ("", "other", None),
    ],
)
def test_classify_text(text, kind, value):
    got_kind, got_value = classify_text(text)
    assert got_kind == kind
    assert got_value == value


def test_classify_long_cjk_is_title():
    kind, _ = classify_text("上海大歌剧院项目地下室结构平面布置图说明")
    assert kind == "title"


# ── 服务整链路（mock 后端） ────────────────────────────────────

def _seed():
    # (text, bbox_pixels, confidence)
    return [
        ("±0.000", (100, 200, 160, 220), 0.98),
        ("+3.600", (100, 400, 160, 420), 0.91),
        ("A", (50, 50, 70, 70), 0.55),          # 低置信轴号
        ("会议室", (300, 300, 360, 320), 0.88),
    ]


def test_run_ocr_with_mock_backend_classifies_and_converts_coords():
    backend = MockOcrBackend(seed=_seed())
    result = run_ocr(b"not-a-real-pdf", "pdf", dpi=144, backend=backend)

    assert isinstance(result, OcrResult)
    assert result.backend == "mock"
    assert result.available is True
    assert len(result.tokens) == 4

    elevations = result.of_kind("elevation")
    assert {t.value for t in elevations} == {0.0, 3.6}

    # dpi=144 → scale = 72/144 = 0.5，像素 100 → 点 50
    elev0 = next(t for t in result.tokens if t.text == "±0.000")
    assert elev0.bbox == (50.0, 100.0, 80.0, 110.0)

    assert result.kind_counts["elevation"] == 2
    assert result.kind_counts["axis"] == 1
    assert result.kind_counts["room_name"] == 1


def test_run_ocr_confidence_filter_drops_low():
    backend = MockOcrBackend(seed=_seed())
    result = run_ocr(b"x", "pdf", dpi=72, backend=backend, min_confidence=0.9)
    # 只剩 conf>=0.9 的 ±0.000(0.98) 和 +3.600(0.91)
    assert len(result.tokens) == 2
    assert all(t.confidence >= 0.9 for t in result.tokens)


def test_filter_confidence_method():
    r = OcrResult(
        tokens=(
            TextToken("a", (0, 0, 1, 1), 0.95, "other"),
            TextToken("b", (0, 0, 1, 1), 0.4, "other"),
        ),
        backend="mock",
    )
    assert len(r.filter_confidence(0.9).tokens) == 1


def test_run_ocr_graceful_degradation_when_backend_unavailable():
    class DeadBackend:
        name = "dead"

        def is_available(self) -> bool:
            return False

        def recognize(self, image_rgb, warnings):
            raise AssertionError("不应被调用")

    result = run_ocr(b"x", "pdf", backend=DeadBackend())
    assert result.backend == "none"
    assert result.available is False
    assert result.tokens == ()
    assert result.warnings  # 有降级说明


def test_empty_mock_seed_still_returns_result_with_warning():
    result = run_ocr(b"x", "pdf", backend=MockOcrBackend(seed=[]))
    assert result.backend == "mock"
    assert result.tokens == ()
    assert any("mock" in w for w in result.warnings)


# ── 下游消费者馈入 ──────────────────────────────────────────────

def test_elevation_candidates_dedup_sort_and_threshold():
    from core.model3d.ocr.consume import elevation_candidates

    result = OcrResult(
        tokens=(
            TextToken("+3.600", (0, 0, 1, 1), 0.95, "elevation", 3.6),
            TextToken("±0.000", (0, 0, 1, 1), 0.99, "elevation", 0.0),
            TextToken("+3.600", (9, 9, 10, 10), 0.9, "elevation", 3.6),  # 重复值去重
            TextToken("-1.500", (0, 0, 1, 1), 0.4, "elevation", -1.5),   # 低置信剔除
        ),
        backend="mock",
    )
    cands = elevation_candidates(result, min_confidence=0.6)
    assert [c["value_m"] for c in cands] == [0.0, 3.6]  # 升序、去重、过滤


def test_parse_paddle_output_v3_dict_structure():
    from core.model3d.ocr.paddle_backend import parse_paddle_output

    page = {
        "rec_texts": ["±0.000", "会议室"],
        "rec_scores": [0.98, 0.87],
        "rec_polys": [
            [(100, 200), (160, 200), (160, 220), (100, 220)],
            [(300, 300), (360, 300), (360, 320), (300, 320)],
        ],
    }
    out = parse_paddle_output([page])
    assert out == [
        ("±0.000", (100.0, 200.0, 160.0, 220.0), 0.98),
        ("会议室", (300.0, 300.0, 360.0, 320.0), 0.87),
    ]


def test_parse_paddle_output_v3_rec_boxes_fallback():
    from core.model3d.ocr.paddle_backend import parse_paddle_output

    page = {"rec_texts": ["A"], "rec_scores": [0.9], "rec_polys": [], "rec_boxes": [[10, 20, 30, 40]]}
    out = parse_paddle_output([page])
    assert out == [("A", (10.0, 20.0, 30.0, 40.0), 0.9)]


def test_parse_paddle_output_v2_nested_list():
    from core.model3d.ocr.paddle_backend import parse_paddle_output

    page = [
        [[(100, 200), (160, 200), (160, 220), (100, 220)], ("+3.600", 0.91)],
        ["garbled"],  # 畸形行跳过
    ]
    out = parse_paddle_output([page])
    assert out == [("+3.600", (100.0, 200.0, 160.0, 220.0), 0.91)]


def test_parse_paddle_output_empty_and_none():
    from core.model3d.ocr.paddle_backend import parse_paddle_output

    assert parse_paddle_output(None) == []
    assert parse_paddle_output([None]) == []


def test_tile_origins_cover_full_length_without_gap():
    from core.model3d.ocr.service import _TILE_OVERLAP_PX, _tile_origins

    assert _tile_origins(1000, 1600, 200) == [0]  # 小于块长不切
    origins = _tile_origins(9500, 1600, 200)
    assert origins[0] == 0
    assert origins[-1] == 9500 - 1600  # 末块贴齐末端
    # 相邻块重叠 ≥ overlap，全覆盖无缝隙
    for prev, nxt in zip(origins, origins[1:]):
        assert nxt - prev <= 1600 - _TILE_OVERLAP_PX


def test_dedup_raw_keeps_highest_confidence_on_overlap():
    from core.model3d.ocr.service import _dedup_raw

    raw = [
        ("±0.000", (100.0, 100.0, 160.0, 120.0), 0.90),
        ("±0.000", (102.0, 101.0, 161.0, 121.0), 0.95),  # 重叠区重复识别
        ("A", (500.0, 500.0, 520.0, 520.0), 0.80),        # 不重叠保留
    ]
    kept = _dedup_raw(raw)
    assert len(kept) == 2
    assert ("±0.000", (102.0, 101.0, 161.0, 121.0), 0.95) in kept
    assert kept[-1][0] in ("±0.000", "A")


def test_recognize_tiled_translates_coords_back_to_full_image():
    from PIL import Image

    from core.model3d.ocr.service import _recognize_tiled

    class EchoBackend:
        """每块都在本块 (10,10)-(50,30) 报一个 token——验证平移与去重。"""

        name = "echo"

        def is_available(self):
            return True

        def recognize(self, image_rgb, warnings):
            return [("T", (10.0, 10.0, 50.0, 30.0), 0.9)]

    # 3000x1000 → x 向 3 块(0/1400/... 贴齐末端), y 向 1 块
    image = Image.new("RGB", (3000, 1000), "white")
    out = _recognize_tiled(EchoBackend(), image, [])
    xs = sorted(round(t[1][0]) for t in out)
    assert xs[0] == 10                      # 第一块原位
    assert all(x > 10 for x in xs[1:])      # 其余块坐标已平移
    assert len(out) == len(set(xs))         # 平移后不重叠,全保留


def test_parse_rapid_output_legacy_list():
    from core.model3d.ocr.rapid_backend import parse_rapid_output

    raw = [
        [[(100, 200), (160, 200), (160, 220), (100, 220)], "±0.000", 0.97],
        ["bad"],  # 畸形行跳过
    ]
    out = parse_rapid_output(raw)
    assert out == [("±0.000", (100.0, 200.0, 160.0, 220.0), 0.97)]


def test_parse_rapid_output_object_form():
    from core.model3d.ocr.rapid_backend import parse_rapid_output

    class FakeOut:
        boxes = [[(10, 10), (50, 10), (50, 30), (10, 30)]]
        txts = ["会议室"]
        scores = [0.88]

    out = parse_rapid_output(FakeOut())
    assert out == [("会议室", (10.0, 10.0, 50.0, 30.0), 0.88)]


def test_parse_rapid_output_none():
    from core.model3d.ocr.rapid_backend import parse_rapid_output

    assert parse_rapid_output(None) == []


def test_as_geometry_texts_filters_and_formats():
    from core.model3d.ocr.consume import as_geometry_texts

    result = OcrResult(
        tokens=(
            TextToken("±0.000", (100, 200, 160, 220), 0.98, "elevation", 0.0),
            TextToken("+3.600", (100, 100, 160, 120), 0.75, "elevation", 3.6),  # <0.8 剔除
            TextToken("会议室", (0, 0, 10, 10), 0.99, "room_name"),             # 非标高剔除
        ),
        backend="mock",
    )
    texts = as_geometry_texts(result)
    # 仅高置信标高，(x_min, y_min, text) 与 fitz words 口径一致
    assert texts == [(100.0, 200.0, "±0.000")]


def test_ocr_elevation_tokens_feed_section_extractor():
    """端到端合成链：OCR 标高 token → 合成几何文本 → extract_section_levels 出 marks。

    这是 section-z OCR 兜底的核心正确性：合成条目必须能被现有 extractor 的
    标高解析、水平线绑定与线性标定完整消费（斜率为负：页面 y 向下、标高向上）。
    """
    from dataclasses import replace

    from core.model3d.ocr.consume import as_geometry_texts
    from core.model3d.section_level_extractor import extract_section_levels
    from core.model3d.types import DrawingGeometry

    # 三条水平标高线（y=100/400/700）+ 对应 OCR 标高 token（文本顶贴近线）
    geom = DrawingGeometry(
        page_w=1000, page_h=800,
        lines=[(50, 100, 950, 100), (50, 400, 950, 400), (50, 700, 950, 700)],
    )
    result = OcrResult(
        tokens=(
            TextToken("+7.200", (100, 95, 160, 115), 0.99, "elevation", 7.2),
            TextToken("+3.600", (100, 395, 160, 415), 0.98, "elevation", 3.6),
            TextToken("±0.000", (100, 695, 160, 715), 0.97, "elevation", 0.0),
        ),
        backend="mock",
    )
    merged = replace(geom, texts=[*geom.texts, *as_geometry_texts(result)])
    levels = extract_section_levels(merged)

    assert [m.elevation_m for m in levels.marks] == [0.0, 3.6, 7.2]
    assert all(m.source_ref["bound"] for m in levels.marks)      # 全部绑上标高线
    assert levels.fit["slope_m_per_pt"] < 0                       # 标定方向正确
    assert all(m.confidence >= 0.8 for m in levels.marks)


def test_axis_anchors_and_space_labels():
    from core.model3d.ocr.consume import axis_anchors, space_labels

    result = OcrResult(
        tokens=(
            TextToken("A", (0, 0, 2, 2), 0.8, "axis"),
            TextToken("1", (10, 0, 12, 2), 0.5, "axis"),          # 低置信剔除
            TextToken("会议室", (4, 4, 6, 6), 0.9, "room_name"),
            TextToken("结构平面图", (0, 0, 8, 2), 0.85, "title"),
        ),
        backend="mock",
    )
    anchors = axis_anchors(result)
    assert [a["label"] for a in anchors] == ["A"]
    assert anchors[0]["center"] == (1.0, 1.0)

    labels = space_labels(result)
    assert {l["text"] for l in labels} == {"会议室", "结构平面图"}
