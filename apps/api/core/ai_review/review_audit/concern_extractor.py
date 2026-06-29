"""核心 concern 抽取：按专业 priority_concerns 优先 + concern_keywords 命中。

固定执行协议第3步：优先识别标高/尺寸/节点/做法/系统/回路/预留/材料/可施工性等高频信号。
输出 1–3 个高密度 concern（priority 排前，命中数多者优先）。
"""
from __future__ import annotations

import logging

from .protocol_loader import load_concern_keywords, load_disciplines

logger = logging.getLogger(__name__)

_MAX_CONCERNS = 3


def _hit_count(keywords: list[str], text: str) -> int:
    return sum(1 for kw in keywords if kw and kw in text)


def extract(discipline_code: str, text: str) -> list[dict]:
    """返回 ``[{label, reason}, ...]``，1–3 条。"""
    text = text or ""
    if not text:
        return []

    keyword_map = load_concern_keywords()
    disc = load_disciplines().get(discipline_code, {})
    priority = [str(c) for c in disc.get("priority_concerns", []) or []]
    priority_set = set(priority)

    scored: list[tuple[int, int, str, str]] = []  # (是优先?, 命中数, label, 命中词)
    for label, keywords in keyword_map.items():
        hits = [kw for kw in keywords if kw and kw in text]
        if not hits:
            continue
        is_priority = 1 if label in priority_set else 0
        scored.append((is_priority, len(hits), label, hits[0]))

    # 优先专业 priority_concerns 排最前，其次命中数高者
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)

    concerns: list[dict] = []
    for is_priority, _count, label, sample in scored[:_MAX_CONCERNS]:
        reason = (
            f"该专业优先关注项，正文命中「{sample}」"
            if is_priority
            else f"正文命中关键词「{sample}」"
        )
        concerns.append({"label": label, "reason": reason})

    return concerns
