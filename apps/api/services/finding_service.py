"""Finding 统一聚合服务（Phase D · 泳道2 · D-05；创效潜力判别 D-07）。

把五类割裂的问题/发现统一读取为一个 Finding 抽象：
  - engine   单图 AI 审图问题（ai_review_issues）
  - review   会审发现（review_audit_findings）
  - cross    跨图/套图问题（review_batches.cross_findings）
  - semantic 语义审校项（project_models.scene 派生，复用 routers.model_review.build_review_queue）
  - symbol   符号待审项（model_symbol_annotations）

本模块只做**只读聚合** + 独立的人工闭环状态机（迁移 026 `finding_status` 覆盖表），
不改动五个来源表的写入路径。状态机固定四态、单向推进：
    pending → acknowledged → remediated → closed

Finding 形态（统一后返回给前端）：
    {id, source, project_id, drawing_id, severity, title, description,
     status, location, note, status_updated_at, created_at, has_saving_potential}

id 编码为 "{source}:{source_key}"，全局可用于 GET/POST 单条端点。

D-07 新增「创效潜力」判别（**规则优先**，见 `_rule_based_saving_potential`）：
每条 Finding 均带 `has_saving_potential`（同步、零成本，随 `_finalize` 计算，不调用任何
模型）。`assess_saving_potential` 提供 LLM 可选增强（走 `ModelRouter`，仅在规则未命中且
调用方显式注入 `router` 时触发，未配置/失败一律优雅降级为规则结果，绝不阻塞主流程），
供 `routers/findings.py` 的 to-proposal 端点在 `use_llm=True` 时调用。
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

VALID_SOURCES: frozenset[str] = frozenset(
    {"engine", "review", "cross", "semantic", "symbol"}
)
VALID_SEVERITIES: frozenset[str] = frozenset(
    {"critical", "high", "medium", "low"}
)
# 状态机：固定四态，单向推进（不可回退，允许原地重复提交）
STATUS_ORDER: list[str] = ["pending", "acknowledged", "remediated", "closed"]

_SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}

# ai_review_issues.severity（critical/major/minor/info）→ 统一四档
_ENGINE_SEVERITY_MAP = {
    "critical": "critical", "major": "high", "minor": "medium", "info": "low",
}
# review_audit_findings.risk_level（高/中/低，见 core/ai_review/review_audit/engine.py）
_RISK_LEVEL_SEVERITY_MAP = {"高": "high", "中": "medium", "低": "low"}

# ai_review_issues.status（open/acknowledged/closed/waived）→ 我方四态初始映射
_ENGINE_STATUS_DEFAULT = {
    "open": "pending", "acknowledged": "acknowledged",
    "closed": "closed", "waived": "closed",
}
# model_symbol_annotations.status（pending/confirmed/rejected/reclassed）→ 我方四态初始映射
_SYMBOL_STATUS_DEFAULT = {
    "pending": "pending", "confirmed": "remediated",
    "rejected": "closed", "reclassed": "remediated",
}


class InvalidTransitionError(Exception):
    """状态机试图回退（如 closed → pending）时抛出。"""


# ── 通用工具 ─────────────────────────────────────────────────

def _parse_json(value: Any, default: Any) -> Any:
    """JSONB 字段经驱动可能回传 str，安全解析。"""
    if value is None:
        return default
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return default
    return value


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _severity_from_confidence(confidence: float | None, conflict: bool) -> str:
    """无原生严重度的来源（语义/符号）按置信度 + 冲突标志归一。"""
    if conflict:
        return "high"
    if confidence is None:
        return "medium"
    if confidence < 0.5:
        return "high"
    if confidence < 0.8:
        return "medium"
    return "low"


def _created_epoch(value: Any) -> float:
    """created_at 排序辅助：datetime → epoch 秒；缺失视为最旧（排最后）。"""
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    return 0.0


def _sort_key(item: dict) -> tuple:
    return (_SEVERITY_RANK.get(item["severity"], 9), -_created_epoch(item.get("created_at")))


def _count_by(items: list[dict], field: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        key = str(item.get(field))
        counts[key] = counts.get(key, 0) + 1
    return counts


# ── 来源①：engine（单图 AI 审图问题）──────────────────────────

_ENGINE_SQL = """
SELECT i.id, r.drawing_id, d.project_id, i.severity, i.category, i.description,
       i.suggestion, i.status, i.location_json, i.created_at
FROM ai_review_issues i
JOIN ai_review_reports r ON r.id = i.report_id
JOIN drawings d ON d.id = r.drawing_id
WHERE d.project_id = $1
ORDER BY i.created_at DESC
"""


async def _fetch_engine_findings(db, project_id: str) -> list[dict]:
    rows = await db.fetch_all(_ENGINE_SQL, project_id)
    out: list[dict] = []
    for raw in rows:
        row = dict(raw)
        out.append({
            "source": "engine",
            "source_key": str(row["id"]),
            "project_id": str(row["project_id"]),
            "drawing_id": str(row["drawing_id"]) if row.get("drawing_id") else None,
            "severity": _ENGINE_SEVERITY_MAP.get(row.get("severity"), "medium"),
            "title": row.get("category") or "AI 审图问题",
            "description": row.get("description") or row.get("suggestion") or "",
            "location": _parse_json(row.get("location_json"), None),
            "created_at": row.get("created_at"),
            "native_status": row.get("status"),
        })
    return out


# ── 来源②：review（会审发现）───────────────────────────────────

_REVIEW_SQL = """
SELECT f.id, rec.project_id, f.discipline_name, f.risk_level, f.object_level,
       f.standard_question, f.location_json, f.created_at
FROM review_audit_findings f
JOIN review_audit_records rec ON rec.id = f.record_id
WHERE rec.project_id = $1
ORDER BY f.created_at DESC
"""


async def _fetch_review_findings(db, project_id: str) -> list[dict]:
    rows = await db.fetch_all(_REVIEW_SQL, project_id)
    out: list[dict] = []
    for raw in rows:
        row = dict(raw)
        discipline = row.get("discipline_name") or "会审"
        question = row.get("standard_question") or ""
        out.append({
            "source": "review",
            "source_key": str(row["id"]),
            "project_id": str(row["project_id"]) if row.get("project_id") else str(project_id),
            "drawing_id": None,
            "severity": _RISK_LEVEL_SEVERITY_MAP.get(row.get("risk_level"), "medium"),
            "title": f"{discipline}会审发现" + (f"（{row['object_level']}）" if row.get("object_level") else ""),
            "description": question,
            "location": _parse_json(row.get("location_json"), None),
            "created_at": row.get("created_at"),
            "native_status": None,
        })
    return out


# ── 来源③：cross（跨图/套图问题）───────────────────────────────

_CROSS_BATCH_SQL = """
SELECT id, cross_findings, created_at
FROM review_batches
WHERE project_id = $1 AND cross_findings IS NOT NULL
ORDER BY created_at DESC
"""


def _cross_items_from_batch(batch_id: str, cross: dict, created_at: Any) -> list[dict]:
    """把一个批次的 cross_findings（core/ai_review/cross_drawing.analyze_batch 输出）
    拆成独立 Finding 条目；source_key 前缀批次 id 避免跨批次碰撞。"""
    out: list[dict] = []

    for entry in cross.get("重复图号") or []:
        drawing_no = str(entry.get("drawing_no") or "")
        out.append({
            "source": "cross", "source_key": f"{batch_id}:dup:{drawing_no}",
            "drawing_id": None, "severity": "medium",
            "title": f"图号重复：{drawing_no}",
            "description": f"套图内 {len(entry.get('drawing_ids') or [])} 张图纸共用图号 {drawing_no}",
            "location": None, "created_at": created_at, "native_status": None,
        })

    for entry in cross.get("版本冲突") or []:
        drawing_no = str(entry.get("drawing_no") or "")
        versions = entry.get("versions") or []
        out.append({
            "source": "cross", "source_key": f"{batch_id}:conflict:{drawing_no}",
            "drawing_id": None, "severity": "high",
            "title": f"版本冲突：{drawing_no}",
            "description": f"图号 {drawing_no} 同时存在版本 {versions}",
            "location": None, "created_at": created_at, "native_status": None,
        })

    for entry in cross.get("接口缺图") or []:
        discipline = str(entry.get("missing_discipline") or "")
        out.append({
            "source": "cross", "source_key": f"{batch_id}:missing:{discipline}",
            "drawing_id": None, "severity": "high",
            "title": f"接口缺图：{discipline}",
            "description": f"套图内 {len(entry.get('referenced_by') or [])} 处引用了 {discipline} 专业但无对应图纸",
            "location": None, "created_at": created_at, "native_status": None,
        })

    for entry in cross.get("问题聚类") or []:
        key = str(entry.get("location_key") or "")
        count = int(entry.get("count") or 0)
        out.append({
            "source": "cross", "source_key": f"{batch_id}:cluster:{key}",
            "drawing_id": None, "severity": "high" if count >= 3 else "medium",
            "title": f"跨图问题聚类：{key or '未知定位'}",
            "description": f"{count} 处问题共现于 {entry.get('drawings') or []}",
            "location": {"location_key": key}, "created_at": created_at, "native_status": None,
        })

    return out


async def _fetch_cross_findings(db, project_id: str) -> list[dict]:
    rows = await db.fetch_all(_CROSS_BATCH_SQL, project_id)
    out: list[dict] = []
    for raw in rows:
        row = dict(raw)
        cross = _parse_json(row.get("cross_findings"), {})
        if not isinstance(cross, dict):
            continue
        items = _cross_items_from_batch(str(row["id"]), cross, row.get("created_at"))
        for item in items:
            item["project_id"] = str(project_id)
        out.extend(items)
    return out


# ── 来源④：semantic（语义审校项，scene 动态派生）────────────────

_SCENE_SQL = "SELECT scene FROM project_models WHERE project_id=$1"


async def _fetch_semantic_findings(db, project_id: str) -> list[dict]:
    # 延迟导入避免 services ↔ routers 之间产生模块级循环依赖；
    # build_review_queue / _parse_scene 是纯函数，复用无副作用。
    from routers.model_review import _parse_scene, build_review_queue

    row = await db.fetch_one(_SCENE_SQL, project_id)
    if row is None:
        return []
    scene = _parse_scene(dict(row).get("scene"))
    items, _summary = build_review_queue(scene)

    out: list[dict] = []
    for it in items:
        conflict = bool(it.get("conflict"))
        severity = "high" if conflict else _severity_from_confidence(it.get("confidence"), False)
        out.append({
            "source": "semantic",
            "source_key": str(it["id"]),
            "project_id": str(project_id),
            "drawing_id": it.get("drawing_id"),
            "severity": severity,
            "title": str(it.get("title") or it["id"]),
            "description": str(it.get("detail") or ""),
            "location": None,
            "created_at": None,
            "native_status": None,
        })
    return out


# ── 来源⑤：symbol（符号待审项）─────────────────────────────────

_SYMBOL_SQL = """
SELECT id, drawing_id, category, mep_system, confidence, status, created_at
FROM model_symbol_annotations
WHERE project_id = $1
ORDER BY confidence ASC NULLS LAST, id ASC
"""


async def _fetch_symbol_findings(db, project_id: str) -> list[dict]:
    rows = await db.fetch_all(_SYMBOL_SQL, project_id)
    out: list[dict] = []
    for raw in rows:
        row = dict(raw)
        confidence = _as_float(row.get("confidence"))
        out.append({
            "source": "symbol",
            "source_key": str(row["id"]),
            "project_id": str(project_id),
            "drawing_id": str(row["drawing_id"]) if row.get("drawing_id") else None,
            "severity": _severity_from_confidence(confidence, False),
            "title": f"符号待审：{row.get('category') or '未知类别'}",
            "description": (
                f"{row.get('category') or ''} 符号候选"
                + (f"（{row['mep_system']}）" if row.get("mep_system") else "")
                + f"，置信度 {confidence if confidence is not None else 'N/A'}"
            ),
            "location": None,
            "created_at": row.get("created_at"),
            "native_status": row.get("status"),
        })
    return out


_FETCHERS: dict[str, Callable[[Any, str], Awaitable[list[dict]]]] = {
    "engine": _fetch_engine_findings,
    "review": _fetch_review_findings,
    "cross": _fetch_cross_findings,
    "semantic": _fetch_semantic_findings,
    "symbol": _fetch_symbol_findings,
}


def _default_status(source: str, native_status: str | None) -> str:
    if source == "engine":
        return _ENGINE_STATUS_DEFAULT.get(native_status or "", "pending")
    if source == "symbol":
        return _SYMBOL_STATUS_DEFAULT.get(native_status or "", "pending")
    return "pending"


# ── 状态覆盖 overlay ─────────────────────────────────────────

_OVERLAY_SQL = """
SELECT source, source_key, status, note, updated_at
FROM finding_status
WHERE project_id = $1
"""


async def _fetch_status_overlay(db, project_id: str) -> dict[tuple[str, str], dict]:
    rows = await db.fetch_all(_OVERLAY_SQL, project_id)
    overlay: dict[tuple[str, str], dict] = {}
    for raw in rows:
        row = dict(raw)
        overlay[(row["source"], row["source_key"])] = row
    return overlay


def _finalize(item: dict, overlay: dict[tuple[str, str], dict]) -> dict:
    """套用状态覆盖，剥离内部字段，组出对外 Finding 形态。"""
    key = (item["source"], item["source_key"])
    override = overlay.get(key)
    if override is not None:
        status = override["status"]
        note = override.get("note")
        status_updated_at = override.get("updated_at")
    else:
        status = _default_status(item["source"], item.get("native_status"))
        note = None
        status_updated_at = None

    return {
        "id": f"{item['source']}:{item['source_key']}",
        "source": item["source"],
        "project_id": item["project_id"],
        "drawing_id": item.get("drawing_id"),
        "severity": item["severity"],
        "title": item["title"],
        "description": item["description"],
        "status": status,
        "location": item.get("location"),
        "note": note,
        "status_updated_at": status_updated_at,
        "created_at": item.get("created_at"),
        "has_saving_potential": _rule_based_saving_potential(item),
    }


# ── D-07：创效潜力判别（规则优先 + LLM 可选增强）─────────────────

# 命中任一关键词 + severity ∈ {critical, high} → 判定有创效潜力（规则，确定性、零成本）。
# 覆盖两类可创效线索：①材料/工程量类（用量、超配、冗余、浪费 → 直接降本）
# ②跨图协调类（版本冲突/接口缺图/图号重复 → 返工/协调成本，整改即避免损失）。
_SAVING_KEYWORDS: tuple[str, ...] = (
    "材料", "钢筋", "混凝土", "用量", "浪费", "超配", "冗余", "重复配置",
    "版本冲突", "接口缺图", "图号重复", "管道综合", "节能", "荷载", "优化空间",
)

# LLM 创效判别复用 CLAUDE.md 预定义引擎名（经济测算类），无需新增 ModelRouter 引擎配置。
_SAVING_LLM_ENGINE = "optimization_hint_writer"


def _rule_based_saving_potential(finding: dict) -> bool:
    """规则判别（D-07 优先通道）：severity 高危 + 命中创效类关键词。"""
    if finding.get("severity") not in ("critical", "high"):
        return False
    haystack = f"{finding.get('title', '')} {finding.get('description', '')}"
    return any(keyword in haystack for keyword in _SAVING_KEYWORDS)


def _saving_llm_messages(finding: dict) -> list[dict]:
    prompt = (
        "你是工程创效线索判别助手。根据以下审图/审查问题，判断它是否有转化为「创效提案」"
        "的潜力（即整改或优化该问题能带来直接降本或间接增收，如节约材料、避免返工、"
        "优化设计方案）。\n"
        f"标题：{finding.get('title', '')}\n"
        f"描述：{finding.get('description', '')}\n"
        f"严重度：{finding.get('severity', '')}\n"
        "只返回如下 JSON，不要任何额外解释：\n"
        '{"has_saving_potential": false, "confidence": 0.0, "rationale": ""}'
    )
    return [{"role": "user", "content": prompt}]


def _parse_llm_json(raw: str) -> dict:
    """健壮解析 LLM 输出为 dict：剥离 markdown 围栏、截取首尾大括号，失败返回空 dict。"""
    text = (raw or "").strip()
    if not text:
        return {}
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return {}
    try:
        parsed = json.loads(text[start : end + 1])
    except (json.JSONDecodeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _no_llm_assessment() -> dict:
    return {"has_saving_potential": False, "source": "rule", "confidence": None, "rationale": None}


async def assess_saving_potential(finding: dict, *, router: Any = None) -> dict:
    """创效潜力判别：规则优先，LLM 仅做可选增强（召回补充，绝不覆盖规则正判）。

    - 规则命中 → 直接 True，``source="rule"``，不调用 LLM（省成本，多数场景走这条）。
    - 规则未命中 + 未注入 ``router``（默认）→ 原样返回规则结果（False），``source="rule"``。
    - 规则未命中 + 注入 ``router`` + severity 仍为高危 → 尝试
      ``ModelRouter.route("optimization_hint_writer", ...)``；引擎未配置 / 调用失败 /
      JSON 解析失败一律优雅降级为规则结果，**绝不抛异常中断主流程**（与 vlm_semantics
      的降级原则一致）。
    """
    if _rule_based_saving_potential(finding):
        return {"has_saving_potential": True, "source": "rule", "confidence": None, "rationale": None}

    if router is None or finding.get("severity") not in ("critical", "high"):
        return _no_llm_assessment()

    try:
        messages = _saving_llm_messages(finding)
        response = await router.route(_SAVING_LLM_ENGINE, messages)
        parsed = _parse_llm_json(getattr(response, "content", "") or "")
        potential = bool(parsed.get("has_saving_potential"))
        confidence = _as_float(parsed.get("confidence"))
        if confidence is not None:
            confidence = max(0.0, min(1.0, confidence))
        return {
            "has_saving_potential": potential,
            "source": "rule+llm" if potential else "rule",
            "confidence": confidence,
            "rationale": (str(parsed.get("rationale") or "")[:500] or None) if potential else None,
        }
    except Exception as exc:  # noqa: BLE001 — LLM 判别失败必须降级，绝不中断
        logger.warning(
            "[finding_service] LLM 创效判别失败 finding=%s:%s err=%s",
            finding.get("source"), finding.get("source_key"), exc,
        )
        return _no_llm_assessment()


def build_finding_proposal_description(finding: dict, extra_note: str | None = None) -> str:
    """由 Finding 组装创效提案**草稿**描述，标注来源便于三审可追溯。

    仅生成文本，不做任何数据库写入；调用方（routers/findings.py）负责实际 INSERT，
    与 ``routers/project_models.py`` 的 ``_qto_proposal_description`` 同一模式。
    """
    lines = [
        f"由审查中心 Finding 自动生成创效线索草稿。来源：{finding['source']}（{finding['id']}）。",
        f"原问题：{finding['title']}",
    ]
    if finding.get("description"):
        lines.append(f"详情：{finding['description']}")
    if finding.get("drawing_id"):
        lines.append(f"关联图纸：{finding['drawing_id']}")
    if extra_note:
        lines.append(extra_note)
    lines.append("（本草稿未经济测算，需经二审经济师测算与签字后方可进入公示/分配流程）")
    return "\n".join(lines)


# ── 对外聚合 API ─────────────────────────────────────────────

async def list_findings(
    db,
    project_id: str,
    *,
    source: str | None = None,
    severity: str | None = None,
    status: str | None = None,
    drawing_id: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> tuple[list[dict], dict]:
    """聚合项目全部 Finding（跨五类来源），返回 (分页后条目, 汇总统计)。"""
    sources = [source] if source else list(_FETCHERS)

    raw_items: list[dict] = []
    for src in sources:
        raw_items.extend(await _FETCHERS[src](db, project_id))

    overlay = await _fetch_status_overlay(db, project_id)
    items = [_finalize(item, overlay) for item in raw_items]

    if severity is not None:
        items = [it for it in items if it["severity"] == severity]
    if status is not None:
        items = [it for it in items if it["status"] == status]
    if drawing_id is not None:
        items = [it for it in items if it.get("drawing_id") == drawing_id]

    items.sort(key=_sort_key)

    summary = {
        "total": len(items),
        "by_source": _count_by(items, "source"),
        "by_severity": _count_by(items, "severity"),
        "by_status": _count_by(items, "status"),
        "saving_potential_count": sum(1 for it in items if it["has_saving_potential"]),
    }

    page = items[offset: offset + limit] if limit else items
    return page, summary


async def get_finding(db, project_id: str, source: str, source_key: str) -> dict | None:
    """单条 Finding 详情：只查该来源，避免为一条记录聚合全部五类来源。"""
    if source not in VALID_SOURCES:
        raise ValueError(f"invalid source: {source}")

    raw_items = await _FETCHERS[source](db, project_id)
    match = next((it for it in raw_items if it["source_key"] == source_key), None)
    if match is None:
        return None

    overlay = await _fetch_status_overlay(db, project_id)
    return _finalize(match, overlay)


# ── 状态机流转 ───────────────────────────────────────────────

_CURRENT_STATUS_SQL = """
SELECT status FROM finding_status WHERE project_id=$1 AND source=$2 AND source_key=$3
"""

_UPSERT_STATUS_SQL = """
INSERT INTO finding_status (project_id, source, source_key, status, note, updated_by, updated_at)
VALUES ($1, $2, $3, $4, $5, $6, now())
ON CONFLICT (project_id, source, source_key)
DO UPDATE SET status = $4, note = $5, updated_by = $6, updated_at = now()
RETURNING id, project_id, source, source_key, status, note, updated_by, updated_at
"""


def _validate_transition(current: str, target: str) -> None:
    if target not in STATUS_ORDER:
        raise ValueError(f"invalid status: {target}")
    if STATUS_ORDER.index(target) < STATUS_ORDER.index(current):
        raise InvalidTransitionError(
            f"cannot move backward: {current} -> {target}"
        )


async def update_finding_status(
    db,
    *,
    project_id: str,
    source: str,
    source_key: str,
    target_status: str,
    note: str | None = None,
    user_id: str | None = None,
) -> dict:
    """推进 Finding 闭环状态机（单向：pending→acknowledged→remediated→closed）。

    覆盖表内没有该 Finding 的记录时，起点视为 pending（不读来源表原生状态，
    避免五套原生状态语义相互打架，见迁移 026 文件头说明）。
    """
    if source not in VALID_SOURCES:
        raise ValueError(f"invalid source: {source}")

    current_row = await db.fetch_one(_CURRENT_STATUS_SQL, project_id, source, source_key)
    current_status = dict(current_row)["status"] if current_row is not None else "pending"

    _validate_transition(current_status, target_status)

    result = await db.fetch_one(
        _UPSERT_STATUS_SQL, project_id, source, source_key, target_status, note, user_id,
    )
    return dict(result)
