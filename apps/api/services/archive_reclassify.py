"""档案原地重分类:分类规则改进后无需重抽 OCR 即可回灌。

档案里存的是**抽取当时的分类快照**。改了 `classify_text` 的判据之后，
旧数据不会自己变——而重抽 2309 张图要跑 OCR，很贵。

但分类只是对已存的 `content` 跑一个**纯函数**，原地重算即可。
这让「改判据 → 全量回灌」从一件昂贵的事变成一件便宜的事。

**两条不可越界的规则**：

1. 只重算 `extractor ∈ {ocr, vector_text}`——`vlm` 走的是另一套语义，
   不归 `classify_text` 管；
2. **绝不动 `source_kind='verified'`**——那是人工审核过的，
   规则改了也不能推翻人（Phase E1.5 auto/verified 分离的同一条原则）。
"""
from __future__ import annotations

from typing import Any, Iterable

from core.model3d.ocr.classify import classify_text

#: 走 `classify_text` 的抽取器。`vlm` 不在其中——它的类别由 LLM 语义给出。
RECLASSIFIABLE_EXTRACTORS = frozenset({"ocr", "vector_text"})

#: 人工审核过的来源，规则改动不得推翻。
PROTECTED_SOURCE_KINDS = frozenset({"verified"})


def plan_reclassify(rows: Iterable[dict]) -> list[dict[str, Any]]:
    """算出需要改类别的行。**只产出真的变了的行。**

    每行需含 ``id`` / ``content`` / ``category`` / ``extractor`` / ``source_kind``。
    返回 ``[{"id", "category", "was"}]``——留下 `was` 便于事后核对。

    纯函数：不碰数据库，可离线全量测。
    """
    plan: list[dict[str, Any]] = []
    for row in rows:
        if str(row.get("source_kind") or "") in PROTECTED_SOURCE_KINDS:
            continue
        if str(row.get("extractor") or "") not in RECLASSIFIABLE_EXTRACTORS:
            continue
        content = str(row.get("content") or "").strip()
        if not content:
            continue
        category, _value = classify_text(content)
        if category != row.get("category"):
            plan.append({"id": row["id"], "category": category,
                         "was": row.get("category")})
    return plan
