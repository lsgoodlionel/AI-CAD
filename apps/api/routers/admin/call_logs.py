"""调用日志与成本分析 API"""
from datetime import date
from fastapi import APIRouter, Depends, Query
from dependencies import get_db, require_admin

router = APIRouter(prefix="/admin/llm/logs", tags=["admin-llm"])


@router.get("/summary")
async def cost_summary(
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    engine_name: str | None = None,
    db=Depends(get_db), _=Depends(require_admin),
):
    """按引擎汇总：调用次数 / 成功率 / 平均延迟 / 总 token / 总费用"""
    where_clauses = ["1=1"]
    args: list = []
    i = 1
    if start_date:
        where_clauses.append(f"cl.created_at >= ${i}"); args.append(start_date); i += 1
    if end_date:
        where_clauses.append(f"cl.created_at < ${i}"); args.append(end_date); i += 1
    if engine_name:
        where_clauses.append(f"cl.engine_name = ${i}"); args.append(engine_name); i += 1
    where = " AND ".join(where_clauses)

    rows = await db.fetch_all(
        f"""
        SELECT cl.engine_name,
               lm.display_name        AS model_name,
               lp.name                AS provider_name,
               COUNT(*)               AS total_calls,
               SUM(CASE WHEN cl.success THEN 1 ELSE 0 END) AS success_calls,
               ROUND(AVG(cl.latency_ms))  AS avg_latency_ms,
               PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY cl.latency_ms) AS p95_latency_ms,
               SUM(cl.prompt_tokens)      AS total_prompt_tokens,
               SUM(cl.completion_tokens)  AS total_completion_tokens,
               ROUND(SUM(cl.cost_usd)::numeric, 4) AS total_cost_usd
        FROM llm_call_logs cl
        JOIN llm_models lm ON cl.model_db_id = lm.id
        JOIN llm_providers lp ON lm.provider_id = lp.id
        WHERE {where}
        GROUP BY cl.engine_name, lm.display_name, lp.name
        ORDER BY total_cost_usd DESC
        """,
        *args,
    )
    return [dict(r) for r in rows]


@router.get("/daily")
async def daily_cost(
    days: int = Query(default=30, ge=1, le=365),
    engine_name: str | None = None,
    db=Depends(get_db), _=Depends(require_admin),
):
    """每日费用趋势（用于折线图）"""
    where = "AND cl.engine_name = $2" if engine_name else ""
    args = [days, engine_name] if engine_name else [days]
    rows = await db.fetch_all(
        f"""
        SELECT DATE(cl.created_at) AS day,
               cl.engine_name,
               ROUND(SUM(cl.cost_usd)::numeric, 4) AS cost_usd,
               COUNT(*) AS calls
        FROM llm_call_logs cl
        WHERE cl.created_at >= now() - ($1 || ' days')::interval {where}
        GROUP BY DATE(cl.created_at), cl.engine_name
        ORDER BY day, cl.engine_name
        """,
        *args,
    )
    return [dict(r) for r in rows]


@router.get("/errors")
async def error_list(
    limit: int = Query(default=50, ge=1, le=500),
    engine_name: str | None = None,
    db=Depends(get_db), _=Depends(require_admin),
):
    where = "AND engine_name=$2" if engine_name else ""
    args = [limit, engine_name] if engine_name else [limit]
    rows = await db.fetch_all(
        f"""
        SELECT id, engine_name, error_type, latency_ms, created_at,
               lm.model_id, lp.name AS provider_name
        FROM llm_call_logs cl
        JOIN llm_models lm ON cl.model_db_id = lm.id
        JOIN llm_providers lp ON lm.provider_id = lp.id
        WHERE cl.success = false {where}
        ORDER BY cl.created_at DESC LIMIT $1
        """,
        *args,
    )
    return [dict(r) for r in rows]


@router.get("/circuit-breakers")
async def cb_status(db=Depends(get_db), _=Depends(require_admin)):
    """所有断路器当前状态（从 Redis 读取）"""
    from dependencies import get_redis
    from core.llm.circuit_breaker import CircuitBreaker
    redis = await get_redis()
    keys = await redis.keys("cb:*")
    result = []
    for key in keys:
        import json
        raw = await redis.get(key)
        data = json.loads(raw) if raw else {}
        result.append({"key": key.decode(), **data})
    return result
