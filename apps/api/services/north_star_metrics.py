"""Phase D · D-24 度量埋点：三个北极星指标的纯计算 + 参数化取数。

北极星指标定义（口径锁定一次，供看板/测试/未来迭代共同引用，避免各处口径漂移）：
  ①关键路径完成时长 —— 项目从首图上传到首个创效提案草稿的时长（中位数，小时）。
  ②建模自动触发采纳率 —— D-08 事件编排层生成的 rebuild_model 建议中
     accepted / (accepted + dismissed)。
  ③审校单条耗时 —— model_review_actions 按审校员分组后，相邻动作时间差的中位数（秒）。

与 routers/dashboard.py 的 C-17 返工点度量保持同一分层风格：
  `fetch_*`  —— 参数化 SQL 取数（防注入，project_id 走占位符），只读；
  `compute_*` —— 纯函数聚合，不接触数据库，单测无需 mock DB。

诚实口径：任何指标在样本为 0 时返回 None（不用 0 填充制造假象），
sampleSize 始终如实反映参与计算的样本数，供前端/使用者自行判断置信度。
"""
from __future__ import annotations

from datetime import datetime
from statistics import median
from typing import Any, Mapping, Sequence

_UNKNOWN_REVIEWER = "未知审校员"

# pipeline_suggestions 中"已收敛"的状态：只有走完人工决策的建议才计入采纳率分母；
# open（尚未处理）计入会随时间自然变化、不是稳定口径，故排除。
_RESOLVED_SUGGESTION_STATUSES = frozenset({"accepted", "dismissed"})


# ── 取数（参数化 SQL，project_id 可选过滤）───────────────────────────────

async def fetch_critical_path_rows(
    db, project_id: str | None
) -> list[Mapping[str, Any]]:
    """每个项目的「首图上传时间」与「首个创效提案草稿创建时间」配对行。

    incentive_proposals.created_at 恒为草稿创建时刻（无论后续状态如何流转），
    故每个项目最早一条提案的 created_at 即为「首个创效提案草稿」时间，
    无需额外按 status='draft' 过滤（过滤反而会漏掉已流转出 draft 的最早提案）。
    """
    params: list[Any] = []
    where = ""
    if project_id:
        params.append(project_id)
        where = f"WHERE fd.project_id = ${len(params)}"
    sql = f"""
        SELECT fd.project_id, fd.first_drawing_at, fp.first_proposal_at
        FROM (SELECT project_id, MIN(created_at) AS first_drawing_at
              FROM drawings GROUP BY project_id) fd
        JOIN (SELECT project_id, MIN(created_at) AS first_proposal_at
              FROM incentive_proposals GROUP BY project_id) fp
          ON fp.project_id = fd.project_id
        {where}
    """
    rows = await db.fetch_all(sql, *params)
    return [dict(r) for r in (rows or [])]


async def fetch_pipeline_suggestion_rows(
    db, project_id: str | None
) -> list[Mapping[str, Any]]:
    """已收敛（accepted/dismissed）的管线建议行，供采纳率计算。"""
    conditions = ["status IN ('accepted', 'dismissed')"]
    params: list[Any] = []
    if project_id:
        params.append(project_id)
        conditions.append(f"project_id = ${len(params)}")
    where = f"WHERE {' AND '.join(conditions)}"
    sql = f"SELECT suggestion_type, status FROM pipeline_suggestions {where}"
    rows = await db.fetch_all(sql, *params)
    return [dict(r) for r in (rows or [])]


async def fetch_review_action_timing_rows(
    db, project_id: str | None
) -> list[Mapping[str, Any]]:
    """人审动作埋点的 (reviewer_id, created_at)，按审校员+时间排序供耗时计算。"""
    params: list[Any] = []
    where = ""
    if project_id:
        params.append(project_id)
        where = f"WHERE project_id = ${len(params)}"
    sql = (
        "SELECT reviewer_id, created_at FROM model_review_actions "
        f"{where} ORDER BY reviewer_id, created_at"
    )
    rows = await db.fetch_all(sql, *params)
    return [dict(r) for r in (rows or [])]


# ── 纯计算 ───────────────────────────────────────────────────────────────

def _as_datetime(value: Any) -> datetime | None:
    """兼容 asyncpg datetime / 测试注入的 ISO 字符串；解析失败返回 None（不硬造）。"""
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def compute_critical_path_metric(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """①关键路径完成时长：首图上传 → 首个创效提案草稿，跨项目取中位数（小时）。

    口径：
      - diff = first_proposal_at - first_drawing_at，仅纳入 diff >= 0 的项目
        （负值视为脏数据/时钟异常，剔除，不参与中位数，也不当 0 处理）；
      - 中位数抗离群，避免个别超长/超短项目扭曲全局观感；
      - 无首图或无提案的项目本就不会出现在 rows 中（内连接），不产生样本，
        不会被误当成「耗时为 0」。
    """
    durations_hours: list[float] = []
    for row in rows:
        first_drawing = _as_datetime(row.get("first_drawing_at"))
        first_proposal = _as_datetime(row.get("first_proposal_at"))
        if first_drawing is None or first_proposal is None:
            continue
        delta_seconds = (first_proposal - first_drawing).total_seconds()
        if delta_seconds < 0:
            continue
        durations_hours.append(delta_seconds / 3600.0)

    return {
        "medianHours": round(median(durations_hours), 2) if durations_hours else None,
        "sampleSize": len(durations_hours),
        "unit": "hours",
    }


def _adoption_rate(bucket: Mapping[str, int]) -> float | None:
    total = bucket["accepted"] + bucket["dismissed"]
    return round(bucket["accepted"] / total, 4) if total else None


def compute_adoption_metric(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """②建模自动触发采纳率：rebuild_model 建议中 accepted / (accepted+dismissed)。

    口径：
      - 数据源 pipeline_suggestions（D-08 事件编排层生成的「自动打底」建议）；
      - 头图指标聚焦 suggestion_type='rebuild_model'（「建模自动触发」字面对应
        的建议类型）；create_proposal 的采纳率一并在 bySuggestionType 给出供
        参考，不计入本北极星指标本身，避免两类不同性质的建议被平均后失真。
    """
    by_type: dict[str, dict[str, int]] = {}
    for row in rows:
        suggestion_type = row.get("suggestion_type") or "unknown"
        status = row.get("status")
        bucket = by_type.setdefault(suggestion_type, {"accepted": 0, "dismissed": 0})
        if status in _RESOLVED_SUGGESTION_STATUSES:
            bucket[status] += 1

    rebuild_bucket = by_type.get("rebuild_model", {"accepted": 0, "dismissed": 0})
    return {
        "rate": _adoption_rate(rebuild_bucket),
        "accepted": rebuild_bucket["accepted"],
        "dismissed": rebuild_bucket["dismissed"],
        "sampleSize": rebuild_bucket["accepted"] + rebuild_bucket["dismissed"],
        "bySuggestionType": {
            suggestion_type: {**bucket, "rate": _adoption_rate(bucket)}
            for suggestion_type, bucket in by_type.items()
        },
    }


def compute_review_duration_metric(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """③审校单条耗时：model_review_actions 相邻动作时间差中位数（秒）。

    口径：
      - 按 reviewer_id 分组后组内按 created_at 排序求相邻差值，避免不同审校员
        交替动作被误判为「同一人连续处理两条」（会显著低估耗时）；
      - reviewer_id 缺失的行归入统一分组「未知审校员」，仍参与统计不丢样本，
        但混合多人时长度可能偏乐观；
      - 全部差值汇总取中位数（抗离群：跨天/跨会话的长间隔不会像均值那样拉高
        结果，这也是选中位数而非均值的原因）；
      - 组内只有 1 条动作时无相邻差值，不产生样本。
    """
    grouped: dict[str, list[datetime]] = {}
    for row in rows:
        created_at = _as_datetime(row.get("created_at"))
        if created_at is None:
            continue
        reviewer = row.get("reviewer_id") or _UNKNOWN_REVIEWER
        grouped.setdefault(reviewer, []).append(created_at)

    diffs_seconds: list[float] = []
    for timestamps in grouped.values():
        ordered = sorted(timestamps)
        for prev, curr in zip(ordered, ordered[1:]):
            diffs_seconds.append((curr - prev).total_seconds())

    return {
        "medianSeconds": round(median(diffs_seconds), 1) if diffs_seconds else None,
        "sampleSize": len(diffs_seconds),
        "unit": "seconds",
    }
