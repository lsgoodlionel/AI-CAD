"""
分区维护任务 — Celery beat 定时任务

llm_call_logs 按月 RANGE 分区。本任务每月月初滚动创建「当月 + 次月」正式分区，
让日志始终写入按月裁剪的正式分区，而非 default 兜底分区（见 migration 028）。

设计：
- 纯函数 `month_partition_ddl` 负责计算分区名与边界并产出幂等 DDL，无副作用、可单测。
- 每个 CREATE 单独 try/except：即便某月因 default 分区已存在冲突行而失败，
  也只告警、不影响其余分区创建（此时该月行留在 default 分区，仍可查询）。
- 维护失败绝不能反向影响 LLM 调用链路——本任务与调用路径完全解耦。
"""
import asyncio
import logging
from datetime import date

import databases

from core.celery_app import celery_app
from core.config import settings

logger = logging.getLogger(__name__)

PARENT_TABLE = "llm_call_logs"
# 每次滚动维护覆盖的月份数（当月 + 未来若干月），留足提前量防止 beat 漏跑。
MONTHS_AHEAD = 2


def _first_of_next_month(d: date) -> date:
    """返回 d 所在月的下一个月 1 号。"""
    if d.month == 12:
        return date(d.year + 1, 1, 1)
    return date(d.year, d.month + 1, 1)


def month_partition_ddl(year: int, month: int) -> tuple[str, str]:
    """
    产出某一月份分区的（分区名, 幂等 CREATE DDL）。

    纯函数，不触库，便于单测。分区名形如 llm_call_logs_2026_07，
    边界 [当月 1 号, 次月 1 号)，与 002/028 迁移保持一致。
    """
    start = date(year, month, 1)
    end = _first_of_next_month(start)
    name = f"{PARENT_TABLE}_{year:04d}_{month:02d}"
    ddl = (
        f"CREATE TABLE IF NOT EXISTS {name} PARTITION OF {PARENT_TABLE} "
        f"FOR VALUES FROM ('{start.isoformat()}') TO ('{end.isoformat()}')"
    )
    return name, ddl


def upcoming_partition_ddls(today: date, months_ahead: int = MONTHS_AHEAD) -> list[tuple[str, str]]:
    """从 today 起，当月 + 未来 months_ahead 个月的分区 DDL 列表（纯函数）。"""
    ddls: list[tuple[str, str]] = []
    cursor = date(today.year, today.month, 1)
    for _ in range(months_ahead + 1):
        ddls.append(month_partition_ddl(cursor.year, cursor.month))
        cursor = _first_of_next_month(cursor)
    return ddls


@celery_app.task(name="tasks.partition_maintenance.ensure_llm_log_partitions")
def ensure_llm_log_partitions() -> dict:
    """滚动创建 llm_call_logs 当月及未来月份分区。"""
    return asyncio.run(_do_ensure())


async def _do_ensure(today: date | None = None) -> dict:
    today = today or date.today()
    db = databases.Database(settings.database_url)
    await db.connect()
    created: list[str] = []
    failed: list[str] = []
    try:
        for name, ddl in upcoming_partition_ddls(today):
            try:
                await db.execute(ddl)
                created.append(name)
            except Exception as exc:  # noqa: BLE001 — 单月失败不阻断其余月份
                failed.append(name)
                logger.warning("分区 %s 创建失败（default 分区兜底，仍可写入）: %s", name, exc)
    finally:
        await db.disconnect()
    logger.info("llm_call_logs 分区维护完成：确保 %s，失败 %s", created, failed)
    return {"ensured": created, "failed": failed}
