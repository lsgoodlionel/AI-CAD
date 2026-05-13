"""
外部规范 API 定时同步任务

beat_schedule 每小时整点调用 sync_due_sources_task：
  - 扫描 regulation_api_sources 表，找出 is_active=true 且距上次同步
    已超过 sync_interval_hours 的数据源
  - 对每条数据源调用外部 HTTP API，增量抓取规范条文，写入 DB/Chroma/AGE

支持 auth_type:
  - api_key  : Authorization: Bearer <key>  （key 来自 auth_config.api_key）
  - basic    : HTTP Basic（auth_config.username / .password）
  - none     : 无认证

外部 API 响应格式约定（可通过 auth_config.response_path 自定义取值路径）：
  {
    "items": [
      {
        "article_no": "3.1.2",
        "title": "...",
        "content": "...",
        "obligation_level": "MUST",   # MUST/SHOULD/MAY/MUST_NOT
        "is_mandatory": true,
        "chapter_no": "3"
      }
    ],
    "total": 42
  }
"""
import asyncio
import json
import logging
from typing import Optional

import databases
import httpx

from core.celery_app import celery_app
from core.config import settings
from services.audit import write_audit

logger = logging.getLogger(__name__)

MAX_ITEMS_PER_SYNC = 500    # 单次同步条目上限，防止长尾任务阻塞
REQUEST_TIMEOUT_SEC = 30


# ── Celery 任务入口 ───────────────────────────────────────────────

@celery_app.task(
    name="tasks.regulation_api_sync.sync_due_sources_task",
    bind=True,
    max_retries=0,      # beat 任务不重试，下次 tick 自然重试
)
def sync_due_sources_task(self) -> dict:
    """扫描到期数据源并逐一同步（每小时 beat 驱动）"""
    return asyncio.run(_sync_due())


@celery_app.task(
    name="tasks.regulation_api_sync.sync_single_source_task",
    bind=True,
    max_retries=2,
    default_retry_delay=300,
)
def sync_single_source_task(self, source_id: str) -> dict:
    """手动触发单个数据源同步（管理员从后台发起）"""
    return asyncio.run(_sync_source_by_id(source_id))


# ── 核心异步逻辑 ─────────────────────────────────────────────────

async def _sync_due() -> dict:
    db = databases.Database(settings.database_url)
    await db.connect()
    results = []
    try:
        due_sources = await db.fetch_all(
            """
            SELECT id, name, endpoint_url, auth_type, auth_config, sync_interval_hours
            FROM regulation_api_sources
            WHERE is_active = true
              AND (
                last_synced_at IS NULL
                OR last_synced_at + (sync_interval_hours * INTERVAL '1 hour') <= now()
              )
            """
        )
        for source in due_sources:
            result = await _do_sync(db, dict(source))
            results.append({"source_id": str(source["id"]), "name": source["name"], **result})
    finally:
        await db.disconnect()
    logger.info("regulation_api_sync: 本次共同步 %d 个数据源", len(results))
    return {"synced": len(results), "results": results}


async def _sync_source_by_id(source_id: str) -> dict:
    db = databases.Database(settings.database_url)
    await db.connect()
    try:
        source = await db.fetch_one(
            "SELECT id, name, endpoint_url, auth_type, auth_config, sync_interval_hours "
            "FROM regulation_api_sources WHERE id=$1",
            source_id,
        )
        if not source:
            return {"error": f"数据源 {source_id} 不存在"}
        return await _do_sync(db, dict(source))
    finally:
        await db.disconnect()


async def _do_sync(db: databases.Database, source: dict) -> dict:
    source_id = str(source["id"])
    source_name = source["name"]
    auth_config: dict = source.get("auth_config") or {}
    if isinstance(auth_config, str):
        auth_config = json.loads(auth_config)

    try:
        items = await _fetch_remote_articles(
            endpoint_url=source["endpoint_url"],
            auth_type=source["auth_type"],
            auth_config=auth_config,
        )
    except Exception as exc:
        logger.error("数据源 %s 拉取失败: %s", source_name, exc)
        await db.execute(
            "UPDATE regulation_api_sources SET last_sync_error=$1, updated_at=now() WHERE id=$2",
            str(exc), source_id,
        )
        return {"error": str(exc), "upserted": 0}

    if not items:
        await _mark_synced(db, source_id, 0)
        return {"upserted": 0}

    # 确保 api_source 对应的 regulation_book 存在（取第一条 book_id 或自动创建）
    book_id = await _ensure_book_for_source(db, source_id, source_name)

    upserted = 0
    for item in items[:MAX_ITEMS_PER_SYNC]:
        article_no = (item.get("article_no") or "").strip()
        content = (item.get("content") or "").strip()
        if not content:
            continue

        await db.execute(
            """
            INSERT INTO regulation_articles
                (book_id, article_no, title, content, obligation_level, is_mandatory, chapter_no)
            VALUES ($1,$2,$3,$4,$5,$6,$7)
            ON CONFLICT (book_id, article_no) DO UPDATE SET
                title=EXCLUDED.title,
                content=EXCLUDED.content,
                obligation_level=EXCLUDED.obligation_level,
                is_mandatory=EXCLUDED.is_mandatory,
                updated_at=now()
            """,
            book_id,
            article_no or f"auto_{upserted}",
            (item.get("title") or "")[:200],
            content,
            _normalize_obligation(item.get("obligation_level")),
            bool(item.get("is_mandatory", False)),
            (item.get("chapter_no") or "")[:50],
        )
        upserted += 1

    await _mark_synced(db, source_id, upserted)
    await write_audit(
        db,
        user_id=None,
        action="regulation_api_sync_complete",
        resource="regulation_api_source",
        resource_id=source_id,
        new_state={"upserted": upserted, "source_name": source_name},
        ip_address="celery-beat",
    )
    logger.info("数据源「%s」同步完成，写入/更新 %d 条条文", source_name, upserted)

    # 异步向量化新增条文（fire-and-forget，不影响同步结果）
    try:
        from tasks.regulation_import import import_regulation_file_task  # noqa: F401
        _trigger_batch_vectorize.apply_async(kwargs={"book_id": book_id})
    except Exception:
        pass

    return {"upserted": upserted}


# ── HTTP 请求 ─────────────────────────────────────────────────────

async def _fetch_remote_articles(
    endpoint_url: str,
    auth_type: str,
    auth_config: dict,
) -> list[dict]:
    headers: dict[str, str] = {"Accept": "application/json"}
    auth: Optional[tuple] = None

    if auth_type == "api_key":
        api_key = auth_config.get("api_key", "")
        header_name = auth_config.get("header", "Authorization")
        prefix = auth_config.get("prefix", "Bearer")
        headers[header_name] = f"{prefix} {api_key}".strip()
    elif auth_type == "basic":
        auth = (auth_config.get("username", ""), auth_config.get("password", ""))

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SEC) as client:
        kwargs: dict = {"headers": headers}
        if auth:
            kwargs["auth"] = auth
        resp = await client.get(endpoint_url, **kwargs)
        resp.raise_for_status()
        data = resp.json()

    # 支持 auth_config.response_path 自定义取值路径（"items" 或 "data.articles" 等）
    response_path = auth_config.get("response_path", "items")
    result = data
    for key in response_path.split("."):
        if isinstance(result, dict):
            result = result.get(key, [])
        else:
            break
    return result if isinstance(result, list) else []


# ── DB 辅助 ───────────────────────────────────────────────────────

async def _ensure_book_for_source(db: databases.Database, source_id: str, source_name: str) -> str:
    row = await db.fetch_one(
        "SELECT id FROM regulation_books WHERE api_source_id=$1 LIMIT 1",
        source_id,
    )
    if row:
        return str(row["id"])
    new_id = await db.fetch_one(
        """
        INSERT INTO regulation_books (title, discipline, status, api_source_id)
        VALUES ($1, 'general', 'active', $2)
        RETURNING id
        """,
        f"[API同步] {source_name}", source_id,
    )
    return str(new_id["id"])


async def _mark_synced(db: databases.Database, source_id: str, count: int) -> None:
    await db.execute(
        "UPDATE regulation_api_sources SET last_synced_at=now(), last_sync_count=$1, "
        "last_sync_error=NULL, updated_at=now() WHERE id=$2",
        count, source_id,
    )


def _normalize_obligation(raw: Optional[str]) -> str:
    mapping = {"MUST": "MUST", "SHOULD": "SHOULD", "MAY": "MAY", "MUST_NOT": "MUST_NOT"}
    return mapping.get((raw or "").upper(), "SHOULD")


# ── 批量向量化（异步触发）─────────────────────────────────────────

@celery_app.task(name="tasks.regulation_api_sync.trigger_batch_vectorize")
def _trigger_batch_vectorize(book_id: str) -> dict:
    return asyncio.run(_do_batch_vectorize(book_id))


async def _do_batch_vectorize(book_id: str) -> dict:
    db = databases.Database(settings.database_url)
    await db.connect()
    try:
        rows = await db.fetch_all(
            "SELECT id FROM regulation_articles WHERE book_id=$1 AND embedding IS NULL LIMIT 200",
            book_id,
        )
        ids = [str(r["id"]) for r in rows]
        if ids:
            from services.regulation_importer import vectorize_articles
            await vectorize_articles(db, ids)
        return {"vectorized": len(ids)}
    finally:
        await db.disconnect()
