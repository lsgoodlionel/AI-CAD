"""
数据看板 API

GET /dashboard/group          集团级看板（仅 group_admin）
GET /dashboard/project/{id}   项目级看板（所有已登录用户）
"""
from datetime import datetime, timezone
import inspect
from fastapi import APIRouter, Depends, HTTPException

from dependencies import get_db, get_current_user, require_admin

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

_YEAR_START = "date_trunc('year', now())"


# ── 集团看板 ────────────────────────────────────────────────────

@router.get("/group")
async def group_dashboard(db=Depends(get_db), _=Depends(require_admin)):
    result = _get_group_dashboard(db)
    if inspect.isawaitable(result):
        return await result
    return result


async def _get_group_dashboard(db):
    # 1. 年度创效总额 & 提案漏斗
    proposals = await db.fetch_all(
        """SELECT status, COUNT(*) AS cnt,
                  COALESCE(SUM(net_saving), 0) AS total_saving
           FROM incentive_proposals
           GROUP BY status"""
    )
    proposal_rows = [dict(r) for r in proposals]
    approved_statuses = {"approved", "paid"}
    annual_saving = sum(
        float(r["total_saving"]) for r in proposal_rows
        if r["status"] in approved_statuses
    )

    # 2. 图纸状态分布 & AI 覆盖率
    drawings = await db.fetch_all(
        """SELECT d.status, COUNT(*) AS cnt,
                  COUNT(r.id) FILTER (WHERE r.status='done') AS ai_done_cnt
           FROM drawings d
           LEFT JOIN ai_review_reports r ON r.drawing_id = d.id
           GROUP BY d.status"""
    )
    drawing_rows = [dict(r) for r in drawings]
    total_drawings = sum(r["cnt"] for r in drawing_rows)
    total_ai_done = sum(r["ai_done_cnt"] for r in drawing_rows)
    ai_coverage = round(total_ai_done / total_drawings, 4) if total_drawings else 0.0

    # 3. 三审通过率
    review_stats = await db.fetch_one(
        """SELECT
             COUNT(*) FILTER (WHERE tr.result='approved')::float /
               NULLIF(COUNT(tr.id), 0) AS tech_pass_rate,
             COUNT(*) FILTER (WHERE er.economist_signed_at IS NOT NULL)::float /
               NULLIF(COUNT(er.id), 0) AS econ_sign_rate
           FROM drawings d
           LEFT JOIN technical_reviews tr ON tr.drawing_id = d.id
           LEFT JOIN economic_reviews  er ON er.drawing_id = d.id"""
    )

    # 4. KPI 预警（年产值≥1亿，年度创效<50万）
    kpi_warnings = await db.fetch_all(
        f"""SELECT p.id, p.name, p.annual_output,
                   COALESCE(SUM(ip.net_saving), 0) AS year_saving
            FROM projects p
            LEFT JOIN incentive_proposals ip
              ON ip.project_id = p.id
             AND ip.status IN ('approved','paid')
             AND ip.created_at >= {_YEAR_START}
            WHERE p.annual_output >= 100000000
            GROUP BY p.id, p.name, p.annual_output
            HAVING COALESCE(SUM(ip.net_saving), 0) < 500000"""
    )

    # 5. LLM 调用成本（近 30 天，按引擎）
    llm_costs = await db.fetch_all(
        """SELECT engine_name,
                  COUNT(*)                              AS call_count,
                  SUM(prompt_tokens + completion_tokens) AS total_tokens,
                  SUM(cost_usd)                         AS total_cost_usd,
                  ROUND(AVG(latency_ms))                AS avg_latency_ms,
                  COUNT(*) FILTER (WHERE NOT success)   AS error_count
           FROM llm_call_logs
           WHERE created_at >= now() - interval '30 days'
           GROUP BY engine_name
           ORDER BY total_cost_usd DESC"""
    )

    # 6. 规范库统计
    reg_stats = await db.fetch_one(
        """SELECT
             COUNT(DISTINCT b.id) AS book_count,
             COUNT(a.id)          AS article_count,
             COUNT(a.id) FILTER (WHERE a.is_mandatory) AS mandatory_count,
             COUNT(a.id) FILTER (WHERE a.vector_id IS NOT NULL) AS vectorized_count
           FROM regulation_books b
           LEFT JOIN regulation_articles a ON a.book_id = b.id
           WHERE b.status = 'active'"""
    )

    return {
        "annual_saving_yuan": round(annual_saving, 2),
        "proposal_funnel": proposal_rows,
        "drawing_overview": {
            "total": total_drawings,
            "by_status": drawing_rows,
            "ai_coverage_rate": ai_coverage,
        },
        "review_stats": {
            "tech_pass_rate": round(float(review_stats["tech_pass_rate"] or 0), 4),
            "econ_sign_rate": round(float(review_stats["econ_sign_rate"] or 0), 4),
        },
        "kpi_warnings": [dict(r) for r in kpi_warnings],
        "llm_cost_30d": [dict(r) for r in llm_costs],
        "regulation_stats": dict(reg_stats) if reg_stats else {},
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# ── 项目看板 ────────────────────────────────────────────────────

@router.get("/project/{project_id}")
async def project_dashboard(
    project_id: str,
    db=Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    result = _get_project_dashboard(project_id, db, current_user)
    if inspect.isawaitable(result):
        return await result
    return result


async def _get_project_dashboard(project_id: str, db, current_user: dict):
    project = await db.fetch_one(
        "SELECT id, name, annual_output, status FROM projects WHERE id=$1", project_id
    )
    if not project:
        raise HTTPException(404, "项目不存在")

    # 1. 图纸状态分布
    drawings_status = await db.fetch_all(
        "SELECT status, COUNT(*) AS cnt FROM drawings WHERE project_id=$1 GROUP BY status",
        project_id,
    )

    # 2. AI 审图质量
    ai_quality = await db.fetch_one(
        """SELECT
             COUNT(DISTINCT r.id)                            AS reviewed_count,
             COALESCE(AVG(r.total_issues), 0)               AS avg_issues,
             COALESCE(SUM(r.critical_issues), 0)            AS total_critical,
             COUNT(DISTINCT r.id) FILTER (WHERE r.critical_issues > 0) AS drawings_with_critical
           FROM ai_review_reports r
           JOIN drawings d ON d.id = r.drawing_id
           WHERE d.project_id = $1 AND r.status = 'done'""",
        project_id,
    )

    # 3. 三审流程耗时
    stage_duration = await db.fetch_all(
        """SELECT
             discipline,
             COUNT(*) FILTER (WHERE status='published') AS published_cnt,
             COUNT(*) FILTER (WHERE status='rejected')  AS rejected_cnt,
             COUNT(*)                                   AS total_cnt
           FROM drawings
           WHERE project_id = $1
           GROUP BY discipline""",
        project_id,
    )

    # 4. 提案漏斗
    proposals = await db.fetch_all(
        """SELECT status, COUNT(*) AS cnt,
                  COALESCE(SUM(net_saving), 0) AS total_saving
           FROM incentive_proposals
           WHERE project_id = $1
           GROUP BY status""",
        project_id,
    )
    approved_saving = sum(
        float(r["total_saving"]) for r in proposals
        if r["status"] in {"approved", "paid"}
    )

    # 5. KPI 红线判断
    kpi_threshold = 500_000.0
    annual_output = float(project["annual_output"] or 0)
    kpi_red = annual_output >= 100_000_000 and approved_saving < kpi_threshold

    # 6. 近期活动（最近 15 条审计日志）
    recent = await db.fetch_all(
        """SELECT al.action, al.resource, al.resource_id,
                  al.new_state, al.created_at,
                  u.display_name AS operator
           FROM audit_logs al
           LEFT JOIN users u ON u.id = al.user_id
           WHERE al.resource_id IN (
               SELECT id FROM drawings WHERE project_id = $1
               UNION ALL
               SELECT id FROM incentive_proposals WHERE project_id = $1
           )
           ORDER BY al.created_at DESC
           LIMIT 15""",
        project_id,
    )

    return {
        "project": dict(project),
        "drawings_by_status": [dict(r) for r in drawings_status],
        "ai_quality": dict(ai_quality) if ai_quality else {},
        "stage_distribution": [dict(r) for r in stage_duration],
        "proposal_funnel": [dict(r) for r in proposals],
        "annual_saving_yuan": round(approved_saving, 2),
        "kpi_red_flag": kpi_red,
        "recent_activity": [dict(r) for r in recent],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
