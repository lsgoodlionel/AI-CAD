"""Finding 表行来源 SQL 下推查询（Phase D · 泳道2 · D-05 性能优化）。

背景：``services/finding_service.list_findings`` 原本把某项目**全部**来源的 Finding
全量拉到 Python（单项目 engine 源实测 2.3 万+ 条），再在 Python 端 finalize/排序/
筛选/分页——每次翻页都要重建 2 万+ dict + 全量排序，内存与延迟随项目规模劣化。

本模块负责把三类**表行来源**（engine=ai_review_issues / review=review_audit_findings /
symbol=model_symbol_annotations）的**筛选（severity/status/drawing_id）+ 排序 + 分页**
下推到 SQL：
  - severity / status / saving 标记全部在 SQL 里用 CASE 计算（CASE 由 finding_service
    的 Python 映射表**程序化生成**，避免 SQL 与 Python 语义漂移）；
  - 状态覆盖表 finding_status 以 LEFT JOIN 就地并入，``COALESCE(overlay, 原生默认)``；
  - 分页只取 ``LIMIT offset+limit`` 行（跨源归并所需的最小上界，见
    ``finding_service._merge_and_slice``）；
  - 汇总计数走一条 ``GROUP BY severity, status`` 聚合查询（返回 ≤16 行而非全表）。

cross/semantic 两类来源是 JSONB 运行时派生、非表行，无法纯 SQL 分页，仍由
finding_service 走全量拉取 + 数量上限（见该模块 ``_cap_derived``）。

降级：每个表行来源查询独立 try/except（由 finding_service 调用侧兜底），单源失败
（如某来源表在当前部署缺失/未迁移）跳过该源、不阻断其余来源聚合，与原
``_safe_fetch`` 语义一致。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from services import finding_service as fs

# 表行来源集合（可 SQL 下推）；cross/semantic 为派生来源，不在此列。
TABLE_SOURCES: tuple[str, ...] = ("engine", "review", "symbol")


# ── SQL 片段生成（由 Python 映射表程序化派生，杜绝语义漂移）──────────

def _sql_case(column: str, mapping: dict[str, str], default: str) -> str:
    """把 Python 值映射表编译为 SQL ``CASE column WHEN 'k' THEN 'v' ... ELSE 'default'``。

    仅用于内部固定映射（严重度/状态），键值均来自模块内常量、无用户输入，
    故直接字符串内插安全。"""
    whens = " ".join(f"WHEN '{k}' THEN '{v}'" for k, v in mapping.items())
    return f"CASE {column} {whens} ELSE '{default}' END"


# severity → 排序 rank（对齐 fs._SEVERITY_RANK），作用于已归一的 severity 别名。
_SEVERITY_RANK_SQL = (
    "CASE severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1 "
    "WHEN 'medium' THEN 2 WHEN 'low' THEN 3 ELSE 9 END"
)

# 创效潜力标记（对齐 fs._rule_based_saving_potential）：高危 + 命中任一关键词。
# 关键词集合共享 fs._SAVING_KEYWORDS（单一事实源），拼为 POSIX 正则的选择分支；
# 关键词均为中文、无正则元字符，直接内插安全。
_SAVING_REGEX = "|".join(fs._SAVING_KEYWORDS)
_SAVING_SQL = f"(severity IN ('critical', 'high') AND haystack ~ '{_SAVING_REGEX}')"


# ── 三类表行来源的「归一化」内层 SELECT（$1 = project_id）──────────
# 每条产出统一列：source_key / drawing_id / severity / status / note /
# status_updated_at / created_at / native_status / haystack（创效关键词命中用）
# + 各源保留供 Python 组装 title/description 的原始字段。

_ENGINE_MAPPED = f"""
SELECT
    i.id::text AS source_key,
    r.drawing_id::text AS drawing_id,
    {_sql_case("i.severity", fs._ENGINE_SEVERITY_MAP, "medium")} AS severity,
    COALESCE(fs.status, {_sql_case("i.status", fs._ENGINE_STATUS_DEFAULT, "pending")}) AS status,
    fs.note AS note,
    fs.updated_at AS status_updated_at,
    i.created_at AS created_at,
    i.status AS native_status,
    i.category AS category,
    i.description AS description,
    i.suggestion AS suggestion,
    i.location_json AS location_json,
    (COALESCE(i.category, '') || ' '
        || COALESCE(NULLIF(i.description, ''), COALESCE(i.suggestion, ''), '')) AS haystack
FROM ai_review_issues i
JOIN ai_review_reports r ON r.id = i.report_id
JOIN drawings d ON d.id = r.drawing_id
LEFT JOIN finding_status fs
    ON fs.project_id = d.project_id AND fs.source = 'engine' AND fs.source_key = i.id::text
WHERE d.project_id = $1
"""

_REVIEW_MAPPED = f"""
SELECT
    f.id::text AS source_key,
    NULL::text AS drawing_id,
    {_sql_case("f.risk_level", fs._RISK_LEVEL_SEVERITY_MAP, "medium")} AS severity,
    COALESCE(fs.status, 'pending') AS status,
    fs.note AS note,
    fs.updated_at AS status_updated_at,
    f.created_at AS created_at,
    NULL::text AS native_status,
    f.discipline_name AS discipline_name,
    f.object_level AS object_level,
    f.standard_question AS standard_question,
    f.location_json AS location_json,
    (COALESCE(f.discipline_name, '') || '会审发现' || COALESCE(f.object_level, '')
        || ' ' || COALESCE(f.standard_question, '')) AS haystack
FROM review_audit_findings f
JOIN review_audit_records rec ON rec.id = f.record_id
LEFT JOIN finding_status fs
    ON fs.project_id = rec.project_id AND fs.source = 'review' AND fs.source_key = f.id::text
WHERE rec.project_id = $1
"""

# 符号严重度由置信度分桶（对齐 fs._severity_from_confidence，conflict 恒 False）。
_SYMBOL_MAPPED = f"""
SELECT
    s.id::text AS source_key,
    s.drawing_id::text AS drawing_id,
    CASE
        WHEN s.confidence IS NULL THEN 'medium'
        WHEN s.confidence < 0.5 THEN 'high'
        WHEN s.confidence < 0.8 THEN 'medium'
        ELSE 'low'
    END AS severity,
    COALESCE(fs.status, {_sql_case("s.status", fs._SYMBOL_STATUS_DEFAULT, "pending")}) AS status,
    fs.note AS note,
    fs.updated_at AS status_updated_at,
    s.created_at AS created_at,
    s.status AS native_status,
    s.category AS category,
    s.mep_system AS mep_system,
    s.confidence AS confidence,
    (COALESCE(s.category, '') || ' ' || COALESCE(s.mep_system, '')) AS haystack
FROM model_symbol_annotations s
LEFT JOIN finding_status fs
    -- model_symbol_annotations.project_id 是 text，finding_status.project_id 是 uuid，
    -- 两侧统一转 text 比较（避免 uuid=text 类型不匹配；uuid→text 转换恒安全）。
    ON fs.project_id::text = s.project_id AND fs.source = 'symbol' AND fs.source_key = s.id::text
WHERE s.project_id = $1
"""


# ── 行 → 最终 Finding 形态（与 fs._finalize 输出同构）──────────────

def _finding_from_engine_row(row: dict, project_id: str) -> dict:
    return {
        "id": f"engine:{row['source_key']}",
        "source": "engine",
        "project_id": str(project_id),
        "drawing_id": row.get("drawing_id"),
        "severity": row["severity"],
        "title": row.get("category") or "AI 审图问题",
        "description": row.get("description") or row.get("suggestion") or "",
        "status": row["status"],
        "location": fs._parse_json(row.get("location_json"), None),
        "note": row.get("note"),
        "status_updated_at": row.get("status_updated_at"),
        "created_at": row.get("created_at"),
        "has_saving_potential": bool(row.get("saving_flag")),
    }


def _finding_from_review_row(row: dict, project_id: str) -> dict:
    discipline = row.get("discipline_name") or "会审"
    title = f"{discipline}会审发现" + (
        f"（{row['object_level']}）" if row.get("object_level") else ""
    )
    return {
        "id": f"review:{row['source_key']}",
        "source": "review",
        "project_id": str(project_id),
        "drawing_id": None,
        "severity": row["severity"],
        "title": title,
        "description": row.get("standard_question") or "",
        "status": row["status"],
        "location": fs._parse_json(row.get("location_json"), None),
        "note": row.get("note"),
        "status_updated_at": row.get("status_updated_at"),
        "created_at": row.get("created_at"),
        "has_saving_potential": bool(row.get("saving_flag")),
    }


def _finding_from_symbol_row(row: dict, project_id: str) -> dict:
    confidence = fs._as_float(row.get("confidence"))
    description = (
        f"{row.get('category') or ''} 符号候选"
        + (f"（{row['mep_system']}）" if row.get("mep_system") else "")
        + f"，置信度 {confidence if confidence is not None else 'N/A'}"
    )
    return {
        "id": f"symbol:{row['source_key']}",
        "source": "symbol",
        "project_id": str(project_id),
        "drawing_id": row.get("drawing_id"),
        "severity": row["severity"],
        "title": f"符号待审：{row.get('category') or '未知类别'}",
        "description": description,
        "status": row["status"],
        "location": None,
        "note": row.get("note"),
        "status_updated_at": row.get("status_updated_at"),
        "created_at": row.get("created_at"),
        "has_saving_potential": bool(row.get("saving_flag")),
    }


@dataclass(frozen=True)
class _SourceSpec:
    mapped_sql: str
    # 内层 mapped 中供 Python 组装 title/description 的原始列（回填到输出 SELECT）
    raw_cols: str
    mapper: Callable[[dict, str], dict]


_SPECS: dict[str, _SourceSpec] = {
    "engine": _SourceSpec(
        _ENGINE_MAPPED, "category, description, suggestion, location_json",
        _finding_from_engine_row,
    ),
    "review": _SourceSpec(
        _REVIEW_MAPPED, "discipline_name, object_level, standard_question, location_json",
        _finding_from_review_row,
    ),
    "symbol": _SourceSpec(
        _SYMBOL_MAPPED, "category, mep_system, confidence",
        _finding_from_symbol_row,
    ),
}


# ── 计数聚合 ─────────────────────────────────────────────────────

def _aggregate_source_counts(source: str, count_rows: list) -> dict:
    """把一个来源的 ``GROUP BY severity, status`` 结果聚合为统一计数结构。"""
    by_severity: dict[str, int] = {}
    by_status: dict[str, int] = {}
    total = 0
    saving = 0
    for raw in count_rows:
        row = dict(raw)
        n = int(row["n"])
        sn = int(row["sn"] or 0)
        total += n
        saving += sn
        by_severity[row["severity"]] = by_severity.get(row["severity"], 0) + n
        by_status[row["status"]] = by_status.get(row["status"], 0) + n
    return {
        "total": total,
        # 与旧 _count_by 一致：来源计数为空时不出现在 by_source 里
        "by_source": {source: total} if total else {},
        "by_severity": by_severity,
        "by_status": by_status,
        "saving": saving,
    }


# ── SQL 组装 ─────────────────────────────────────────────────────

def _build_filter(severity, status, drawing_id, start_idx: int) -> tuple[str, list, int]:
    """把 severity/status/drawing_id 编译为下推 WHERE 片段（作用于 mapped 别名）。

    返回 ``(sql_fragment, extra_args, next_param_idx)``；无筛选时片段为空串。"""
    conds: list[str] = []
    args: list[Any] = []
    idx = start_idx
    if severity is not None:
        conds.append(f"severity = ${idx}")
        args.append(severity)
        idx += 1
    if status is not None:
        conds.append(f"status = ${idx}")
        args.append(status)
        idx += 1
    if drawing_id is not None:
        conds.append(f"drawing_id = ${idx}")
        args.append(drawing_id)
        idx += 1
    fragment = (" AND " + " AND ".join(conds)) if conds else ""
    return fragment, args, idx


def _page_sql(spec: _SourceSpec, where: str, limit_param: str | None) -> str:
    limit_clause = f"\nLIMIT {limit_param}" if limit_param else ""
    return (
        f"WITH mapped AS ({spec.mapped_sql})\n"
        f"SELECT source_key, drawing_id, severity, status, note, status_updated_at,\n"
        f"       created_at, {spec.raw_cols}, {_SAVING_SQL} AS saving_flag\n"
        f"FROM mapped\n"
        f"WHERE 1=1{where}\n"
        f"ORDER BY {_SEVERITY_RANK_SQL} ASC, created_at DESC NULLS LAST{limit_clause}"
    )


def _count_sql(spec: _SourceSpec, where: str) -> str:
    return (
        f"WITH mapped AS ({spec.mapped_sql})\n"
        f"SELECT severity, status, COUNT(*) AS n,\n"
        f"       COUNT(*) FILTER (WHERE {_SAVING_SQL}) AS sn\n"
        f"FROM mapped\n"
        f"WHERE 1=1{where}\n"
        f"GROUP BY severity, status"
    )


# ── 对外查询入口 ─────────────────────────────────────────────────

async def query_table_source(
    db,
    source: str,
    project_id: str,
    *,
    severity: str | None = None,
    status: str | None = None,
    drawing_id: str | None = None,
    top_n: int | None = None,
) -> tuple[list[dict], dict]:
    """下推查询单个表行来源，返回 ``(分页后 Finding 列表, 计数结构)``。

    - ``top_n``：分页窗口大小（= offset+limit）。为 None 时不加 LIMIT（回退全量，
      仅供显式无分页调用；热路径始终传有界 top_n）。
    - 计数结构：``{total, by_source, by_severity, by_status, saving}``，基于**筛选后**
      全量（GROUP BY 聚合，不受分页影响），供上层合并出 meta。
    """
    if source not in _SPECS:
        raise ValueError(f"not a table source: {source}")
    spec = _SPECS[source]

    where, filter_args, next_idx = _build_filter(severity, status, drawing_id, start_idx=2)

    # 分页查询：过滤 + 排序 + LIMIT offset+limit（只物化窗口所需行）
    page_args: list[Any] = [project_id, *filter_args]
    limit_param: str | None = None
    if top_n:
        limit_param = f"${next_idx}"
        page_args.append(top_n)
    page_rows = await db.fetch_all(_page_sql(spec, where, limit_param), *page_args)
    findings = [spec.mapper(dict(row), project_id) for row in page_rows]

    # 计数查询：GROUP BY 聚合（返回 ≤16 行），与分页共享同一 WHERE
    count_args: list[Any] = [project_id, *filter_args]
    count_rows = await db.fetch_all(_count_sql(spec, where), *count_args)
    counts = _aggregate_source_counts(source, count_rows)

    return findings, counts
