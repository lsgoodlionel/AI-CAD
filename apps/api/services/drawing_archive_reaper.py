"""档案僵死状态回收:把长时间卡在 `extracting` 的图纸放回可重抽状态。

**为什么必须有**:档案状态机是 `pending → extracting → ready → reviewed`。
抽取任务先置位 `extracting`,再做真正的活。**如果进程在中途被杀**
(镜像重建、OOM、容器重启),状态就永久停在 `extracting`——

- 它不会被重抽(没人再派任务)
- 它也不出现在任何失败列表里(状态看起来像「正在进行」)

结果是**静默丢失**。实测歌剧院项目有 10 张真实图纸这样卡了 **12~13 天**。

**设计取舍**:
- 只回收 `extracting`,终态(ready/reviewed)一律不碰——误杀等于毁数据;
- 时间戳缺失时**不判死**,宁可漏回收不可误杀;
- 重派次数有上限。永久坏的图(比如 MinIO 里文件真的没了)若每轮都重派,
  只会周期性刷错误日志;到上限后仍脱离 `extracting`,但不再重派,
  让它以 `pending` 出现在待处理清单里交给人工。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

#: 只有这个状态会被回收
ACTIVE_STATUS = "extracting"

#: 回收后置回的状态(状态机起点,可被重派)
RESET_STATUS = "pending"

#: 超过多少小时仍在 extracting 判为僵死。
#: 单图抽取实测秒级到分钟级(OCR ≈1s/图,VLM 最慢数分钟),2h 已极宽松,
#: 而僵死实例是 12~13 天,两者相差三个数量级,阈值不敏感。
STALE_EXTRACTING_HOURS = 2

#: 同一张图最多重派几次。超过说明它不是「偶发被杀」而是「本身有问题」
MAX_REAP_ATTEMPTS = 2

#: 回收次数记在 summary 这个自由 jsonb 列里,避免为此加一列
_ATTEMPTS_KEY = "reap_attempts"


def _as_utc(value: datetime) -> datetime:
    """驱动可能返回不带时区的时间;统一按 UTC 解读,避免比较时抛异常。"""
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def is_stale(status: str, started_at: datetime | None, *,
             now: datetime | None = None,
             max_hours: float = STALE_EXTRACTING_HOURS) -> bool:
    """是否为僵死的 extracting。

    边界取**严格超过**,不把刚好卡在阈值上的任务判死。
    """
    if status != ACTIVE_STATUS or started_at is None:
        return False
    moment = now or datetime.now(timezone.utc)
    return _as_utc(started_at) < moment - timedelta(hours=max_hours)


def reap_attempts_of(summary: object) -> int:
    """从 summary 读已回收次数。summary 是自由 jsonb,任何非法值一律当 0。"""
    if not isinstance(summary, dict):
        return 0
    value = summary.get(_ATTEMPTS_KEY)
    return value if isinstance(value, int) and value >= 0 else 0


def plan_reap(rows: list[dict], *, now: datetime | None = None,
              max_hours: float = STALE_EXTRACTING_HOURS,
              max_attempts: int = MAX_REAP_ATTEMPTS) -> list[dict]:
    """状态行 → 回收计划(纯函数,不改入参)。

    rows 每项需含 drawing_id / project_id / status / started_at / summary。
    返回 [{drawing_id, project_id, next_status, reap_attempts, requeue}]。
    """
    moment = now or datetime.now(timezone.utc)
    plan: list[dict] = []
    for row in rows:
        if not is_stale(row.get("status", ""), row.get("started_at"),
                        now=moment, max_hours=max_hours):
            continue
        attempts = reap_attempts_of(row.get("summary")) + 1
        plan.append({
            "drawing_id": row["drawing_id"],
            "project_id": row.get("project_id"),
            "next_status": RESET_STATUS,
            "reap_attempts": attempts,
            # 到达上限后仍脱离 extracting,但不再重派
            "requeue": attempts <= max_attempts,
        })
    return plan


def attempts_summary(reap_attempts: int) -> dict:
    """回收计划 → 要写回 summary 的片段。"""
    return {_ATTEMPTS_KEY: reap_attempts}
