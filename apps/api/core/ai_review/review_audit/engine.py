"""会审审查引擎主入口。

- ``audit_text(...)``：纯函数文本审查，返回契约第3节 ``data`` 结构（中文 key）。
  无 db / 无 LLM 也能运行；yaml 缺失时优雅降级为空结构。
- ``ReviewAuditEngine(BaseEngine)``：engine_name="review"，串接四引擎协调器。
"""
from __future__ import annotations

import logging

from ..base import AIIssue, BaseEngine, DrawingContext, IssueSeverity
from . import (
    action_recommender,
    checklist_runner,
    classifier,
    concern_extractor,
    control_chain,
    dimension_checker,
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

    # ── V3 流水线：SOP 逐项清单核查（审图目标/未来影响/清单覆盖）──
    sop = checklist_runner.run(
        code,
        text,
        concerns,
        location,
        scenario,
        classification["issue_class"],
        classification["risk"],
    )

    # ── V4 流水线：五维审查 → 结构化处理建议 → 六步控制链（含闭环判定）──
    dimensions = dimension_checker.check(
        text, location, concerns, classification["issue_class"]
    )
    recommendation = action_recommender.recommend(text, classification, scenario, obj)
    structured_actions = recommendation["处理建议"]
    chain = control_chain.build(
        text, classification, scenario, [a["动作"] for a in structured_actions]
    )
    priority_objects = dimension_checker.hit_priority_objects(text)

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
        # ── V3 section（SOP 逐项清单）──
        "审图目标": {
            "protected_result": sop.get("protected_result", ""),
            "why_now": sop.get("why_now", ""),
        },
        "未来影响": sop.get("future_impact", {"stage": "", "effect": ""}),
        "逐项清单": sop.get(
            "coverage",
            {"ratio": 0.0, "checked": 0, "covered": 0, "items": [], "uncovered": []},
        ),
        # ── V4 section（方法论：控制链/五维审查/结构化处理建议）──
        "控制链": chain,
        "五维审查": dimensions,
        "处理建议": structured_actions,
        "闭环要求": recommendation["闭环要求"],
        "优先对象": priority_objects,
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


def _build_review_sop(result: dict) -> dict:
    """从 audit_text 结果组装挂到主 finding 的 SOP 增强结构。"""
    goal = result.get("审图目标", {}) or {}
    return {
        "protected_result": goal.get("protected_result", ""),
        "why_now": goal.get("why_now", ""),
        "future_impact": result.get("未来影响", {}) or {},
        "checklist": result.get("逐项清单", {}) or {},
    }


def _build_review_method(result: dict) -> dict:
    """从 audit_text 结果组装挂到主 finding 的方法论增强结构（V4）。"""
    return {
        "控制链": result.get("控制链", {}) or {},
        "五维审查": result.get("五维审查", []) or [],
        "处理建议": result.get("处理建议", []) or [],
        "闭环要求": result.get("闭环要求", {}) or {},
        "优先对象": result.get("优先对象", []) or [],
    }


def _extra_findings(result: dict) -> list[AIIssue]:
    """对「命中且未覆盖且可升级」的 SOP 清单项追加紧凑高价值 finding（≤3 条）。"""
    coverage = result.get("逐项清单", {}) or {}
    uncovered = [u for u in coverage.get("uncovered", []) if u.get("升级")]
    if not uncovered:
        return []

    judgement = result["专业判断"]
    risk = result["风险等级"]
    interface = result["接口复核"]
    severity = IssueSeverity.MAJOR if risk.get("level") == "高" else IssueSeverity.MINOR

    findings: list[AIIssue] = []
    for item in uncovered[:_MAX_EXTRA_FINDINGS]:
        question = str(item.get("必问问题", "")).strip()
        if not question:
            continue
        findings.append(
            AIIssue(
                engine="review",
                severity=severity,
                description=question,
                category="会审审查·SOP清单",
                suggestion=str(item.get("输出口径", "")),
                discipline_code=judgement["code"],
                discipline_name=judgement["name"],
                location=result["定位信息"],
                risk_level=str(risk.get("level", "")),
                interface_primary=interface.get("primary", ""),
                interface_related=interface.get("related", []),
                standard_question=question,
            )
        )
    return findings


# 追加 SOP 高价值 finding 上限（遵循 SKILL.md「compact question pack」，不堆砌低密度问题）
_MAX_EXTRA_FINDINGS = 3

_POLISH_SYSTEM = (
    "你是工程图纸会审问题润色助手。请把给定的会审问题改写为一句可直接进会审问题单的"
    "闭环问法：保留对象、定位与待明确事项，明确责任与图纸依据，不杜撰原文没有的实体。"
    "只输出改写后的一句话，不要解释。"
)


class ReviewAuditEngine(BaseEngine):
    """会审审查引擎（接入协调器并行层）。

    ``redis`` 可选：提供后启用 ``review_question_writer`` LLM 润色（模板优先、失败回退）。
    """

    engine_name = "review"

    def __init__(self, redis=None):
        self._redis = redis
        self._router = None

    async def analyze(self, ctx: DrawingContext, db) -> list[AIIssue]:
        try:
            text = ctx.extracted_text or ctx.title or ""
            if not text.strip():
                return []
            result = audit_text(ctx.title or "", text, discipline=ctx.discipline)
            main = self._to_issue(result, text)
            main.review_sop = _build_review_sop(result)
            main.review_method = _build_review_method(result)

            # LLM 可选润色（仅主问题，1 次调用；任何失败回退模板原句）
            polished = await self._maybe_polish(main.standard_question, text, db)
            if polished and polished != main.standard_question:
                main.standard_question = polished
                main.description = polished

            return [main, *_extra_findings(result)]
        except Exception as exc:  # noqa: BLE001 - 引擎异常返回空列表，不影响其他引擎
            logger.error("[ReviewAuditEngine] 审查失败: %s", exc)
            return []

    def _get_router(self, db):
        """惰性构造 ModelRouter；缺 db/redis 时返回 None（润色降级跳过）。"""
        if db is None or self._redis is None:
            return None
        if self._router is None:
            try:
                from core.llm.router import ModelRouter
                self._router = ModelRouter(db, self._redis)
            except Exception as exc:  # noqa: BLE001 - 导入/构造失败则不润色
                logger.debug("[ReviewAuditEngine] ModelRouter 不可用，跳过润色: %s", exc)
                return None
        return self._router

    async def _maybe_polish(self, question: str, context: str, db) -> str:
        if not question:
            return question
        router = self._get_router(db)
        if router is None:
            return question
        try:
            messages = [
                {"role": "system", "content": _POLISH_SYSTEM},
                {"role": "user", "content": f"原问题：{question}\n记录背景：{context[:500]}"},
            ]
            resp = await router.route("review_question_writer", messages)
            out = (getattr(resp, "content", "") or "").strip()
            return out or question
        except Exception as exc:  # noqa: BLE001 - 润色失败回退模板原句
            logger.debug("[ReviewAuditEngine] 问题润色跳过: %s", exc)
            return question

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
