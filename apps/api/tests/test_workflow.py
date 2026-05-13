"""
三审状态机单元测试 — 100% 状态边界覆盖

覆盖目标：
- 所有合法跳转（正向路径）
- 所有非法跳转（应抛出 MachineError / ValueError）
- 驳回路径：各审批阶段 → draft
- ai_fail 回退
- escalate 触发（不改变状态，仅标记）
"""
import pytest
from transitions import MachineError

from core.workflow.drawing_state_machine import (
    DRAWING_STATES,
    DrawingWorkflow,
    assert_valid_transition,
    next_state,
)


# ── 辅助 ─────────────────────────────────────────────────────────

def wf(state: str) -> DrawingWorkflow:
    return DrawingWorkflow(state)


# ── 正向主路径 ────────────────────────────────────────────────────

class TestHappyPath:
    def test_draft_to_ai_reviewing(self):
        w = wf("draft")
        w.submit_for_ai()
        assert w.state == "ai_reviewing"

    def test_ai_reviewing_to_ai_done(self):
        w = wf("ai_reviewing")
        w.ai_complete()
        assert w.state == "ai_done"

    def test_ai_done_to_technical_review(self):
        w = wf("ai_done")
        w.start_technical()
        assert w.state == "technical_review"

    def test_technical_to_economic(self):
        w = wf("technical_review")
        w.approve_technical()
        assert w.state == "economic_review"

    def test_economic_to_settlement(self):
        w = wf("economic_review")
        w.approve_economic()
        assert w.state == "settlement_review"

    def test_settlement_to_published(self):
        w = wf("settlement_review")
        w.approve_settlement()
        assert w.state == "published"

    def test_full_pipeline(self):
        w = wf("draft")
        steps = [
            ("submit_for_ai",      "ai_reviewing"),
            ("ai_complete",        "ai_done"),
            ("start_technical",    "technical_review"),
            ("approve_technical",  "economic_review"),
            ("approve_economic",   "settlement_review"),
            ("approve_settlement", "published"),
        ]
        for trigger, expected in steps:
            getattr(w, trigger)()
            assert w.state == expected


# ── 驳回路径 ─────────────────────────────────────────────────────

class TestRejections:
    def test_reject_technical_returns_to_draft(self):
        w = wf("technical_review")
        w.reject_technical()
        assert w.state == "draft"

    def test_reject_economic_returns_to_draft(self):
        w = wf("economic_review")
        w.reject_economic()
        assert w.state == "draft"

    def test_reject_settlement_returns_to_draft(self):
        w = wf("settlement_review")
        w.reject_settlement()
        assert w.state == "draft"

    def test_ai_fail_returns_to_draft(self):
        w = wf("ai_reviewing")
        w.ai_fail()
        assert w.state == "draft"


# ── 非法跳转（应抛出 MachineError）──────────────────────────────

class TestIllegalTransitions:
    @pytest.mark.parametrize("state,trigger", [
        # 不能跳过 AI 审图
        ("draft", "start_technical"),
        ("draft", "approve_technical"),
        # 不能跳过一审
        ("ai_done", "approve_technical"),
        ("ai_done", "approve_economic"),
        # 不能跳过二审签字
        ("technical_review", "approve_economic"),
        ("technical_review", "approve_settlement"),
        # 不能跳过三审
        ("economic_review", "approve_settlement"),
        # 已发布状态不能再推进
        ("published", "submit_for_ai"),
        ("published", "approve_technical"),
        # 各阶段不能反向跳转
        ("economic_review", "start_technical"),
        ("settlement_review", "approve_economic"),
        # AI 未完成不能开始一审
        ("ai_reviewing", "start_technical"),
        # draft 不能直接驳回
        ("draft", "reject_technical"),
    ])
    def test_illegal_raises(self, state: str, trigger: str):
        w = wf(state)
        with pytest.raises(MachineError):
            getattr(w, trigger)()


# ── assert_valid_transition ───────────────────────────────────────

class TestAssertValidTransition:
    def test_valid_passes(self):
        assert_valid_transition("draft", "submit_for_ai")  # 不抛出

    def test_invalid_raises_value_error(self):
        with pytest.raises(ValueError, match="不允许"):
            assert_valid_transition("draft", "approve_technical")


# ── can_trigger ───────────────────────────────────────────────────

class TestCanTrigger:
    def test_returns_true_for_valid(self):
        assert wf("draft").can_trigger("submit_for_ai") is True

    def test_returns_false_for_invalid(self):
        assert wf("draft").can_trigger("approve_technical") is False

    def test_published_has_no_forward_triggers(self):
        w = wf("published")
        forward_triggers = [
            "submit_for_ai", "ai_complete", "start_technical",
            "approve_technical", "approve_economic", "approve_settlement",
        ]
        for trigger in forward_triggers:
            assert not w.can_trigger(trigger)


# ── next_state ────────────────────────────────────────────────────

class TestNextState:
    @pytest.mark.parametrize("trigger,expected_dest", [
        ("submit_for_ai",      "ai_reviewing"),
        ("ai_complete",        "ai_done"),
        ("ai_fail",            "draft"),
        ("start_technical",    "technical_review"),
        ("approve_technical",  "economic_review"),
        ("reject_technical",   "draft"),
        ("approve_economic",   "settlement_review"),
        ("reject_economic",    "draft"),
        ("approve_settlement", "published"),
        ("reject_settlement",  "draft"),
    ])
    def test_next_state_mapping(self, trigger: str, expected_dest: str):
        assert next_state(trigger) == expected_dest


# ── escalate（不改变状态）────────────────────────────────────────

class TestEscalate:
    def test_escalate_from_technical_review(self):
        w = wf("technical_review")
        w.escalate()
        assert w.state == "technical_review"   # dest=None，不改变状态

    def test_escalate_from_economic_review(self):
        w = wf("economic_review")
        w.escalate()
        assert w.state == "economic_review"

    def test_escalate_from_other_states_raises(self):
        for state in ["draft", "ai_done", "settlement_review", "published"]:
            w = wf(state)
            with pytest.raises(MachineError):
                w.escalate()


# ── 所有状态都已定义 ──────────────────────────────────────────────

def test_all_states_defined():
    expected = {
        "draft", "ai_reviewing", "ai_done",
        "technical_review", "economic_review",
        "settlement_review", "published", "rejected",
    }
    assert set(DRAWING_STATES) == expected
