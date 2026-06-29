"""标准问题生成：填充 question_templates 模板，产出可入会审问题单的闭环句子。

协议 4A/4B：
- 识别到对象（构件/部位/系统/节点）优先走对象级模板。
- 无对象时退回专业级问题模板。
- 每条问题应含：对象、冲突点、图纸依据缺口、待明确事项。

可选 LLM 润色：经 ModelRouter 引擎名 review_question_writer；
任何失败/不可用都回退模板原句。
"""
from __future__ import annotations

import logging

from .protocol_loader import load_disciplines, load_templates

logger = logging.getLogger(__name__)

_QUESTION_WRITER_ENGINE = "review_question_writer"
_DEFAULT_CLARIFY = "对应图纸依据与待确认事项"
_MAX_QUESTIONS = 3


def _safe_format(template: str, obj: str, clarify: str) -> str:
    """str.format 填位，缺值填空；任何异常回退原模板。"""
    try:
        return template.format(对象=obj or "", 待明确=clarify or "")
    except (KeyError, IndexError, ValueError):
        return template


def _pick_object(objects_hit: list[str], location: dict) -> str:
    """命中对象名优先，否则用定位信息（轴线/层位/图号）兜底。"""
    if objects_hit:
        return objects_hit[0]
    for kind in ("axes", "levels", "nodes_or_systems", "spaces", "drawings"):
        values = (location or {}).get(kind, [])
        if values:
            return values[0]
    return ""


def _discipline_objects(discipline_code: str) -> list[str]:
    disc = load_disciplines().get(discipline_code, {})
    names: list[str] = []
    for obj in disc.get("objects", []) or []:
        if isinstance(obj, dict) and obj.get("name"):
            names.append(str(obj["name"]))
    return names


def generate(
    discipline_code: str,
    concerns: list[dict],
    objects_hit: list[str],
    location: dict,
    text: str,
) -> list[str]:
    """返回标准问题句子列表（模板填充，不含 LLM）。"""
    templates = load_templates().get(discipline_code, {})
    if not isinstance(templates, dict) or not templates:
        return []

    obj = _pick_object(objects_hit, location)
    clarify = _DEFAULT_CLARIFY
    questions: list[str] = []

    # 4B 对象级优先
    object_templates = templates.get("object", {})
    if isinstance(object_templates, dict):
        for hit in objects_hit:
            tmpl = object_templates.get(hit)
            if tmpl:
                questions.append(_safe_format(str(tmpl), hit, clarify))

    # 4A 专业级问题模板（按 concern 选取，无 concern 取首条）
    problem_templates = templates.get("problem", [])
    if isinstance(problem_templates, list) and problem_templates:
        chosen = _select_problem_templates(problem_templates, concerns)
        for tmpl in chosen:
            questions.append(_safe_format(str(tmpl), obj, clarify))

    # 去重保序
    seen: set[str] = set()
    unique = [q for q in questions if q and not (q in seen or seen.add(q))]
    return unique[:_MAX_QUESTIONS]


def _select_problem_templates(problem_templates: list, concerns: list[dict]) -> list[str]:
    """按 concern.label 与模板 type 模糊匹配选模板；无匹配取首条。"""
    labels = {str(c.get("label", "")) for c in concerns}
    matched: list[str] = []
    for entry in problem_templates:
        if not isinstance(entry, dict):
            continue
        ttype = str(entry.get("type", ""))
        text = entry.get("text")
        if text and any(label and (label in ttype or ttype in label) for label in labels):
            matched.append(str(text))

    if matched:
        return matched[:1]

    first = problem_templates[0]
    if isinstance(first, dict) and first.get("text"):
        return [str(first["text"])]
    return []


async def polish_with_llm(questions: list[str], router) -> list[str]:
    """可选 LLM 润色；router 为 None 或任何失败都回退原句。

    需要 await，仅在 ReviewAuditEngine.analyze 中调用（纯函数 audit_text 不调用）。
    """
    if router is None or not questions:
        return questions

    polished: list[str] = []
    for question in questions:
        polished.append(await _polish_one(question, router))
    return polished


async def _polish_one(question: str, router) -> str:
    messages = [
        {
            "role": "system",
            "content": "你是图纸会审问题撰写助手。润色为通顺、可直接入会审问题单的闭环句子，"
            "保留专业前缀标签、对象、冲突点、图纸依据缺口与待明确事项，不改变事实，只输出问题本身。",
        },
        {"role": "user", "content": question},
    ]
    try:
        response = await router.route(_QUESTION_WRITER_ENGINE, messages)
        content = getattr(response, "content", "") or ""
        polished = content.strip()
        return polished or question
    except Exception as exc:  # noqa: BLE001 - 润色失败必须静默回退
        logger.debug("[review_question_writer] 润色失败，回退模板原句: %s", exc)
        return question
