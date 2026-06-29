"""问题包构建（V2 — 协议 4E 问题包输出）。

输出三段式问题包：主问题 / 补充问题 / 证据缺口。

主问题生成优先级（CONTRACT V2-4）：
1. scenario_templates 场景模板（按 obj.level + scenario.name 命中专业-对象条目）。
2. 回退 question_pack_templates 填位（``{对象}{待明确}{级别}{concern}``）。
证据缺口按 location 缺失项动态生成。
"""
from __future__ import annotations

import logging

from .protocol_loader import load_question_pack_templates, load_scenario_templates

logger = logging.getLogger(__name__)

_DEFAULT_CLARIFY = "对应图纸依据与待确认事项"
_LOCATION_LABELS: dict[str, str] = {
    "drawings": "图号",
    "levels": "层位",
    "axes": "轴线",
    "nodes_or_systems": "节点号/系统号",
    "spaces": "房间/设备名称",
}


def _safe_format(template: str, **values: str) -> str:
    """str.format 填位，缺值填空；任何异常回退原模板。"""
    try:
        return template.format(**{k: (v or "") for k, v in values.items()})
    except (KeyError, IndexError, ValueError):
        return template


def _format_pack(template: str, obj: dict, concern_label: str) -> str:
    return _safe_format(
        str(template),
        对象=str(obj.get("object", "")),
        待明确=_DEFAULT_CLARIFY,
        级别=str(obj.get("level", "")),
        concern=concern_label,
    )


def _scenario_main(discipline_code: str, obj: dict, scenario: dict) -> str:
    """从 scenario_templates 取主问题：按对象名匹配条目，按场景名取模板。"""
    obj_name = str(obj.get("object", ""))
    obj_level = str(obj.get("level", ""))
    scenario_name = str((scenario or {}).get("name", ""))
    if not obj_name or not scenario_name:
        return ""

    entries = load_scenario_templates().get(discipline_code, [])
    for entry in entries:
        if str(entry.get("object", "")) != obj_name:
            continue
        if obj_level and str(entry.get("level", "")) and str(entry["level"]) != obj_level:
            continue
        template = entry.get(scenario_name) or entry.get("图间冲突") or entry.get("正常审图")
        if template:
            return str(template)
    return ""


def _pack_main(discipline_code: str, obj: dict, concern_label: str) -> str:
    pack = load_question_pack_templates().get(discipline_code, {})
    template = pack.get("主问题")
    if template:
        return _format_pack(template, obj, concern_label)
    return ""


def _pack_supplement(discipline_code: str, obj: dict, concern_label: str) -> str:
    pack = load_question_pack_templates().get(discipline_code, {})
    template = pack.get("补充问题")
    if template:
        return _format_pack(template, obj, concern_label)
    return ""


def _evidence_gap(discipline_code: str, location: dict) -> str:
    """按 location 缺失项动态生成证据缺口。"""
    missing = [
        label
        for key, label in _LOCATION_LABELS.items()
        if not (location or {}).get(key)
    ]
    if missing:
        return f"请补充{('、'.join(missing))}及关联专业图纸依据后再闭环。"

    # 定位齐全时回退模板缺口口径
    pack = load_question_pack_templates().get(discipline_code, {})
    template = pack.get("证据缺口")
    if template:
        return str(template)
    return "请补充对应图纸依据及关联专业图纸后再闭环。"


def _first_concern_label(concerns: list) -> str:
    for concern in concerns or []:
        label = str((concern or {}).get("label", "")).strip()
        if label:
            return label
    return ""


def build(
    discipline_code: str,
    obj: dict,
    scenario: dict,
    location: dict,
    concerns: list,
) -> dict:
    """返回 ``{主问题, 补充问题, 证据缺口}``。

    Args:
        discipline_code: 细分专业代码。
        obj: object_identifier 输出 ``{level, object, basis}``。
        scenario: scenario_router 输出 ``{name, priority_reason}``。
        location: location_extractor 输出五类定位。
        concerns: concern_extractor 输出 ``[{label, reason}, ...]``。
    """
    obj = obj or {}
    concern_label = _first_concern_label(concerns)

    main = _scenario_main(discipline_code, obj, scenario or {})
    if not main:
        main = _pack_main(discipline_code, obj, concern_label)

    supplement = _pack_supplement(discipline_code, obj, concern_label)
    gap = _evidence_gap(discipline_code, location or {})

    return {"主问题": main, "补充问题": supplement, "证据缺口": gap}
