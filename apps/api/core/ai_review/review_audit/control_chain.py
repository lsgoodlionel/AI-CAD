"""六步控制链（V4 — 蒸馏自 meeting_ai_agent_principles 核心流程）。

稳定序列：触发 → 边界 → 风险 → 责任 → 动作 → 闭环。

底层原则（agent 原则文档）：
- 会审记录是工程控制记录，要还原成控制逻辑而不是语言摘要；
- 若正文没有明确动作或责任，必须标记「闭环不足」并输出追问项。

纯模板确定性逻辑，无 LLM、无 db；yaml 缺失时优雅降级。
"""
from __future__ import annotations

import logging

from .protocol_loader import load_review_methodology

logger = logging.getLogger(__name__)

_CLOSURE_OK = "闭环完整"
_CLOSURE_INSUFFICIENT = "闭环不足"

_DEFAULT_RESPONSIBLE = "设计单位"


def _identify_parties(text: str, parties: list[dict]) -> list[str]:
    """按关键词识别正文卷入的责任方，按历史样本权重降序。"""
    hits: list[tuple[int, str]] = []
    for party in parties:
        name = str(party.get("name", ""))
        keywords = [str(k) for k in party.get("keywords", []) or []]
        if name and any(k and k in text for k in keywords):
            hits.append((int(party.get("weight", 0) or 0), name))
    hits.sort(key=lambda p: -p[0])
    return [name for _, name in hits]


def _closure_verdict(text: str, methodology: dict) -> dict:
    """闭环四要素检测 → ``{status, 缺失项, 追问项}``。"""
    elements: dict[str, list[str]] = methodology.get("closure_elements", {})
    followups: dict[str, str] = methodology.get("closure_followups", {})

    missing = [
        element
        for element, words in elements.items()
        if not any(w and w in text for w in words)
    ]
    if not elements:
        # 资产缺失时不做误导性判定
        return {"status": _CLOSURE_INSUFFICIENT, "缺失项": [], "追问项": []}

    status = _CLOSURE_OK if not missing else _CLOSURE_INSUFFICIENT
    questions = [followups[m] for m in missing if followups.get(m)]
    return {"status": status, "缺失项": missing, "追问项": questions}


def build(
    text: str,
    classification: dict,
    scenario: dict,
    action_names: list[str],
) -> dict:
    """组装六步控制链。

    Args:
        text: 标题 + 正文合并文本。
        classification: classifier 输出 ``{issue_class, risk, interface}``。
        scenario: scenario_router 输出 ``{name, priority_reason}``。
        action_names: 结构化处理建议中的动作名列表（action_recommender 产出）。
    """
    text = text or ""
    classification = classification or {}
    scenario = scenario or {}
    methodology = load_review_methodology()

    risk = classification.get("risk", {}) or {}
    interface = classification.get("interface", {}) or {}

    trigger = str(scenario.get("priority_reason", "")) or str(risk.get("trigger", ""))
    related = [str(i) for i in interface.get("related", []) or []]
    boundary = " / ".join(
        part for part in (str(interface.get("primary", "")), "、".join(related)) if part
    )
    risk_line = "，".join(
        part for part in (str(risk.get("level", "")), str(risk.get("trigger", ""))) if part
    )

    parties = _identify_parties(text, methodology.get("responsible_parties", []))
    if not parties:
        parties = [_DEFAULT_RESPONSIBLE]

    verdict = _closure_verdict(text, methodology)
    closure_line = (
        "闭环要素齐备（责任方/时限/输出件/确认方式）"
        if verdict["status"] == _CLOSURE_OK
        else f"缺少{('、'.join(verdict['缺失项'])) or '闭环要素'}，需按追问项补齐后关闭"
    )

    return {
        "触发": trigger,
        "边界": boundary,
        "风险": risk_line,
        "责任": "、".join(parties),
        "动作": list(action_names or []),
        "闭环": closure_line,
        "闭环判定": verdict,
    }
