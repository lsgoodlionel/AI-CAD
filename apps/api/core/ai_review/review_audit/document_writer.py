"""文书化输出（V2 — 协议 4F 文书化输出）。

把问题包转写为两类文书口径，二者不混写：
- 会审纪要口径：问题条目 / 责任条目 / 结论条目
- 设计答复口径：设计意图 / 执行依据 / 修订说明 / 闭环条件

模板来自 document_templates.yaml，占位符 ``{对象}{concern}{场景}{主责专业}{接口专业}{待明确}``。
A 的 yaml 缺失时降级返回空列表（不抛异常）。
"""
from __future__ import annotations

import logging

from .protocol_loader import load_document_templates

logger = logging.getLogger(__name__)

_DEFAULT_CLARIFY = "对应图纸依据与待确认事项"
_MINUTES_KEY = "纪要口径"
_REPLY_KEY = "答复口径"


def _safe_format(template: str, **values: str) -> str:
    """str.format 填位，缺值填空；任何异常回退原模板。"""
    try:
        return template.format(**{k: (v or "") for k, v in values.items()})
    except (KeyError, IndexError, ValueError):
        return template


def _render_entries(entries: list, values: dict[str, str]) -> list[dict]:
    """渲染一组 ``[{type, text}]`` 条目。"""
    rendered: list[dict] = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        text = entry.get("text")
        if not text:
            continue
        rendered.append(
            {
                "type": str(entry.get("type", "")),
                "text": _safe_format(str(text), **values),
            }
        )
    return rendered


def write(
    discipline_code: str,
    obj: dict,
    question_pack: dict,
    interface: dict,
) -> dict:
    """返回 ``{会审纪要口径:[{type,text}], 设计答复口径:[{type,text}]}``。

    Args:
        discipline_code: 细分专业代码。
        obj: object_identifier 输出 ``{level, object, basis}``，并可携带 ``concern``/``scenario``。
        question_pack: question_pack_builder 输出 ``{主问题, 补充问题, 证据缺口}``。
        interface: classifier 输出 ``{primary, related, reason}``。
    """
    obj = obj or {}
    interface = interface or {}
    docs = load_document_templates().get(discipline_code, {})

    minutes_tmpl = docs.get(_MINUTES_KEY, []) if isinstance(docs, dict) else []
    reply_tmpl = docs.get(_REPLY_KEY, []) if isinstance(docs, dict) else []

    related = interface.get("related", []) or []
    values = {
        "对象": str(obj.get("object", "")),
        "concern": str(obj.get("concern", "")),
        "场景": str(obj.get("scenario", "")),
        "主责专业": str(interface.get("primary", "")),
        "接口专业": "、".join(str(r) for r in related),
        "待明确": _DEFAULT_CLARIFY,
    }

    minutes = _render_entries(minutes_tmpl, values)
    reply = _render_entries(reply_tmpl, values)

    if not minutes and not reply:
        logger.debug("[document_writer] %s 文书模板缺失，降级返回空文书", discipline_code)

    return {"会审纪要口径": minutes, "设计答复口径": reply}
