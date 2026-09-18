"""现实合理性分析的编排层：跑一遍规则、落库、读回。

**这一层只做编排**，判据全在 `core/model3d/plausibility/`（无 DB 依赖、纯函数）。
分开是为了让规则能在三处复用同一份判据：整模型（本文件）、单张图纸
（AI 审图引擎）、以及测试。

**落库要截断，但截断必须可见**：一个模型可能出上万条结论（几百根柱同时
自交就是几百条），全量 jsonb 塞进一行既慢又没人看。这里每条规则只存
前 `PER_RULE_CAP` 条样例，**计数保留完整**，并在 `truncated` 里写明
「哪条规则被截了、总共多少条」—— 截断本身不能让人误以为只有这么多。
"""
from __future__ import annotations

import json
import logging

from core.model3d.plausibility.model import from_scene
from core.model3d.plausibility.registry import run
from core.model3d.plausibility.types import Report

logger = logging.getLogger(__name__)

#: 每条规则落库的样例上限。50 条足够看清模式（是零星还是成片），
#: 又不至于让一行 jsonb 膨胀到兆级。
PER_RULE_CAP = 50


def analyze_scene(scene: dict) -> Report:
    """场景 JSON → 合理性报告。"""
    return run(from_scene(scene or {}))


def summarize(report: Report) -> dict:
    """报告 → 可落库的 dict（按规则聚合 + 截断样例 + 截断说明）。"""
    by_rule: dict[str, dict] = {}
    for finding in report.findings:
        entry = by_rule.setdefault(finding.rule, {
            "rule": finding.rule, "severity": finding.severity, "basis": finding.basis,
            "count": 0, "kinds": {}, "samples": [],
        })
        entry["count"] += 1
        entry["kinds"][finding.kind] = entry["kinds"].get(finding.kind, 0) + 1
        if len(entry["samples"]) < PER_RULE_CAP:
            entry["samples"].append({"target": finding.target, "kind": finding.kind,
                                     "detail": finding.detail, "evidence": finding.evidence})
    truncated = {r: e["count"] for r, e in by_rule.items() if e["count"] > PER_RULE_CAP}
    return {
        "counts": report.counts,
        "rules": [by_rule[key] for key in sorted(by_rule)],
        "checked": dict(report.checked),
        # 跑不了的与炸了的都要露出来：少跑了一条却报「通过」是最坏的结果
        "skipped": dict(report.skipped),
        "errored": dict(report.errored),
        "truncated": truncated,
        "per_rule_cap": PER_RULE_CAP,
    }


def _parse_scene(value) -> dict:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return {}
    return value or {}


async def run_for_project(db, project_id: str) -> dict | None:
    """取该工程当前模型的场景，跑一遍并落库。没有模型时返回 None。"""
    row = await db.fetch_one(
        "SELECT scene, version FROM project_models WHERE project_id=$1", project_id)
    if row is None:
        return None
    data = dict(row)
    scene = _parse_scene(data.get("scene"))
    if not scene:
        return None
    summary = summarize(analyze_scene(scene))
    version = int(data.get("version") or 0)
    await db.execute(
        "INSERT INTO model_plausibility_reports (project_id, model_version, report, counts)"
        " VALUES ($1, $2, $3, $4)",
        project_id, version, json.dumps(summary, ensure_ascii=False),
        json.dumps(summary["counts"], ensure_ascii=False))
    return {**summary, "model_version": version}


async def latest_report(db, project_id: str) -> dict | None:
    """最近一次分析结果；没跑过返回 None（**不即时重算** —— 免得一个只读
    接口悄悄做重活，也免得两次打开页面看到两份不同的数）。"""
    row = await db.fetch_one(
        "SELECT report, model_version, created_at FROM model_plausibility_reports"
        " WHERE project_id=$1 ORDER BY created_at DESC LIMIT 1", project_id)
    if row is None:
        return None
    data = dict(row)
    report = data.get("report")
    if isinstance(report, str):
        try:
            report = json.loads(report)
        except ValueError:
            report = {}
    return {**(report or {}), "model_version": data.get("model_version"),
            "analyzed_at": data.get("created_at")}
