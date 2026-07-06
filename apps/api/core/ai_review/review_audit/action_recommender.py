"""结构化处理建议 + 闭环要求（V4 — 蒸馏自 drawing_review_output_template）。

输出模板要求每条建议落到：动作 / 动作类型（补图|复核|RFI|会签|专题协调）/
责任方 / 配合方 / 输出件；并给出闭环要求（是否影响开工/穿插/需要专题会/下次复核节点）。

纯模板确定性逻辑，无 LLM、无 db；yaml 缺失时优雅降级为默认 RFI 建议。
"""
from __future__ import annotations

import logging

from .protocol_loader import load_review_methodology

logger = logging.getLogger(__name__)

# 动作类型固定输出顺序（先消图纸冲突，再补图，再澄清，再协调，最后会签下发）
_TYPE_ORDER = ("复核", "补图", "RFI", "专题协调", "会签")
# 处理建议保持紧凑，不堆砌低密度动作
_MAX_ACTIONS = 4

_DEFAULT_OBJECT = "该问题部位"
_DEFAULT_RESPONSIBLE = "设计单位"
# 专题协调由总包牵头（高频责任方先验：总包 3519 为最高频协调主体）
_COORDINATION_LEAD = "总包"

# 补图复合判定：正文出现「缺/无/补」+ 图纸表达对象 即视为需要补图
_SUPPLEMENT_VERBS = ("缺", "无", "补")
_SUPPLEMENT_OBJECTS = ("大样", "详图", "节点图", "做法", "说明")

# 闭环要求信号
_BLOCK_START_SIGNALS = ("影响开工", "无法开工", "开工", "基坑", "围护", "桩基")
_BLOCK_INSERT_SIGNALS = ("穿插", "预留", "预埋", "安装顺序", "工作面", "移交")

_ACTION_TEXT: dict[str, str] = {
    "复核": "并列核对{obj}相关平面/剖面/节点/系统图，明确以哪张图为准并复核修订",
    "补图": "补充{obj}节点详图/大样，并以书面说明明确构造做法",
    "RFI": "以设计澄清（RFI）明确{obj}待明确事项及对应图纸依据",
    "专题协调": "组织相关专业联合会审，明确{obj}接口关系与责任边界",
    "会签": "完成修订图纸审批会签并正式下发至现场",
}


def _needs_supplement(text: str) -> bool:
    """复合判定补图需求：动词（缺/无/补）与图纸表达对象（大样/详图…）同时出现。"""
    return any(v in text for v in _SUPPLEMENT_VERBS) and any(
        o in text for o in _SUPPLEMENT_OBJECTS
    )


def _hit_action_types(text: str, action_types: dict[str, list[str]]) -> list[str]:
    hit = {
        name
        for name, signals in action_types.items()
        if any(s and s in text for s in signals)
    }
    if _needs_supplement(text):
        hit.add("补图")
    ordered = [name for name in _TYPE_ORDER if name in hit]
    return ordered or ["RFI"]  # 无信号时至少形成一条澄清动作（问题必须有动作出口）


def _responsible(action_type: str) -> str:
    return _COORDINATION_LEAD if action_type == "专题协调" else _DEFAULT_RESPONSIBLE


def _collaborators(action_type: str, related: list[str]) -> list[str]:
    """配合方 = 接口专业（前2）+ 牵头方之外的另一主体。"""
    partners = [r for r in related[:2] if r]
    other_lead = (
        _DEFAULT_RESPONSIBLE if action_type == "专题协调" else _COORDINATION_LEAD
    )
    if other_lead not in partners:
        partners.append(other_lead)
    return partners


def recommend(
    text: str,
    classification: dict,
    scenario: dict,
    obj: dict,
) -> dict:
    """返回 ``{处理建议:[{动作,动作类型,责任方,配合方,输出件}], 闭环要求:{...}}``。"""
    text = text or ""
    classification = classification or {}
    scenario = scenario or {}
    obj = obj or {}

    methodology = load_review_methodology()
    action_types = methodology.get("action_types", {})
    action_outputs = methodology.get("action_outputs", {})

    object_name = str(obj.get("object", "")) or _DEFAULT_OBJECT
    interface = classification.get("interface", {}) or {}
    related = [str(i) for i in interface.get("related", []) or []]

    hit_types = _hit_action_types(text, action_types)[:_MAX_ACTIONS]
    actions = [
        {
            "动作": _ACTION_TEXT.get(t, "").format(obj=object_name),
            "动作类型": t,
            "责任方": _responsible(t),
            "配合方": _collaborators(t, related),
            "输出件": str(action_outputs.get(t, "")) or "书面回复与修订依据",
        }
        for t in hit_types
    ]

    return {"处理建议": actions, "闭环要求": _closure_requirements(text, classification, scenario, hit_types)}


def _closure_requirements(
    text: str, classification: dict, scenario: dict, hit_types: list[str]
) -> dict:
    """闭环要求（输出模板固定四项）。"""
    risk_level = str((classification.get("risk", {}) or {}).get("level", ""))
    scenario_name = str(scenario.get("name", ""))

    blocks_start = risk_level == "高" and any(s in text for s in _BLOCK_START_SIGNALS)
    blocks_insert = any(s in text for s in _BLOCK_INSERT_SIGNALS) or (
        scenario_name == "施工落地"
    )
    needs_meeting = "专题协调" in hit_types

    review_node = (
        "责任方完成补图/答复后组织专项复核，建议安排在下次会审或该部位施工前"
    )
    return {
        "是否影响开工": blocks_start,
        "是否影响穿插": blocks_insert,
        "是否需要专题会": needs_meeting,
        "下次复核节点": review_node,
    }
