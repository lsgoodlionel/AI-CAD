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
