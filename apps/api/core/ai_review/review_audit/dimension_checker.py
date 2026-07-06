"""五维审查（V4 — 蒸馏自 drawing_review_core_principles 审查顺序）。

固定顺序逐维检查：完整性 → 界面一致性 → 可施工性 → 验收可达性 → 闭环性。

每维输出 ``{维度, 状态, 依据, 追问}``：
- 状态 ``存疑``：正文命中该维度问题信号 → 依据给出命中信号；
- 状态 ``待核``：未命中信号（文本没提不代表没问题）→ 追问给出该维问题集提问，供人工核对。

纯模板确定性逻辑，无 LLM、无 db；yaml 缺失时返回空列表。
"""
from __future__ import annotations

import logging

from .protocol_loader import load_review_methodology

logger = logging.getLogger(__name__)

_STATUS_SUSPECT = "存疑"
_STATUS_PENDING = "待核"

# 问题归类 → 五维映射（正文信号之外的补充判定）
_CLASS_TO_DIMENSION: dict[str, str] = {
    "表达遗漏": "完整性",
    "图纸冲突": "界面一致性",
    "接口冲突": "界面一致性",
    "施工条件问题": "可施工性",
    "验收风险": "验收可达性",
}


def _hit_signals(signals: list[str], text: str) -> list[str]:
    return [s for s in signals if s and s in text]


def check(
    text: str,
    location: dict,
    concerns: list[dict],
    issue_class: list[str],
) -> list[dict]:
    """按稳定顺序逐维审查，返回 ``[{维度, 状态, 依据, 追问}, ...]``。"""
    text = text or ""
    issue_class = issue_class or []
    dimensions = load_review_methodology().get("dimensions", [])

    class_dims = {
        _CLASS_TO_DIMENSION[c] for c in issue_class if c in _CLASS_TO_DIMENSION
    }

    rows: list[dict] = []
    for dim in dimensions:
        name = str(dim.get("name", ""))
        signals = [str(s) for s in dim.get("signals", []) or []]
        question = str(dim.get("question", ""))

        hits = _hit_signals(signals, text)
        from_class = name in class_dims

        if hits or from_class:
            basis_parts: list[str] = []
            if hits:
                basis_parts.append(f"正文命中信号「{'、'.join(hits[:3])}」")
            if from_class:
                mapped = [c for c in issue_class if _CLASS_TO_DIMENSION.get(c) == name]
                basis_parts.append(f"问题归类「{'、'.join(mapped)}」映射到本维")
            rows.append(
                {"维度": name, "状态": _STATUS_SUSPECT, "依据": "；".join(basis_parts), "追问": question}
            )
        else:
            rows.append({"维度": name, "状态": _STATUS_PENDING, "依据": "", "追问": question})
    return rows


def hit_priority_objects(text: str) -> list[dict]:
    """识别高频审查对象命中（按历史样本权重降序）。"""
    text = text or ""
    objects = load_review_methodology().get("priority_objects", [])
    hits: list[dict] = []
    for obj in objects:
        keywords = [str(k) for k in obj.get("keywords", []) or []]
        matched = [k for k in keywords if k and k in text]
        if matched:
            hits.append(
                {
                    "name": str(obj.get("name", "")),
                    "weight": int(obj.get("weight", 0) or 0),
                    "hit": "、".join(matched),
                }
            )
    hits.sort(key=lambda o: -o["weight"])
    return hits
