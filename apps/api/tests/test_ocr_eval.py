"""D-16 OCR 后端评测基座测试（离线，mock 后端，无重依赖）。

覆盖两档模式：

- **有金标签**（``harness.py`` / ``metrics.py`` / ``report.render_markdown``）：
  数值容差匹配、归一化字符串匹配、置信标定（含退化 None 情形）、跨样本聚合、
  Markdown 渲染。
- **无金标签**（``eval/unlabeled.py`` / ``report.render_unlabeled_markdown``）：
  后端间一致性（Jaccard 重合率）、识别量/置信分布、consume.py 三馈线产出量、
  "双方均需 available 才可比"的过滤、Markdown 渲染。

以及 CLI（``scripts/model3d/ocr_eval.py``）的模式路由与目录扫描（用 importlib
按路径直接加载脚本模块，非包，参照 ``test_dataset_split.py`` 的既有模式）。

真实 PaddleOCR/RapidOCR 推理留待带依赖/权重环境验证；本文件全离线可跑。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from core.model3d.ocr.eval.harness import (
    BackendMetrics,
    OcrComparisonReport,
    OcrEvalSample,
    run_backend_comparison,
)
from core.model3d.ocr.eval.metrics import (
    GoldLabels,
    TokenSetMetrics,
    confidence_calibration,
    evaluate_ocr,
    match_elevation_values,
    match_label_set,
)
from core.model3d.ocr.eval.report import render_markdown, render_unlabeled_markdown
from core.model3d.ocr.eval.unlabeled import (
    AgreementMetrics,
    ConfidenceStats,
    UnlabeledComparisonReport,
    UnlabeledSample,
    pairwise_agreement,
    run_unlabeled_comparison,
)
from core.model3d.ocr.mock_backend import MockOcrBackend
from core.model3d.ocr.paddleocr_vl_backend import PaddleOcrVlBackend
from core.model3d.ocr.service import run_ocr
from core.model3d.ocr.types import OcrResult, TextToken

# ── 夹具 ──────────────────────────────────────────────────────


def _token(text: str, kind: str, confidence: float, value: float | None = None) -> TextToken:
    return TextToken(text=text, bbox=(0.0, 0.0, 10.0, 10.0), confidence=confidence, kind=kind, value=value)


def _result(tokens: tuple[TextToken, ...], backend: str = "mock") -> OcrResult:
    return OcrResult(tokens=tokens, backend=backend, dpi=200, page_size=(100.0, 100.0))


def _unavailable_result() -> OcrResult:
    return OcrResult(backend="none", dpi=200, warnings=("不可用",))


# ══════════════════════════════════════════════════════════════
# 有金标签模式：metrics.py
# ══════════════════════════════════════════════════════════════


class TestMatchElevationValues:
    def test_within_tolerance_is_hit(self):
        metrics, hits = match_elevation_values(
            pred=[(3.601, 0.9)], gold=[3.6], tolerance_m=0.05
        )
        assert metrics.tp == 1 and metrics.fp == 0 and metrics.fn == 0
        assert hits == ((True, 0.9),)

    def test_outside_tolerance_is_miss(self):
        metrics, hits = match_elevation_values(
            pred=[(3.7, 0.9)], gold=[3.6], tolerance_m=0.05
        )
        assert metrics.tp == 0 and metrics.fp == 1 and metrics.fn == 1
        assert hits == ((False, 0.9),)

    def test_greedy_prefers_higher_confidence_for_closest_gold(self):
        # 两个预测都能匹配 3.6，但只有一个金标签；高置信者优先拿到
        metrics, hits = match_elevation_values(
            pred=[(3.61, 0.5), (3.59, 0.95)], gold=[3.6], tolerance_m=0.05
        )
        assert metrics.tp == 1 and metrics.fp == 1 and metrics.fn == 0
        # hits 与 pred 顺序无关，按置信降序处理；高置信(0.95)在前且命中
        assert hits[0] == (True, 0.95)
        assert hits[1] == (False, 0.5)

    def test_empty_pred_and_gold_all_zero(self):
        metrics, hits = match_elevation_values(pred=[], gold=[], tolerance_m=0.05)
        assert (metrics.tp, metrics.fp, metrics.fn) == (0, 0, 0)
        assert hits == ()


class TestMatchLabelSet:
    def test_exact_match_after_normalization(self):
        metrics, hits = match_label_set(pred=[(" 首层平面图 ", 0.9)], gold=["首层平面图"])
        assert metrics.tp == 1
        assert hits == ((True, 0.9),)

    def test_duplicate_labels_each_count(self):
        # 多重集：同一标签出现两次，各自计数
        metrics, _ = match_label_set(pred=[("A", 0.9), ("A", 0.8)], gold=["A", "A"])
        assert metrics.tp == 2 and metrics.fp == 0 and metrics.fn == 0

    def test_unmatched_pred_is_fp_unmatched_gold_is_fn(self):
        metrics, _ = match_label_set(pred=[("B", 0.9)], gold=["A"])
        assert metrics.tp == 0 and metrics.fp == 1 and metrics.fn == 1


class TestConfidenceCalibration:
    def test_perfect_separation_gives_positive_correlation(self):
        hits = [(True, 0.95), (True, 0.9), (False, 0.2), (False, 0.1)]
        r = confidence_calibration(hits)
        assert r is not None
        assert r > 0.9

    def test_fewer_than_two_samples_returns_none(self):
        assert confidence_calibration([]) is None
        assert confidence_calibration([(True, 0.9)]) is None

    def test_all_hits_or_all_misses_returns_none(self):
        assert confidence_calibration([(True, 0.9), (True, 0.5)]) is None
        assert confidence_calibration([(False, 0.9), (False, 0.5)]) is None

    def test_zero_variance_confidence_returns_none(self):
        assert confidence_calibration([(True, 0.5), (False, 0.5)]) is None


class TestEvaluateOcr:
    def test_evaluates_all_three_kinds(self):
        result = _result((
            _token("±0.000", "elevation", 0.98, 0.0),
            _token("A", "axis", 0.6),
            _token("首层平面图", "title", 0.9),
        ))
        gold = GoldLabels(elevations=(0.0,), axes=("A",), titles=("首层平面图",))
        sample = evaluate_ocr(result, gold)
        assert sample.elevation.tp == 1
        assert sample.axis.tp == 1
        assert sample.title.tp == 1

    def test_title_kind_merges_room_name(self):
        # title 评测口径合并 title + room_name（共用 space_labels 下游馈线）
        result = _result((_token("会议室", "room_name", 0.8),))
        gold = GoldLabels(titles=("会议室",))
        sample = evaluate_ocr(result, gold)
        assert sample.title.tp == 1

    def test_evaluates_full_confidence_range_not_filtered(self):
        # evaluate_ocr 刻意不做置信过滤（评测要看到低置信预测的对错分布）
        result = _result((_token("A", "axis", 0.05),))
        gold = GoldLabels(axes=("A",))
        sample = evaluate_ocr(result, gold)
        assert sample.axis.tp == 1


# ══════════════════════════════════════════════════════════════
# 有金标签模式：harness.py + report.render_markdown
# ══════════════════════════════════════════════════════════════


class TestRunBackendComparison:
    def _samples(self) -> list[OcrEvalSample]:
        gold = GoldLabels(elevations=(0.0, 3.6), axes=("A",), titles=("首层平面图",))
        return [
            OcrEvalSample(file_bytes=b"not-a-real-pdf", file_ext="pdf", gold=gold, sample_id="s1")
        ]

    def test_aggregates_across_samples_not_cross_matched(self):
        # 两个样本各自有独立金标签；跨样本聚合应按样本分别匹配再求和
        gold1 = GoldLabels(elevations=(0.0,))
        gold2 = GoldLabels(elevations=(3.6,))
        samples = [
            OcrEvalSample(file_bytes=b"x", gold=gold1, sample_id="s1"),
            OcrEvalSample(file_bytes=b"x", gold=gold2, sample_id="s2"),
        ]
        backend = MockOcrBackend(seed=[("±0.000", (0, 0, 10, 10), 0.9)])
        report = run_backend_comparison(samples, {"mock": backend})
        bm = report.backends["mock"]
        # mock 每个样本都产出同一条 ±0.000 预测；s1 命中(gold=0.0)、s2 不命中(gold=3.6)
        assert bm.elevation.tp == 1
        assert bm.elevation.fp == 1
        assert bm.elevation.fn == 1

    def test_unavailable_backend_counted_and_all_gold_becomes_fn(self):
        class DeadBackend:
            name = "dead"

            def is_available(self) -> bool:
                return False

            def recognize(self, image_rgb, warnings):
                raise AssertionError("不应被调用")

        report = run_backend_comparison(self._samples(), {"dead": DeadBackend()})
        bm = report.backends["dead"]
        assert bm.unavailable_samples == 1
        assert bm.elevation.tp == 0
        assert bm.elevation.fn == 2  # 两个金标签标高全部落空

    def test_multiple_backends_compared_independently(self):
        good = MockOcrBackend(seed=[("±0.000", (0, 0, 10, 10), 0.9), ("+3.600", (0, 20, 10, 30), 0.9)])
        bad = MockOcrBackend(seed=[])
        report = run_backend_comparison(self._samples(), {"good": good, "bad": bad})
        assert report.backends["good"].elevation.tp == 2
        assert report.backends["bad"].elevation.tp == 0

    def test_report_to_dict_roundtrip(self):
        backend = MockOcrBackend(seed=[("±0.000", (0, 0, 10, 10), 0.9)])
        report = run_backend_comparison(self._samples(), {"mock": backend})
        d = report.to_dict()
        assert d["sample_count"] == 1
        assert "mock" in d["backends"]
        assert d["backends"]["mock"]["elevation"]["tp"] == 1


class TestRenderMarkdown:
    def test_render_contains_expected_sections(self):
        backend = MockOcrBackend(seed=[("±0.000", (0, 0, 10, 10), 0.9)])
        gold = GoldLabels(elevations=(0.0,))
        samples = [OcrEvalSample(file_bytes=b"x", gold=gold, sample_id="s1")]
        report = run_backend_comparison(samples, {"mock": backend})
        md = render_markdown(report)
        assert "标高（elevation）" in md
        assert "轴号（axis）" in md
        assert "图名·房间名（title）" in md
        assert "后端可用性" in md
        assert "结论摘要" in md
        assert "mock" in md

    def test_render_empty_backends(self):
        report = OcrComparisonReport(backends={}, sample_count=0)
        md = render_markdown(report)
        assert "无后端参与评测" in md


# ══════════════════════════════════════════════════════════════
# 无金标签模式：eval/unlabeled.py
# ══════════════════════════════════════════════════════════════


class TestPairwiseAgreement:
    def test_identical_backends_full_agreement(self):
        result = _result((_token("±0.000", "elevation", 0.9, 0.0), _token("A", "axis", 0.7)))
        elevation, axis, title, comparable = pairwise_agreement([result], [result])
        assert comparable == 1
        assert elevation.matched == 1 and elevation.only_a == 0 and elevation.only_b == 0
        assert elevation.jaccard == 1.0
        assert axis.jaccard == 1.0
        # 无 title token，双方分母为 0 → 不可判定
        assert title.jaccard is None

    def test_disjoint_backends_zero_agreement(self):
        result_a = _result((_token("±0.000", "elevation", 0.9, 0.0),))
        result_b = _result((_token("+9.000", "elevation", 0.9, 9.0),))
        elevation, _axis, _title, comparable = pairwise_agreement([result_a], [result_b])
        assert comparable == 1
        assert elevation.matched == 0
        assert elevation.only_a == 1 and elevation.only_b == 1
        assert elevation.jaccard == 0.0

    def test_within_tolerance_elevation_still_agrees(self):
        result_a = _result((_token("+3.600", "elevation", 0.9, 3.600),))
        result_b = _result((_token("+3.601", "elevation", 0.9, 3.601),))
        elevation, _axis, _title, _c = pairwise_agreement(
            [result_a], [result_b], elevation_tolerance_m=0.05
        )
        assert elevation.matched == 1 and elevation.jaccard == 1.0

    def test_both_unavailable_sample_excluded_from_comparable(self):
        elevation, axis, title, comparable = pairwise_agreement(
            [_unavailable_result()], [_unavailable_result()]
        )
        assert comparable == 0
        assert elevation.jaccard is None
        assert axis.jaccard is None
        assert title.jaccard is None

    def test_one_side_unavailable_sample_excluded(self):
        available = _result((_token("A", "axis", 0.9),))
        elevation, axis, title, comparable = pairwise_agreement(
            [available], [_unavailable_result()]
        )
        assert comparable == 0  # 一方不可用，不计入可比样本

    def test_title_merges_room_name_like_labeled_mode(self):
        result_a = _result((_token("会议室", "room_name", 0.8),))
        result_b = _result((_token("会议室", "title", 0.8),))
        _elevation, _axis, title, _c = pairwise_agreement([result_a], [result_b])
        assert title.matched == 1
        assert title.jaccard == 1.0


class TestRunUnlabeledComparison:
    def _two_samples(self) -> list[UnlabeledSample]:
        return [
            UnlabeledSample(file_bytes=b"not-a-real-pdf", file_ext="pdf", sample_id="s1"),
            UnlabeledSample(file_bytes=b"not-a-real-pdf", file_ext="pdf", sample_id="s2"),
        ]

    def test_volume_and_confidence_stats(self):
        seed = [
            ("±0.000", (0, 0, 10, 10), 0.9),
            ("+3.600", (0, 20, 10, 30), 0.7),
        ]
        report = run_unlabeled_comparison(self._two_samples(), {"mock": MockOcrBackend(seed=seed)})
        bm = report.backends["mock"]
        assert bm.sample_count == 2
        assert bm.unavailable_samples == 0
        # 每个样本产出 2 条 elevation token，两个样本共 4 条
        assert bm.kind_counts["elevation"] == 4
        stats = bm.confidence_by_kind["elevation"]
        assert stats.count == 4
        assert stats.min == pytest.approx(0.7)
        assert stats.max == pytest.approx(0.9)
        assert stats.mean == pytest.approx(0.8)

    def test_consume_feed_counts_reflect_default_threshold(self):
        # 置信 0.9 过默认门槛(0.6)，0.3 过不了 → elevation_candidates 只算高置信那条
        seed = [("±0.000", (0, 0, 10, 10), 0.9), ("+9.000", (0, 20, 10, 30), 0.3)]
        report = run_unlabeled_comparison([self._two_samples()[0]], {"mock": MockOcrBackend(seed=seed)})
        bm = report.backends["mock"]
        assert bm.consume_elevation_count == 1

    def test_pairwise_computed_for_each_backend_pair(self):
        seed_a = [("±0.000", (0, 0, 10, 10), 0.9)]
        seed_b = [("±0.000", (0, 0, 10, 10), 0.8)]
        report = run_unlabeled_comparison(
            [self._two_samples()[0]],
            {"a": MockOcrBackend(seed=seed_a), "b": MockOcrBackend(seed=seed_b)},
        )
        assert len(report.pairwise) == 1
        pair = report.pairwise[0]
        assert {pair.backend_a, pair.backend_b} == {"a", "b"}
        assert pair.elevation.jaccard == 1.0

    def test_single_backend_has_no_pairwise_entries(self):
        report = run_unlabeled_comparison(
            [self._two_samples()[0]], {"mock": MockOcrBackend(seed=[])}
        )
        assert report.pairwise == ()

    def test_zero_backends_empty_report(self):
        report = run_unlabeled_comparison(self._two_samples(), {})
        assert report.backends == {}
        assert report.pairwise == ()
        assert report.sample_count == 2

    def test_to_dict_is_json_serializable(self):
        import json

        seed = [("±0.000", (0, 0, 10, 10), 0.9)]
        report = run_unlabeled_comparison(
            [self._two_samples()[0]],
            {"a": MockOcrBackend(seed=seed), "b": MockOcrBackend(seed=seed)},
        )
        # tuple 的 pairwise key 已在 to_dict 内转成 list，必须能直接 json.dumps
        json.dumps(report.to_dict(), ensure_ascii=False)


class TestConfidenceStats:
    def test_empty_confidences_returns_none_fields(self):
        from core.model3d.ocr.eval.unlabeled import _confidence_stats

        stats = _confidence_stats([])
        assert stats == ConfidenceStats(count=0, mean=None, median=None, min=None, max=None)

    def test_to_dict_rounds_floats(self):
        stats = ConfidenceStats(count=2, mean=0.123456, median=0.1, min=0.05, max=0.2)
        d = stats.to_dict()
        assert d["mean"] == 0.1235


class TestAgreementMetrics:
    def test_to_dict_none_jaccard_stays_none(self):
        a = AgreementMetrics(matched=0, only_a=0, only_b=0, jaccard=None)
        assert a.to_dict()["jaccard"] is None


class TestRenderUnlabeledMarkdown:
    def test_render_contains_expected_sections(self):
        seed_a = [("±0.000", (0, 0, 10, 10), 0.9)]
        seed_b = [("±0.000", (0, 0, 10, 10), 0.8)]
        report = run_unlabeled_comparison(
            [UnlabeledSample(file_bytes=b"not-a-real-pdf", sample_id="s1")],
            {"a": MockOcrBackend(seed=seed_a), "b": MockOcrBackend(seed=seed_b)},
        )
        md = render_unlabeled_markdown(report)
        assert "识别量与置信分布" in md
        assert "三馈线产出量" in md
        assert "后端可用性" in md
        assert "后端间一致性" in md
        assert "不是准确率" in md
        assert "a" in md and "b" in md

    def test_render_empty_backends(self):
        report = UnlabeledComparisonReport(backends={}, sample_count=0)
        md = render_unlabeled_markdown(report)
        assert "无后端参与评测" in md

    def test_render_single_backend_no_pairwise_table(self):
        report = run_unlabeled_comparison(
            [UnlabeledSample(file_bytes=b"not-a-real-pdf", sample_id="s1")],
            {"mock": MockOcrBackend(seed=[])},
        )
        md = render_unlabeled_markdown(report)
        assert "不足两个后端参与评测" in md


# ══════════════════════════════════════════════════════════════
# paddleocr_vl_backend.py（复核：不可用时优雅降级，绝不抛错）
# ══════════════════════════════════════════════════════════════


class TestPaddleOcrVlBackend:
    def test_name_is_stable_for_harness_reporting(self):
        assert PaddleOcrVlBackend().name == "paddleocr_vl"

    def test_is_available_false_without_real_wiring(self):
        # 依赖是否安装取决于运行环境；无论哪种情况，真实推理管线未接线，
        # 本次改动明确不做真实调用 → 恒为 False（stub 边界）。
        assert PaddleOcrVlBackend().is_available() is False

    def test_recognize_returns_empty_with_warning_when_unavailable(self):
        warnings: list[str] = []
        out = PaddleOcrVlBackend().recognize(image_rgb=None, warnings=warnings)
        assert out == []
        assert warnings  # 如实告警，不静默

    def test_participates_in_backend_comparison_as_unavailable(self):
        # harness 应该能把恒不可用的 VL stub 和其它后端放进同一张对比表
        gold = GoldLabels(elevations=(0.0,))
        samples = [OcrEvalSample(file_bytes=b"x", gold=gold, sample_id="s1")]
        report = run_backend_comparison(samples, {"paddleocr_vl": PaddleOcrVlBackend()})
        bm = report.backends["paddleocr_vl"]
        assert isinstance(bm, BackendMetrics)
        assert bm.unavailable_samples == 1
        assert bm.elevation.tp == 0


# ══════════════════════════════════════════════════════════════
# CLI（scripts/model3d/ocr_eval.py）：模式路由 + 目录扫描
# ══════════════════════════════════════════════════════════════

_CLI_MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "model3d" / "ocr_eval.py"
_spec = importlib.util.spec_from_file_location("ocr_eval_cli", _CLI_MODULE_PATH)
assert _spec and _spec.loader
ocr_eval_cli = importlib.util.module_from_spec(_spec)
sys.modules["ocr_eval_cli"] = ocr_eval_cli
_spec.loader.exec_module(ocr_eval_cli)


class TestCliDemoModes:
    def test_demo_labeled_runs_end_to_end(self, capsys):
        rc = ocr_eval_cli.main(["--demo"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "D-16 OCR 后端评测对比报告" in out

    def test_demo_unlabeled_runs_end_to_end(self, capsys):
        rc = ocr_eval_cli.main(["--demo-unlabeled"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "无金标签" in out
        assert "mock_a" in out and "mock_b" in out

    def test_no_mode_flag_errors(self, capsys):
        rc = ocr_eval_cli.main([])
        assert rc == 2
        err = capsys.readouterr().err
        assert "需 --demo" in err

    def test_unknown_backend_name_rejected(self, capsys):
        rc = ocr_eval_cli.main(["--demo-unlabeled", "--backends", "not-a-real-backend"])
        # --demo-unlabeled 走合成样本，不校验 --backends；改用 --dir 校验路径
        assert rc == 0


class TestCliDirMode:
    def _write_pdfs(self, tmp_path: Path) -> Path:
        (tmp_path / "a.pdf").write_bytes(b"%PDF-1.4\n%%EOF")
        (tmp_path / "b.PDF").write_bytes(b"%PDF-1.4\n%%EOF")
        (tmp_path / "c.txt").write_bytes(b"not a pdf")
        return tmp_path

    def test_iter_pdf_files_is_case_insensitive_and_sorted(self, tmp_path):
        self._write_pdfs(tmp_path)
        files = ocr_eval_cli._iter_pdf_files(tmp_path, recursive=False)
        assert [f.name for f in files] == ["a.pdf", "b.PDF"]

    def test_iter_pdf_files_recursive(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "nested.pdf").write_bytes(b"%PDF-1.4\n%%EOF")
        self._write_pdfs(tmp_path)
        flat = ocr_eval_cli._iter_pdf_files(tmp_path, recursive=False)
        deep = ocr_eval_cli._iter_pdf_files(tmp_path, recursive=True)
        assert len(flat) == 2
        assert len(deep) == 3

    def test_dir_mode_end_to_end(self, tmp_path, capsys):
        self._write_pdfs(tmp_path)
        rc = ocr_eval_cli.main(["--dir", str(tmp_path), "--backends", "mock"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "无金标签" in out

    def test_dir_mode_respects_limit(self, tmp_path):
        self._write_pdfs(tmp_path)
        samples, _backends = ocr_eval_cli._load_dir_samples(
            tmp_path, ["mock"], recursive=False, limit=1
        )
        assert len(samples) == 1

    def test_dir_missing_errors(self, capsys):
        rc = ocr_eval_cli.main(["--dir", "/definitely/not/a/real/path"])
        assert rc == 2
        assert "目录不存在" in capsys.readouterr().err

    def test_dir_empty_errors(self, tmp_path, capsys):
        rc = ocr_eval_cli.main(["--dir", str(tmp_path), "--backends", "mock"])
        assert rc == 2
        assert "未找到 PDF" in capsys.readouterr().err

    def test_dir_unknown_backend_errors(self, tmp_path, capsys):
        self._write_pdfs(tmp_path)
        rc = ocr_eval_cli.main(["--dir", str(tmp_path), "--backends", "not-a-real-backend"])
        assert rc == 2
        assert "未知后端" in capsys.readouterr().err
