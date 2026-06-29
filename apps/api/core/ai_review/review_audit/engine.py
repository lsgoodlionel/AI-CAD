"""会审审查引擎主入口。

- ``audit_text(...)``：纯函数文本审查，返回契约第3节 ``data`` 结构（中文 key）。
  无 db / 无 LLM 也能运行；yaml 缺失时优雅降级为空结构。
- ``ReviewAuditEngine(BaseEngine)``：engine_name="review"，串接四引擎协调器。
"""
from __future__ import annotations

import logging

from ..base import AIIssue, BaseEngine, DrawingContext, IssueSeverity
from . import (
    classifier,
    concern_extractor,
    document_writer,
    location_extractor,
    object_identifier,
    question_generator,
    question_pack_builder,
    scenario_router,
)
from .discipline_router import route as route_discipline
from .protocol_loader import load_disciplines

logger = logging.getLogger(__name__)

# 风险等级 → severity（契约2）；命中安全/消防/主系统 → critical
_RISK_SEVERITY = {
    "高": IssueSeverity.MAJOR,
    "中": IssueSeverity.MINOR,
    "低": IssueSeverity.INFO,
}
_CRITICAL_SIGNALS = ("安全", "消防", "主系统", "基坑", "人防")


def _hit_objects(discipline_code: str, text: str) -> list[tuple[str, str]]:
    """返回正文命中的对象 ``[(name, level), ...]``。"""
    disc = load_disciplines().get(discipline_code, {})
    hits: list[tuple[str, str]] = []
    for obj in disc.get("objects", []) or []:
        if not isinstance(obj, dict):
            continue
        name = str(obj.get("name", ""))
        level = str(obj.get("level", ""))
        if name and name in text:
            hits.append((name, level))
    return hits


def _object_level(object_hits: list[tuple[str, str]]) -> str:
    return object_hits[0][1] if object_hits else ""


def _evidence_gap(location: dict, concerns: list[dict]) -> list[str]:
    """证据缺口：无定位则提示先补定位；否则提示补对应图纸依据。"""
    gaps: list[str] = []
    has_location = any(location.get(k) for k in location)
    if not has_location:
        gaps.append("缺少图号/层位/轴线等定位信息，需先补充定位证据")
    for concern in concerns:
        label = concern.get("label", "")
        if label:
            gaps.append(f"缺少与「{label}」对应的图纸依据（平面/剖面/节点/系统图）")
    if not gaps:
        gaps.append("需回原图核对，补充对应图纸依据")
    return gaps


def _suggested_actions(
    concerns: list[dict], interface: dict, risk: dict, has_location: bool
) -> list[str]:
    """建议动作：核图 / 提问 / 协调（协议第8步闭环）。"""
    actions: list[str] = []
    if not has_location:
        actions.append("先补充图号/轴线/层位等定位信息再做实体结论")
    actions.append("核图：并列核对平面、剖面、节点、系统图及历史答复，确认以哪张为准")
    related = interface.get("related", [])
    if related:
        actions.append(f"协调：联查 {('、'.join(related))} 专业，明确接口与责任边界")
    if risk.get("level") == "高":
        actions.append("提问：向设计明确责任方、依据图纸与完成条件，并升级跟踪")
    else:
        actions.append("提问：向设计追问待明确事项与图纸依据")
    return actions


def audit_text(
    title: str,
    body: str,
    *,
    discipline: str | None = None,
    doc_type: str | None = None,
) -> dict:
    """纯文本会审审查，返回契约第3节 ``data`` 结构 dict。"""
    title = title or ""
    body = body or ""
    text = f"{title}\n{body}".strip()

    judgement = route_discipline(discipline, title, body)
    code = judgement["code"]

    location = location_extractor.extract(text)
    concerns = concern_extractor.extract(code, text)
    object_hits = _hit_objects(code, text)
    classification = classifier.classify(code, concerns, text, location)

    v1_questions = question_generator.generate(
        code,
        concerns,
        [name for name, _ in object_hits],
        location,
        text,
    )

    # ── V2 流水线：对象识别 → 场景路由 → 问题包 → 文书化输出 ──
    obj = object_identifier.identify(code, concerns, text)
    scenario = scenario_router.route(text, classification["risk"], classification["issue_class"])
    question_pack = question_pack_builder.build(
        code, obj, scenario, location, concerns
    )
    document = _write_documents(
        code, obj, scenario, concerns, question_pack, classification["interface"]
    )

    # 标准问题 = 问题包.主问题 +（如有）补充问题；问题包缺失时回退 V1 标准问题
    standard_questions = _standard_questions(question_pack, v1_questions)

    has_location = any(location.get(k) for k in location)
    return {
        "专业判断": judgement,
        "定位信息": location,
        "核心concern": concerns,
        "问题归类": classification["issue_class"],
        "接口复核": classification["interface"],
        "风险等级": classification["risk"],
        "建议动作": _suggested_actions(
            concerns, classification["interface"], classification["risk"], has_location
        ),
        "证据缺口": _evidence_gap(location, concerns),
        "标准问题": standard_questions,
        # ── V2 section ──
        "对象识别": obj,
        "场景识别": scenario,
        "问题包": question_pack,
        "文书输出": document,
    }


def _first_concern_label(concerns: list[dict]) -> str:
    for concern in concerns or []:
        label = str((concern or {}).get("label", "")).strip()
        if label:
            return label
    return ""


def _write_documents(
    code: str,
    obj: dict,
    scenario: dict,
    concerns: list[dict],
    question_pack: dict,
    interface: dict,
) -> dict:
    """组装文书化输出，给 document_writer 补 concern/scenario 上下文。"""
    enriched_obj = {
        **obj,
        "concern": _first_concern_label(concerns),
        "scenario": str((scenario or {}).get("name", "")),
    }
    return document_writer.write(code, enriched_obj, question_pack, interface)


def _standard_questions(question_pack: dict, v1_questions: list[str]) -> list[str]:
    """标准问题 = 主问题 +（如有）补充问题；问题包为空时回退 V1。"""
    main = str((question_pack or {}).get("主问题", "")).strip()
    supplement = str((question_pack or {}).get("补充问题", "")).strip()
    questions = [q for q in (main, supplement) if q]
    return questions or list(v1_questions)


def _map_severity(risk_level: str, text: str) -> IssueSeverity:
    if any(signal in text for signal in _CRITICAL_SIGNALS) and risk_level == "高":
        return IssueSeverity.CRITICAL
    return _RISK_SEVERITY.get(risk_level, IssueSeverity.INFO)


class ReviewAuditEngine(BaseEngine):
    """会审审查引擎（接入协调器并行层）。"""

    engine_name = "review"

    async def analyze(self, ctx: DrawingContext, db) -> list[AIIssue]:
        try:
            text = ctx.extracted_text or ctx.title or ""
            if not text.strip():
                return []
            result = audit_text(ctx.title or "", text, discipline=ctx.discipline)
            return [self._to_issue(result, text)]
        except Exception as exc:  # noqa: BLE001 - 引擎异常返回空列表，不影响其他引擎
            logger.error("[ReviewAuditEngine] 审查失败: %s", exc)
            return []

    def _to_issue(self, result: dict, text: str) -> AIIssue:
        judgement = result["专业判断"]
        risk = result["风险等级"]
        interface = result["接口复核"]
        questions = result["标准问题"]
        issue_class = result["问题归类"]
        concerns = result["核心concern"]

        # ── V2 section（向后兼容：缺失时退回 V1 行为）──
        obj = result.get("对象识别", {}) or {}
        scenario = result.get("场景识别", {}) or {}
        question_pack = result.get("问题包", {}) or {}
        document = result.get("文书输出", {}) or {}
        object_level = str(obj.get("level", "")) or _object_level(
            _hit_objects(judgement["code"], text)
        )

        description = questions[0] if questions else (
            f"[{judgement['code']}/{judgement['name']}] {('；'.join(issue_class))}"
        )

        return AIIssue(
            engine=self.engine_name,
            severity=_map_severity(risk["level"], text),
            description=description,
            category="会审审查",
            suggestion="；".join(result["建议动作"]),
            discipline_code=judgement["code"],
            discipline_name=judgement["name"],
            location=result["定位信息"],
            concerns=concerns,
            issue_class=issue_class,
            interface_primary=interface["primary"],
            interface_related=interface["related"],
            risk_level=risk["level"],
            object_level=object_level,
            standard_question=questions[0] if questions else "",
            evidence_gap=result["证据缺口"],
            # ── V2 扩展字段 ──
            object_name=str(obj.get("object", "")),
            object_basis=str(obj.get("basis", "")),
            scenario=str(scenario.get("name", "")),
            scenario_reason=str(scenario.get("priority_reason", "")),
            question_pack=question_pack,
            doc_minutes=document.get("会审纪要口径", []) or [],
            doc_reply=document.get("设计答复口径", []) or [],
        )
