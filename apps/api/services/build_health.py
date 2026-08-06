"""建模任务健康检查 —— 识别「僵尸构建」。纯函数。

**实测问题**:建模进度停在 2308/2309,而 Celery worker 活跃任务为空——任务已死
(worker 重启/OOM/超时),但 `project_models.status` 仍是 `building`,前端永远显示
「构建中」,用户既看不到结果也不知道能重建。

**判定**:status=building 且 `updated_at` 超过 `STALE_AFTER_SEC` 未推进 → 判为僵尸。
建模每处理一张图都会写进度,正常构建不会长时间不更新。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

#: 超过此时长无进度更新即判僵尸(建模每张图都会写进度,10 分钟足够宽松)
STALE_AFTER_SEC = 600


def is_stale_building(
    status: str | None, updated_at: datetime | None, now: datetime | None = None,
    stale_after_sec: int = STALE_AFTER_SEC,
) -> bool:
    """构建是否已僵死(状态 building 但长时间无进度)。"""
    if (status or "") != "building" or updated_at is None:
        return False
    current = now or datetime.now(timezone.utc)
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)
    return current - updated_at > timedelta(seconds=stale_after_sec)


def build_health(
    status: str | None, updated_at: datetime | None, progress: dict | None,
    now: datetime | None = None,
) -> dict:
    """构建健康度 → {stale, hint}。stale=True 时给出可操作提示。"""
    stale = is_stale_building(status, updated_at, now)
    hint = ""
    if stale:
        done = (progress or {}).get("done")
        total = (progress or {}).get("total")
        where = f"(停在 {done}/{total})" if done is not None and total else ""
        hint = f"构建似乎已中断{where},可点「重新生成模型」重试"
    return {"stale": stale, "hint": hint}
