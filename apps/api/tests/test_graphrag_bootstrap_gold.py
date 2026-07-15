"""D-18 评测金标准自举单测（`core/ai_review/graphrag/eval/bootstrap_gold.py`）。

覆盖：
    ① `rows_to_gold`（纯函数）：confirm/reclass 计入金标准；reject 不计入；
       同一 target_id 多条动作取按 created_at 排序后的最新状态；
       note JSON 携带 severity/obligation_level/snippet 时优先采用，缺省回退；
       缺 target_id 的行被诚实丢弃（不臆造 regulation_ref）。
    ② `bootstrap_gold_from_review_actions`：只读查询 + 优雅降级（查询异常返回
       空列表，不级联崩溃）——mock db，离线可跑，不连真实数据库。

对齐 migrations/024_review_actions.sql 的 model_review_actions 表结构与
docs/PHASE_D_GRAPHRAG.md §3.6/§5 的「人审动作埋点回流金标准」设计。
"""
from __future__ import annotations

import json

import pytest

from core.ai_review.graphrag.eval.bootstrap_gold import (
    bootstrap_gold_from_review_actions,
    rows_to_gold,
)


def _row(
    *, project_id="p1", drawing_id="d1", target_id="GB50010-2010 8.2.1",
    action_type="confirm", discipline="structure", note=None, created_at="2026-01-01T00:00:00",
) -> dict:
    return {
        "project_id": project_id, "drawing_id": drawing_id, "target_id": target_id,
        "action_type": action_type, "discipline": discipline, "note": note,
        "created_at": created_at,
    }


# ──────────────────────── ① rows_to_gold（纯函数） ────────────────────────

def test_confirm_action_becomes_gold_with_defaults():
    rows = [_row(action_type="confirm")]
    gold = rows_to_gold(rows)

    assert len(gold) == 1
    assert gold[0].drawing_id == "d1"
    assert gold[0].discipline == "structure"
    assert gold[0].severity == "major"          # 缺省
    assert gold[0].obligation_level == "SHOULD"  # 缺省


def test_reject_action_is_excluded_from_gold():
    rows = [_row(action_type="reject")]
    assert rows_to_gold(rows) == []


def test_latest_action_wins_when_multiple_actions_on_same_target():
    rows = [
        _row(action_type="confirm", created_at="2026-01-01T00:00:00"),
        _row(action_type="reject", created_at="2026-01-02T00:00:00"),  # 最新：否定
    ]
    assert rows_to_gold(rows) == []  # 最新动作是 reject → 不计入


def test_latest_reclass_after_confirm_is_kept():
    rows = [
        _row(action_type="confirm", created_at="2026-01-01T00:00:00",
             note=json.dumps({"severity": "minor"})),
        _row(action_type="reclass", created_at="2026-01-02T00:00:00",
             note=json.dumps({"severity": "critical", "obligation_level": "MUST"})),
    ]
    gold = rows_to_gold(rows)
    assert len(gold) == 1
    assert gold[0].severity == "critical"
    assert gold[0].obligation_level == "MUST"


def test_note_json_overrides_severity_obligation_and_snippet():
    rows = [_row(note=json.dumps({
        "severity": "critical", "obligation_level": "must_not", "snippet": "禁止事项摘要",
    }))]
    gold = rows_to_gold(rows)
    assert gold[0].severity == "critical"
    assert gold[0].obligation_level == "MUST_NOT"
    assert gold[0].snippet == "禁止事项摘要"


def test_malformed_note_json_falls_back_to_defaults():
    rows = [_row(note="不是 JSON")]
    gold = rows_to_gold(rows)
    assert gold[0].severity == "major"
    assert gold[0].obligation_level == "SHOULD"


def test_invalid_severity_or_obligation_in_note_falls_back_to_defaults():
    rows = [_row(note=json.dumps({"severity": "catastrophic", "obligation_level": "PLEASE"}))]
    gold = rows_to_gold(rows)
    assert gold[0].severity == "major"
    assert gold[0].obligation_level == "SHOULD"


def test_missing_target_id_row_is_dropped_not_fabricated():
    rows = [_row(target_id="")]
    assert rows_to_gold(rows) == []


def test_different_targets_produce_independent_gold_entries():
    rows = [
        _row(target_id="GB50010-2010 8.2.1", drawing_id="d1"),
        _row(target_id="GB50011-2010 3.1.1", drawing_id="d1"),
    ]
    gold = rows_to_gold(rows)
    assert len(gold) == 2
    refs = {g.regulation_ref for g in gold}
    assert refs == {"GB500108.2.1", "GB500113.1.1"}


def test_regulation_ref_is_normalized_from_target_id():
    rows = [_row(target_id=" gb50010-2010 8.2.1 ")]
    gold = rows_to_gold(rows)
    assert gold[0].regulation_ref == "GB500108.2.1"


# ──────────────────────── ② bootstrap_gold_from_review_actions ────────────────────────

class _FakeDb:
    def __init__(self, rows=None, raise_on_fetch=False):
        self._rows = rows or []
        self._raise = raise_on_fetch

    async def fetch_all(self, query):
        if self._raise:
            raise RuntimeError("relation model_review_actions does not exist")
        return self._rows


@pytest.mark.asyncio
async def test_bootstrap_returns_gold_from_confirmed_rows():
    db = _FakeDb(rows=[_row(action_type="confirm")])
    gold = await bootstrap_gold_from_review_actions(db)
    assert len(gold) == 1
    assert gold[0].drawing_id == "d1"


@pytest.mark.asyncio
async def test_bootstrap_degrades_gracefully_on_query_failure():
    db = _FakeDb(raise_on_fetch=True)
    gold = await bootstrap_gold_from_review_actions(db)
    assert gold == []


@pytest.mark.asyncio
async def test_bootstrap_returns_empty_list_when_no_compliance_rows_yet():
    """诚实反映当前数据现状（见模块 docstring「已知局限」）：接入未完成前查询
    大概率返回空——这是合法结果，不是错误。"""
    db = _FakeDb(rows=[])
    gold = await bootstrap_gold_from_review_actions(db)
    assert gold == []
