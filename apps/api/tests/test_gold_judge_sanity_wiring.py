"""判读健全性闸要真的看到「不确定」与备注 —— 此前它是瞎的。

**实测**（col3 批导入，2026-09-15）：判读者 61 格里有 18 格 `confident:false`、
每格都有 `saw` 描述，导入却报「61 格无一标不确定、无一条备注 —— 真看图不会这么齐」。

原因：`summarize` 调 `check_batch` 时只传了 `id` 与 `what`，`confident` 与备注都没传，
而检查器对缺省的 `confident` 按 True 算、备注只认 `note`。于是这条闸**对每一批都报**，
等于从未生效 —— 真出现「重放答案」时也分辨不出来。
"""
from __future__ import annotations

import pytest

from core.model3d.gold.batch_codes import make_codes
from core.model3d.gold.ingest import summarize


def _batch(n: int, *, confident: bool, saw: str):
    codes = make_codes(n, seed=11)
    manifest = [{"code": c, "group": "kept", "stratum": "S", "drawing_id": f"d{i}",
                 "dup_of": "", "drawing_candidates": "10"} for i, c in enumerate(codes)]
    answers = [{"id": c, "is_column": False, "what": "text", "saw": saw, "confident": confident}
                for c in codes]
    return manifest, answers


def _issue_kinds(summary: dict) -> set[str]:
    return {issue.split(":")[0] for issue in summary["judge_issues"]}


@pytest.mark.unit
def test_uncertainty_and_notes_reach_the_sanity_check():
    manifest, answers = _batch(30, confident=True, saw="红框压在文字上")
    answers[0] = {**answers[0], "confident": False}
    s = summarize(manifest, answers, field="is_column", weights={"S": 1.0})
    assert "no_uncertainty" not in _issue_kinds(s)


@pytest.mark.unit
def test_degenerate_batch_is_still_flagged():
    """闸没被拆掉：全有把握、零备注的一批照样要报。"""
    manifest, answers = _batch(30, confident=True, saw="")
    s = summarize(manifest, answers, field="is_column", weights={"S": 1.0})
    assert "no_uncertainty" in _issue_kinds(s)
