"""学习闭环编排:扫事件 → 跑分析 → 落建议 + 过程日志 → 人审采纳 → 生效。

**过程日志**(`optimization_runs.steps_json`)记录每一步做了什么、看了多少数据、
得出什么——不是为了好看,是为了让人能判断「这条建议凭什么」。

**采纳的两条路**:
- `auto_applicable=true` → 写 `learned_rules`,**当场生效**(词表/纠错/阈值);
- `auto_applicable=false` → 只置为已导出,**明确告诉人这需要开发介入**,
  不假装已解决。
"""
from __future__ import annotations

import json
import logging
from typing import Any

from services.learning_analyzers import run_analyzers

logger = logging.getLogger(__name__)

#: 单轮分析扫描的事件上限(够看出模式,又不至于一次拉爆内存)
DEFAULT_EVENT_LIMIT = 5000

_EVENTS_SQL = """
SELECT kind, field, auto_value, human_value, context_json, created_at
FROM annotation_events
WHERE project_id = CAST(:project_id AS uuid)
ORDER BY created_at DESC
LIMIT :limit
"""

_RUN_INSERT_SQL = """
INSERT INTO optimization_runs (project_id, trigger, events_scanned, steps_json)
VALUES (CAST(:project_id AS uuid), :trigger, 0, CAST(:steps AS jsonb))
RETURNING id
"""

_RUN_FINISH_SQL = """
UPDATE optimization_runs
SET events_scanned = :scanned, findings = :findings,
    steps_json = CAST(:steps AS jsonb), finished_at = now(), error = :error
WHERE id = CAST(:id AS uuid)
"""

_SUGGESTION_INSERT_SQL = """
INSERT INTO improvement_suggestions
    (run_id, project_id, category, title, detail, evidence_json,
     impact, confidence, auto_applicable)
VALUES (CAST(:run_id AS uuid), CAST(:project_id AS uuid), :category, :title,
        :detail, CAST(:evidence AS jsonb), :impact, :confidence, :auto)
RETURNING id
"""

#: 同一条建议已在 pending 里就不重复插(否则每跑一轮列表翻一倍)
_EXISTING_SQL = """
SELECT title FROM improvement_suggestions
WHERE project_id = CAST(:project_id AS uuid) AND status IN ('pending', 'rejected')
"""


def _parse_context(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except (ValueError, TypeError):
            return {}
    return {}


async def run_optimization(
    db: Any, project_id: str, *, trigger: str = "manual",
    limit: int = DEFAULT_EVENT_LIMIT,
) -> dict:
    """跑一轮学习分析,落建议与过程日志。返回 {run_id, findings, steps}。"""
    steps: list[dict] = [{"step": "start", "trigger": trigger}]
    row = await db.fetch_one(_RUN_INSERT_SQL, {
        "project_id": project_id, "trigger": trigger,
        "steps": json.dumps(steps, ensure_ascii=False)})
    run_id = str(row["id"]) if row is not None else None

    scanned = 0
    findings: list[dict] = []
    error: str | None = None
    try:
        rows = await db.fetch_all(
            _EVENTS_SQL, {"project_id": project_id, "limit": limit})
        events = [{
            "kind": r["kind"], "field": r["field"],
            "auto_value": r["auto_value"], "human_value": r["human_value"],
            "context_json": _parse_context(r["context_json"]),
        } for r in rows]
        scanned = len(events)
        by_kind: dict[str, int] = {}
        for e in events:
            by_kind[e["kind"]] = by_kind.get(e["kind"], 0) + 1
        steps.append({"step": "scan_events", "scanned": scanned, "by_kind": by_kind})

        findings = run_analyzers(events)
        steps.append({
            "step": "analyze", "findings": len(findings),
            "by_category": _count_by(findings, "category"),
            "auto_applicable": sum(1 for f in findings if f["auto_applicable"]),
        })

        existing = {r["title"] for r in await db.fetch_all(
            _EXISTING_SQL, {"project_id": project_id})}
        saved = 0
        for f in findings:
            if f["title"] in existing:
                continue                # 已提过(含被否决的),不重复刷屏
            await db.execute(_SUGGESTION_INSERT_SQL, {
                "run_id": run_id, "project_id": project_id,
                "category": f["category"], "title": f["title"],
                "detail": f["detail"],
                "evidence": json.dumps(f["evidence"], ensure_ascii=False),
                "impact": f["impact"], "confidence": f["confidence"],
                "auto": f["auto_applicable"]})
            saved += 1
        steps.append({"step": "persist", "new_suggestions": saved,
                      "skipped_duplicates": len(findings) - saved})
    except Exception as exc:  # noqa: BLE001 — 学习失败不该影响主流程
        error = str(exc)[:300]
        steps.append({"step": "error", "message": error})
        logger.warning("[optimization] 学习分析失败 %s: %s", project_id, exc)

    steps.append({"step": "done"})
    if run_id:
        await db.execute(_RUN_FINISH_SQL, {
            "id": run_id, "scanned": scanned, "findings": len(findings),
            "steps": json.dumps(steps, ensure_ascii=False), "error": error})
    return {"run_id": run_id, "scanned": scanned,
            "findings": len(findings), "steps": steps, "error": error}


def _count_by(items: list[dict], key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for item in items:
        out[item[key]] = out.get(item[key], 0) + 1
    return out


#: 建议类别 → 生效为哪种规则。algorithm 类不在此表内(需开发介入)。
CATEGORY_TO_RULE = {
    "vocabulary": "vocabulary",
    "ocr_correction": "ocr_correction",
    "threshold": "threshold",
}


def rule_from_suggestion(category: str, evidence: dict) -> tuple[str, str] | None:
    """建议证据 → (rule_key, rule_value);无法落成规则返回 None。"""
    if category == "vocabulary":
        word = str(evidence.get("word") or "").strip()
        return (word, word) if word else None
    if category == "ocr_correction":
        raw = str(evidence.get("raw") or "").strip()
        fixed = str(evidence.get("corrected") or "").strip()
        return (raw, fixed) if raw and fixed else None
    if category == "threshold":
        # 阈值建议目前只覆盖候选轴线最小跨度
        spans = evidence.get("observed_spans") or []
        if not spans:
            return None
        return ("axis_min_span", str(round(float(min(spans)) * 0.9, 3)))
    return None


_SUGGESTION_SQL = """
SELECT id, project_id, category, title, evidence_json, auto_applicable, status
FROM improvement_suggestions WHERE id = CAST(:id AS uuid)
"""

_STATUS_SQL = """
UPDATE improvement_suggestions
SET status = :status, applied_at = now(), reviewed_by = CAST(:user AS uuid)
WHERE id = CAST(:id AS uuid)
"""


async def review_suggestion(
    db: Any, suggestion_id: str, *, accept: bool, user_id: str | None,
) -> dict:
    """人审建议:采纳 → 可自动生效的当场写规则;否决 → 记状态不再重复提。"""
    from services.learned_rules import save_rule

    row = await db.fetch_one(_SUGGESTION_SQL, {"id": suggestion_id})
    if row is None:
        return {"ok": False, "error": "SUGGESTION_NOT_FOUND"}
    if not accept:
        await db.execute(_STATUS_SQL, {
            "id": suggestion_id, "status": "rejected", "user": user_id})
        return {"ok": True, "status": "rejected", "applied": False}

    if not row["auto_applicable"]:
        # 算法改造类:采纳只代表「确认要做」,标为待导出,**不假装已生效**
        await db.execute(_STATUS_SQL, {
            "id": suggestion_id, "status": "exported", "user": user_id})
        return {"ok": True, "status": "exported", "applied": False,
                "note": "该建议需开发介入,已标记为待导出,系统行为暂未改变"}

    rule_type = CATEGORY_TO_RULE.get(row["category"])
    pair = rule_from_suggestion(row["category"], _parse_context(row["evidence_json"]))
    if not rule_type or pair is None:
        return {"ok": False, "error": "CANNOT_DERIVE_RULE"}

    await save_rule(db, project_id=str(row["project_id"]), rule_type=rule_type,
                    rule_key=pair[0], rule_value=pair[1],
                    suggestion_id=suggestion_id)
    await db.execute(_STATUS_SQL, {
        "id": suggestion_id, "status": "accepted", "user": user_id})
    return {"ok": True, "status": "accepted", "applied": True,
            "rule": {"type": rule_type, "key": pair[0], "value": pair[1]}}


def build_export(run: dict, suggestions: list[dict]) -> dict:
    """导出给开发的结构化包:只含需开发介入的建议 + 完整证据与过程日志。"""
    dev_items = [s for s in suggestions if not s.get("auto_applicable")]
    return {
        "generated_from_run": run.get("run_id"),
        "events_scanned": run.get("scanned"),
        "process_log": run.get("steps"),
        "needs_development": [{
            "title": s["title"], "category": s["category"],
            "detail": s["detail"], "evidence": s.get("evidence"),
            "impact_drawings": s.get("impact"), "confidence": s.get("confidence"),
        } for s in dev_items],
        "auto_applied_count": sum(1 for s in suggestions if s.get("auto_applicable")),
    }
