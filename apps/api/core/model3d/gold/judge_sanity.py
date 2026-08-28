"""判读者健全性闸 —— 一批判读结果进金标准之前，先证明它是「看过图」的。

实测触发：图层分类器那一批，**两次不同的图**得到**逐格相同的回答**
（100/100 重合，最高频答案占 69%，confident 全 true，note 全空）。
判读者重放了上一次的答案。没有这道闸，这批会安静地变成金标准 —— 而它
恰恰是用来给「承重函数」定标的，错的真值比没有真值更糟。

四条判据全部取在**退化的极端**上（全同 / 零不确定 / 零备注 / 各层同众数），
不设可调阈值 —— 阈值一旦可调，就会被调到让当前这批通过。
"""
from __future__ import annotations

import collections
from dataclasses import dataclass

# 少于这个格数时，「全部有把握、无备注」是正常的，不构成退化证据。
_MIN_ITEMS_FOR_DEGENERACY = 20


@dataclass(frozen=True)
class JudgeIssue:
    """一条健全性问题。``kind`` 供程序判断，``detail`` 给人看。"""
    kind: str
    detail: str


def _answers(batch: list[dict]) -> dict[str, str]:
    return {str(r.get("id")): str(r.get("what") or r.get("kind") or r.get("system") or "")
            for r in batch}


def check_batch(
    batch: list[dict],
    prior: list[dict] | None = None,
    strata: dict[str, str] | None = None,
) -> list[JudgeIssue]:
    """返回这批判读的健全性问题；空列表表示看不出问题。

    ``prior`` 是上一批的结果（用于查重放），``strata`` 是「编号 → 抽样分层」
    （分层抽样时才有），例如图层分类器批次里每格所属的预测类别。
    """
    issues: list[JudgeIssue] = []
    if not batch:
        return issues
    cur = _answers(batch)

    if prior is not None:
        prev = _answers(prior)
        shared = set(cur) & set(prev)
        if shared and all(cur[k] == prev[k] for k in shared):
            issues.append(JudgeIssue(
                "replay",
                f"与上一批 {len(shared)} 个重合编号逐格相同 —— 判读者在重放而不是看图"))

    if len(batch) >= _MIN_ITEMS_FOR_DEGENERACY:
        no_unsure = all(r.get("confident", True) for r in batch)
        no_notes = not any(str(r.get("note") or "").strip() for r in batch)
        if no_unsure and no_notes:
            issues.append(JudgeIssue(
                "no_uncertainty",
                f"{len(batch)} 格无一标不确定、无一条备注 —— 真看图不会这么齐"))

    if strata:
        by_stratum: dict[str, collections.Counter] = collections.defaultdict(
            collections.Counter)
        for tag, ans in cur.items():
            s = strata.get(tag)
            if s is not None:
                by_stratum[s][ans] += 1
        modes = {s.most_common(1)[0][0] for s in by_stratum.values() if s}
        if len(by_stratum) > 1 and len(modes) == 1:
            issues.append(JudgeIssue(
                "stratum_blind",
                f"{len(by_stratum)} 个抽样分层的众数答案都是 "
                f"「{modes.pop()}」 —— 答案与图无关"))
    return issues
