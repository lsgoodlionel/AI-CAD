"""学习分析器单测(人工标注 → 系统改进建议)。"""
from services.learning_analyzers import (
    ANALYZERS, MIN_EVIDENCE, analyze_manual_axis_drawing, analyze_ocr_corrections,
    analyze_scale_disagreement, analyze_template_gaps, analyze_vocabulary,
    run_analyzers,
)


def _ev(kind, human=None, auto=None, **ctx):
    return {"kind": kind, "human_value": human, "auto_value": auto,
            "context_json": ctx or {}}


# ── 词表扩充(采纳即生效)────────────────────────────────────────

def test_vocabulary_suggests_word_people_keep_typing():
    events = [_ev("discipline", human="舞台机械", raw_text="舞台机械")
              for _ in range(MIN_EVIDENCE)]
    got = analyze_vocabulary(events)
    assert len(got) == 1
    assert got[0]["evidence"]["word"] == "舞台机械"
    assert got[0]["auto_applicable"] is True


def test_vocabulary_ignores_values_system_already_got_right():
    """系统本来就认出来了的,不是学习信号。"""
    events = [_ev("discipline", human="给排水", auto="给排水") for _ in range(5)]
    assert analyze_vocabulary(events) == []


def test_vocabulary_needs_enough_evidence():
    """凭一两次标注推规则会引入噪声,反而拉低下一轮准确率。"""
    events = [_ev("discipline", human="声学") for _ in range(MIN_EVIDENCE - 1)]
    assert analyze_vocabulary(events) == []


def test_vocabulary_confidence_grows_with_evidence():
    few = analyze_vocabulary([_ev("discipline", human="人防") for _ in range(3)])
    many = analyze_vocabulary([_ev("discipline", human="人防") for _ in range(9)])
    assert many[0]["confidence"] > few[0]["confidence"]


# ── OCR 纠错(采纳即生效)────────────────────────────────────────

def test_ocr_correction_learns_stable_misreading():
    """实测「建筑」被认成「建 个人」——重复出现即系统性误识。"""
    events = [_ev("discipline", human="建筑", raw_text="建 个人")
              for _ in range(MIN_EVIDENCE)]
    got = analyze_ocr_corrections(events)
    assert got[0]["evidence"] == {"raw": "建 个人", "corrected": "建筑",
                                  "times": MIN_EVIDENCE}


def test_ocr_correction_skips_when_raw_matches_human():
    events = [_ev("discipline", human="电气", raw_text="电气") for _ in range(5)]
    assert analyze_ocr_corrections(events) == []


def test_ocr_correction_needs_repetition():
    assert analyze_ocr_corrections(
        [_ev("discipline", human="建筑", raw_text="建 个人")]) == []


# ── 阈值调整(采纳即生效)────────────────────────────────────────

def test_axis_threshold_suggested_when_people_mostly_hand_draw():
    events = [_ev("axis", human="1", source="handdrawn", span=0.18)
              for _ in range(4)] + [_ev("axis", human="2", source="candidate")]
    got = analyze_manual_axis_drawing(events)
    assert got[0]["category"] == "threshold"
    assert got[0]["evidence"]["handdrawn"] == 4
    assert got[0]["auto_applicable"] is True


def test_axis_threshold_not_suggested_when_candidates_work():
    events = [_ev("axis", human=str(i), source="candidate") for i in range(6)]
    events.append(_ev("axis", human="x", source="handdrawn", span=0.2))
    assert analyze_manual_axis_drawing(events) == []


def test_axis_threshold_uses_observed_spans():
    events = [_ev("axis", human="1", source="handdrawn", span=0.2),
              _ev("axis", human="2", source="handdrawn", span=0.16),
              _ev("axis", human="3", source="handdrawn", span=0.3)]
    got = analyze_manual_axis_drawing(events)
    # 建议值应低于观测到的最小跨度,才能把这类线纳进来
    assert got[0]["evidence"]["observed_spans"][0] == 0.16
    assert "0.144" in got[0]["title"]


# ── 需开发介入(不可自动生效)────────────────────────────────────

def test_template_gap_is_flagged_as_algorithm_work():
    """宽高比区分不了图框内部布局,属算法改造,不能假装采纳即解决。"""
    events = [_ev("title_block", human="建筑", page_aspect=1.41)
              for _ in range(MIN_EVIDENCE * 2)]
    got = analyze_template_gaps(events)
    assert got[0]["category"] == "algorithm"
    assert got[0]["auto_applicable"] is False


def test_template_gap_requires_stronger_evidence():
    events = [_ev("title_block", human="建筑", page_aspect=1.41)
              for _ in range(MIN_EVIDENCE)]
    assert analyze_template_gaps(events) == []


def test_scale_disagreement_flagged_for_developers():
    events = [_ev("scale", human="1:100", auto="1:2815") for _ in range(4)]
    got = analyze_scale_disagreement(events)
    assert got[0]["auto_applicable"] is False
    assert got[0]["evidence"]["count"] == 4


def test_scale_agreement_produces_nothing():
    events = [_ev("scale", human="1:100", auto="1:100") for _ in range(5)]
    assert analyze_scale_disagreement(events) == []


# ── 编排 ────────────────────────────────────────────────────────

def test_run_analyzers_sorts_by_impact():
    events = ([_ev("discipline", human="舞台机械") for _ in range(8)]
              + [_ev("scale", human="1:100", auto="1:50") for _ in range(3)])
    got = run_analyzers(events)
    assert got[0]["impact"] >= got[-1]["impact"]
    assert {s["category"] for s in got} == {"vocabulary", "algorithm"}


def test_run_analyzers_survives_a_broken_analyzer(monkeypatch):
    """学习是辅助功能,一个维度出错不该让整轮瘫痪。"""
    import services.learning_analyzers as mod

    def boom(_events):
        raise RuntimeError("analyzer down")

    monkeypatch.setattr(mod, "ANALYZERS", (boom, analyze_vocabulary))
    got = mod.run_analyzers([_ev("discipline", human="人防") for _ in range(4)])
    assert len(got) == 1


def test_run_analyzers_on_empty_input():
    assert run_analyzers([]) == []


def test_all_analyzers_return_well_formed_suggestions():
    events = ([_ev("discipline", human="人防", raw_text="人 防") for _ in range(5)]
              + [_ev("axis", human="1", source="handdrawn", span=0.2) for _ in range(4)]
              + [_ev("title_block", human="建筑", page_aspect=1.41) for _ in range(7)]
              + [_ev("scale", human="1:100", auto="1:50") for _ in range(4)])
    required = {"category", "title", "detail", "evidence", "impact",
                "confidence", "auto_applicable"}
    got = run_analyzers(events)
    assert len(ANALYZERS) == 5
    assert got and all(required <= set(s) for s in got)
    assert all(0.0 <= s["confidence"] <= 1.0 for s in got)
