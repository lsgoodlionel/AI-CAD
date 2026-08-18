"""图纸问题的**代价风险**分级 —— 从 4176 篇会议纪要 / 208 个工程实证得出。

数据来源:`docs/MEETING_MINUTES_MINING.md`(全语料 230,097 行统计,无采样)。
口径:LIFT = 该问题类型行中含**真·代价词**的比率 ÷ 全语料基线率 0.47%。

**三条反直觉但有数据支撑的结论**:

1. LIFT 最高的三项 —— 施工顺序 ×5.80、设计变更 ×4.81、图纸不一致 ×4.16 ——
   **都不是几何问题,而是「过程与版本」问题**,恰是现有清单(纯几何/专业维度)的盲区;
2. 「预留预埋」是绝对量与 LIFT 双高项(3844 行 / ×3.18 / 16 个工程),
   最值得硬编码;
3. 「碰撞」LIFT 仅 ×1.56 —— 但**它只覆盖 2/208 个工程,证据本身就弱**,
   所以本模块给它低置信、不参与压低既有权重(见
   `test_low_coverage_types_are_marked_unreliable`)。

**「拆除」不得进后果词表**:28.9% 的高频几乎全是计划性拆除。
"""
from __future__ import annotations

import pytest

from core.ai_review.cost_risk import (
    BASELINE_COST_RATE, MIN_RELIABLE_PROJECTS, classify_problem_types,
    cost_risk_score, extract_closure_signals, has_cost_consequence,
)


# ── 问题类型识别与 LIFT ─────────────────────────────────────────

@pytest.mark.unit
def test_process_problems_outrank_geometry():
    """**核心用例**:过程/版本问题的代价风险高于几何问题。"""
    order = cost_risk_score("施工顺序未明确,工序穿插冲突")
    geometry = cost_risk_score("尺寸标注与定位存在偏差")
    assert order > geometry


@pytest.mark.unit
def test_embedded_opening_is_high_risk():
    """预留预埋:绝对量与 LIFT 双高(3844 行 / ×3.18 / 16 工程)。"""
    got = classify_problem_types("三层二结构留洞图与管线不匹配,需预留孔洞")
    assert "预留预埋" in got


@pytest.mark.unit
def test_drawing_inconsistency_is_recognised():
    got = classify_problem_types("结构与人防结构图纸不符,图实不一致")
    assert "图纸不一致" in got


@pytest.mark.unit
def test_unrelated_text_has_no_type():
    assert classify_problem_types("会议地点:总包会议室") == []


@pytest.mark.unit
def test_score_of_nothing_is_zero():
    assert cost_risk_score("") == 0.0
    assert cost_risk_score("与会人员签到") == 0.0


# ── 证据强度必须随数据一起走 ────────────────────────────────────

@pytest.mark.unit
def test_low_coverage_types_are_marked_unreliable():
    """**样本小就要说** —— 「碰撞」的 ×1.56 只来自 2/208 个工程。

    反直觉的结论同样要受证据强度约束:不能因为它反直觉就当真理,
    去压低碰撞在既有规则里的权重。
    """
    from core.ai_review.cost_risk import PROBLEM_TYPES

    collision = PROBLEM_TYPES["碰撞冲突"]
    assert collision.projects < MIN_RELIABLE_PROJECTS
    assert not collision.is_reliable, "2/208 工程不足以支撑一个通用权重"
    # 不可靠的类型不参与打分（既不加分也不减分）
    assert cost_risk_score("管线碰撞") == 0.0


@pytest.mark.unit
def test_reliable_types_do_score():
    from core.ai_review.cost_risk import PROBLEM_TYPES

    assert PROBLEM_TYPES["施工顺序"].is_reliable
    assert cost_risk_score("施工顺序冲突") > 0


# ── 真·代价词(「拆除」陷阱)────────────────────────────────────

@pytest.mark.unit
def test_real_consequence_words_are_detected():
    assert has_cost_consequence("需要凿除 45mm 的砼保护层")
    assert has_cost_consequence("二次开洞的相关损失由安装单位承担")
    assert has_cost_consequence("将来返工成本将增大")


@pytest.mark.unit
def test_planned_demolition_is_not_a_consequence():
    """**「拆除」是陷阱**:28.9% 的高频几乎全是计划性拆除。"""
    assert not has_cost_consequence("按图拆除原有隔墙")
    assert not has_cost_consequence("拆除工程量清单")


# ── 闭环信号句式(7 条,各自跨工程数见报告)──────────────────────

@pytest.mark.unit
def test_authoritative_drawing_ruling_is_extracted():
    """**「以 X 图为准」是被低估的金标签**(163 篇 / 31 个工程):
    它是纪要里**图纸版本冲突的裁决记录**,等于人工标注的「多图冲突→执行图」。
    """
    got = extract_closure_signals("结构与人防图纸不符,以首层留洞图为准")
    assert got["authoritative_drawing"]


@pytest.mark.unit
def test_responsible_party_is_extracted():
    """责任单位指派:56/208 工程,覆盖最广的闭环句式。"""
    got = extract_closure_signals("由总包单位负责复核并出图")
    assert got["responsible_party"]


@pytest.mark.unit
def test_deadline_is_extracted():
    got = extract_closure_signals("请于 3月15日前 完成提交")
    assert got["deadline"]
    assert extract_closure_signals("一周内回复")["deadline"]


@pytest.mark.unit
def test_pending_decision_is_flagged():
    """决策悬置 = **未闭环**,审图时要单列。"""
    assert extract_closure_signals("此处做法待设计确认")["pending_decision"]


@pytest.mark.unit
def test_liability_clause_is_extracted():
    got = extract_closure_signals("由此产生的损失由安装单位自行承担")
    assert got["liability"]


@pytest.mark.unit
def test_signals_of_plain_text_are_all_false():
    got = extract_closure_signals("本次会议讨论了进度安排")
    assert not any(got.values())


@pytest.mark.unit
def test_baseline_rate_is_documented():
    """基线率是 LIFT 的分母,必须与报告一致(0.47%)。"""
    assert BASELINE_COST_RATE == pytest.approx(0.0047, abs=1e-4)


# ── 接进审图排序:同 severity 内按实证代价风险排 ──────────────────

@pytest.mark.unit
def test_issue_sort_key_puts_costly_first_within_severity():
    """**核心接线用例**:同为 warning,施工顺序问题排在尺寸偏差之前。

    `severity` 表达「违规严重程度」,代价风险表达「会付出多少代价」——
    实证显示两者不一致,所以**在 severity 之内**再按代价排,
    而不是让代价越权覆盖 severity。
    """
    from core.ai_review.cost_risk import issue_sort_key

    order = issue_sort_key("warning", "施工顺序与工序穿插冲突")
    size = issue_sort_key("warning", "尺寸标注存在偏差")
    assert order < size, "代价高的排前面"


@pytest.mark.unit
def test_severity_still_dominates():
    """**代价不得越权**:再高代价的 warning 也排在 critical 之后。"""
    from core.ai_review.cost_risk import issue_sort_key

    assert issue_sort_key("critical", "普通问题") < issue_sort_key(
        "warning", "施工顺序冲突")


@pytest.mark.unit
def test_unknown_severity_sorts_last():
    from core.ai_review.cost_risk import issue_sort_key

    assert issue_sort_key("nonsense", "x") > issue_sort_key("info", "x")
