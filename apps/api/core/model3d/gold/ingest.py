"""判读结果回收：编号纠错 → 有效性 → 分层加权汇总（纯函数，无 IO）。

判读者**字符转写不可靠**（实测曾有 33% 的编号是编的），所以编号带校验位
（`batch_codes`）。回收时先按「本批实际发出的编号」纠错：

    matched     原样对上
    repaired    抄错一位，被校验位唯一纠回
    fabricated  纠不回来 —— 不进统计，但必须报出来（编造率本身是信度信号）

之后分三组汇总：被测组（按层计精确率并加权回语料）、空白对照组
（仪器有效性）、重测副本组（批内信度）。
"""
from __future__ import annotations

import collections

from core.model3d.gold.batch_codes import repair_code
from core.model3d.gold.judge_sanity import check_batch
from core.model3d.gold.taxonomy import canonical_what
from core.model3d.gold.validity import pair_agreement, weighted_precision

_TRUE = {"true", "yes", "1", "是", "t", "y"}


def _as_bool(value) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    return str(value).strip().lower() in _TRUE


def _resolve(answers: list[dict], issued: set[str], field: str):
    """编号纠错；返回 (编号→(判定, 原答), 计数)。同一编号答了两次取第一次。"""
    resolved: dict[str, tuple[bool, dict]] = {}
    counts = collections.Counter()
    for ans in answers:
        raw = str(ans.get("id") or "").strip().upper()
        code = repair_code(raw, issued)
        if code is None:
            counts["fabricated"] += 1
            continue
        counts["matched" if code == raw else "repaired"] += 1
        verdict = _as_bool(ans.get(field))
        if verdict is not None and code not in resolved:
            resolved[code] = (verdict, ans)
    return resolved, counts


def summarize(
    manifest: list[dict],
    answers: list[dict],
    *,
    field: str,
    weights: dict[str, float],
) -> dict:
    """汇总一批判读结果。``manifest`` 行需含 code/group/stratum/dup_of。"""
    issued = {row["code"] for row in manifest}
    resolved, counts = _resolve(answers, issued, field)

    per: dict[str, list[int]] = {}
    fp_labels: collections.Counter = collections.Counter()
    blank_neg = blank_n = 0
    pairs: list[tuple[str, str]] = []
    for row in manifest:
        code, group = row["code"], row["group"]
        if group == "dup":
            pairs.append((row["dup_of"], code))
            continue
        if code not in resolved:
            continue
        verdict, ans = resolved[code]
        if group == "blank":
            blank_n += 1
            blank_neg += int(not verdict)
        elif group == "kept":
            ok_n = per.setdefault(row["stratum"], [0, 0])
            ok_n[0] += int(verdict)
            ok_n[1] += 1
            if not verdict:
                raw = str(ans.get("what") or "").strip()
                fp_labels[canonical_what(raw) or f"未登记:{raw or '空'}"] += 1

    per_stratum = {s: (v[0], v[1]) for s, v in per.items()}
    total_ok = sum(v[0] for v in per_stratum.values())
    total_n = sum(v[1] for v in per_stratum.values())
    wp, coverage = weighted_precision(per_stratum, weights)
    verdicts = {code: v for code, (v, _a) in resolved.items()}
    issues = check_batch([{"id": c, "what": a.get("what") or str(v)}
                          for c, (v, a) in resolved.items()])
    return {
        "matched": counts["matched"],
        "repaired": counts["repaired"],
        "fabricated": counts["fabricated"],
        "per_stratum": per_stratum,
        "raw_precision": (total_ok / total_n) if total_n else None,
        "weighted_precision": wp,
        "coverage": coverage,
        "blank": (blank_neg, blank_n),
        "pair_agreement": pair_agreement(verdicts, pairs),
        "false_positive_labels": dict(fp_labels),
        "judge_issues": [f"{i.kind}: {i.detail}" for i in issues],
    }
