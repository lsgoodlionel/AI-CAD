"""工程信息抽取 Celery 任务(Phase E1-1)。

extract_project_drawing_info(project_id)：
- 遍历项目全部图纸,逐图:MinIO 拉字节 → drawing_info_extractor 抽取 → 覆盖式落库
- 单图失败记日志跳过,不拖垮批量(与建模管线纪律一致)
- 代次:全项目重抽时统一 extraction_version = 上一代 max + 1

extract_single_drawing_info(drawing_id)：单图重抽(上传/换版后增量触发)。
"""
import asyncio
import logging
import os

import databases

from core.celery_app import celery_app
from core.config import settings

logger = logging.getLogger(__name__)


def _task_db() -> databases.Database:
    """单图任务专用小池连接。

    asyncpg 池默认 min_size=10——扇出场景 8 并发 worker × 10 = 80+ 连接,
    会打爆 PG max_connections(实测 too many clients)。单图任务串行查写,
    1~2 条连接绰绰有余。
    """
    return databases.Database(settings.database_url, min_size=1, max_size=2)

_SELECT_PROJECT_DRAWINGS = """
SELECT id, project_id, file_key, title
FROM drawings
WHERE project_id = :project_id AND file_key IS NOT NULL
ORDER BY created_at
"""

_SELECT_ONE_DRAWING = """
SELECT id, project_id, file_key, title
FROM drawings
WHERE id = :drawing_id AND file_key IS NOT NULL
"""

_SELECT_NEXT_VERSION = """
SELECT COALESCE(MAX(extraction_version), 0) + 1 AS v
FROM drawing_extracted_info
WHERE project_id = :project_id
"""


def _file_ext(file_key: str, title: str | None) -> str:
    ext = os.path.splitext(file_key)[1].lstrip(".").lower()
    if ext:
        return ext
    return os.path.splitext(title or "")[1].lstrip(".").lower()


_UPSERT_STATUS_SQL = """
INSERT INTO drawing_archive_status
    (drawing_id, project_id, status, extraction_version, item_count,
     extractors_done, summary, started_at, updated_at)
VALUES (:drawing_id, :project_id, :status, :version, :item_count,
     CAST(:extractors_done AS jsonb), CAST(:summary AS jsonb),
     COALESCE(:started_at, now()), now())
ON CONFLICT (drawing_id) DO UPDATE
SET status = EXCLUDED.status,
    extraction_version = EXCLUDED.extraction_version,
    item_count = EXCLUDED.item_count,
    extractors_done = COALESCE(EXCLUDED.extractors_done, drawing_archive_status.extractors_done),
    summary = COALESCE(EXCLUDED.summary, drawing_archive_status.summary),
    started_at = CASE WHEN EXCLUDED.status = 'extracting'
                      THEN now() ELSE drawing_archive_status.started_at END,
    updated_at = now()
"""


async def _set_status(db, drawing_id: str, project_id: str, status: str,
                      version: int = 0, item_count: int = 0,
                      extractors_done: list | None = None,
                      summary: dict | None = None) -> None:
    """更新单图档案状态机(失败不阻断抽取);extracting 时刷新 started_at。"""
    import json
    try:
        await db.execute(_UPSERT_STATUS_SQL, {
            "drawing_id": drawing_id, "project_id": project_id,
            "status": status, "version": version, "item_count": item_count,
            "extractors_done": json.dumps(extractors_done) if extractors_done is not None else None,
            "summary": json.dumps(summary, ensure_ascii=False) if summary is not None else None,
            "started_at": None,
        })
    except Exception as exc:  # noqa: BLE001
        logger.debug("[drawing_info] 状态更新跳过 %s: %s", drawing_id, exc)


_VLM_PROVIDER_SQL = (
    "SELECT base_url FROM llm_providers WHERE name = :name AND is_active = true LIMIT 1"
)


async def _resolve_vlm_base_url(db) -> str | None:
    """用任务自己的 db 连接查远程 VLM 端点(celery worker 无 app 共享连接)。"""
    try:
        row = await db.fetch_one(_VLM_PROVIDER_SQL, {"name": "Ollama 远程"})
        return (row["base_url"] if row else None) or None
    except Exception:  # noqa: BLE001
        return None


async def _run_vlm(file_bytes: bytes, ext: str, base_url: str | None) -> tuple[list, str]:
    """渲染位图 → 远程 VLM 读图 → (档案条目, backend);失败降级 ([], 'none')。

    base_url 由调用方用任务 db 查出显式传入(worker 无 app 共享 DB 连接,
    read_drawing_vlm 内部的 DB 端点解析在 worker 里会失败)。
    """
    if not base_url:
        return [], "none"
    try:
        from services.drawing_info_extractor import items_from_vlm

        img_bytes = file_bytes
        if ext == "pdf":
            import fitz
            doc = fitz.open(stream=file_bytes, filetype="pdf")
            try:
                page = doc[0]
                z = min(1024 / page.rect.width, 1024 / page.rect.height, 2.0)
                img_bytes = page.get_pixmap(matrix=fitz.Matrix(z, z), alpha=False).tobytes("png")
            finally:
                doc.close()
        from core.model3d.vlm_read import read_drawing_vlm
        result = await read_drawing_vlm(img_bytes, base_url=base_url)
        return items_from_vlm(result), getattr(result, "backend", "none")
    except Exception as exc:  # noqa: BLE001 — VLM 失败不阻断扫描
        logger.warning("[drawing_info] VLM 读图跳过: %s", exc)
        return [], "none"


async def _extract_one(db, row: dict, version: int, with_vlm: bool = False) -> int:
    """抽取并落库单图(矢量/OCR/文件名 + 可选 VLM),写扫描摘要,返回写入条数。"""
    from core.storage import get_file_bytes
    from services.drawing_info_extractor import (
        build_scan_summary,
        extract_drawing_info,
        persist_drawing_info,
    )

    drawing_id = str(row["id"])
    project_id = str(row["project_id"])
    await _set_status(db, drawing_id, project_id, "extracting", version)

    file_bytes = get_file_bytes(row["file_key"])
    ext = _file_ext(row["file_key"], row.get("title"))
    items, transform = extract_drawing_info(
        file_bytes, ext, filename=row.get("title"), with_transform=True
    )
    extractors = ["vector_text", "ocr", "filename"]

    # F1：VLM 读图(判专业/标高/构件候选)——全量扫描时启用,慢但独立降级
    vlm_backend = "skipped"
    if with_vlm and ext in ("pdf", "png", "jpg", "jpeg"):
        vlm_base_url = await _resolve_vlm_base_url(db)
        vlm_items, vlm_backend = await _run_vlm(file_bytes, ext, vlm_base_url)
        items = items + vlm_items
        if vlm_backend not in ("none", "skipped"):
            extractors.append("vlm")

    written = await persist_drawing_info(
        db, project_id=project_id, drawing_id=drawing_id, items=items, version=version,
    )
    if transform is not None:
        try:
            from services.drawing_transform import persist_transform
            await persist_transform(
                db, project_id=project_id, drawing_id=drawing_id, transform=transform
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("[drawing_info] 变换落库跳过 %s: %s", drawing_id, exc)

    await _apply_title_block_fields(db, drawing_id, items)

    summary = build_scan_summary(items, vlm_backend=vlm_backend)
    await _set_status(db, drawing_id, project_id, "ready", version, written,
                      extractors_done=extractors, summary=summary)
    return written


async def _apply_title_block_fields(db, drawing_id: str, items: list) -> None:
    """抽取完即读图框:专业权威覆盖,图号仅在文件名缺失时补。

    专业 —— 图框「专业」栏是设计单位填写的权威值,比文件名猜测准,直接覆盖。
    图号 —— 实测图框图号与文件名解析值只有 **86% 一致**,不一致的多是 OCR
             字符级糊字(`P-31-23`→`D-31-23`、`.01C`→`.010`)。文件名是导入时的
             确定信息,更可信,故**只在文件名没给出图号时才补**,绝不静默覆盖。
    """
    try:
        from services.title_block_discipline import (
            _bbox, coarse_discipline, discipline_from_items, drawing_no_from_items,
        )
        boxed = [(i.get("content") or "", _bbox(i.get("location_json")))
                 for i in items]
        boxed = [(c, b) for c, b in boxed if b]
        if not boxed:
            return

        label = discipline_from_items(boxed)
        if label:
            await db.execute(
                "UPDATE drawings SET discipline_label=:l, discipline=:c, "
                "updated_at=now() WHERE id=:id",
                {"l": label, "c": coarse_discipline(label) or "general",
                 "id": drawing_id})

        row = await db.fetch_one(
            "SELECT drawing_no FROM drawings WHERE id=:id", {"id": drawing_id})
        current = (row["drawing_no"] or "").strip() if row else ""
        if not current:
            no = drawing_no_from_items(boxed)
            if no:
                await db.execute(
                    "UPDATE drawings SET drawing_no=:n, updated_at=now() WHERE id=:id",
                    {"n": no, "id": drawing_id})
    except Exception as exc:  # noqa: BLE001 — 读不到图框字段不阻断抽取
        logger.debug("[drawing_info] 图框字段读取跳过 %s: %s", drawing_id, exc)


@celery_app.task(bind=True, max_retries=1, default_retry_delay=60)
def extract_project_drawing_info(self, project_id: str, with_vlm: bool = False) -> dict:
    """全项目工程信息抽取入口(扇出模式)。

    大项目(数千图 × OCR 数十秒/图)串行必撞 celery 1800s 硬超时,
    故本任务只做「查清单 + 定版本 + 逐图派发单图任务」,秒级完成;
    真正抽取由 extract_single_drawing_info 逐图独立执行(可重试、互不拖累)。
    """
    logger.info("[drawing_info] 项目级抽取启动(扇出): project_id=%s with_vlm=%s", project_id, with_vlm)
    try:
        return asyncio.run(_fanout_project(project_id, with_vlm))
    except Exception as exc:
        logger.error("[drawing_info] 项目级扇出失败: %s", exc)
        raise self.retry(exc=exc)


async def _fanout_project(project_id: str, with_vlm: bool = False) -> dict:
    db = _task_db()
    await db.connect()
    try:
        rows = [dict(r) for r in await db.fetch_all(
            _SELECT_PROJECT_DRAWINGS, {"project_id": project_id}
        )]
        version_row = await db.fetch_one(_SELECT_NEXT_VERSION, {"project_id": project_id})
        version = int(version_row["v"]) if version_row else 1
    finally:
        await db.disconnect()

    for row in rows:
        extract_single_drawing_info.delay(str(row["id"]), version, with_vlm)

    result = {"project_id": project_id, "enqueued": len(rows), "version": version, "with_vlm": with_vlm}
    logger.info("[drawing_info] 项目级扇出完成: %s", result)
    return result


@celery_app.task(bind=True, max_retries=1, default_retry_delay=30)
def extract_single_drawing_info(self, drawing_id: str, version: int | None = None, with_vlm: bool = False) -> dict:
    """单图工程信息抽取/重抽入口。

    version 由项目级扇出统一下发保证同代次;单图独立触发时为 None,
    按项目当前 max+1 自算。
    """
    try:
        return asyncio.run(_do_extract_single(drawing_id, version, with_vlm))
    except Exception as exc:
        logger.error("[drawing_info] 单图抽取失败: drawing_id=%s %s", drawing_id, exc)
        raise self.retry(exc=exc)


async def _do_extract_single(drawing_id: str, version: int | None = None, with_vlm: bool = False) -> dict:
    db = _task_db()
    await db.connect()
    try:
        row = await db.fetch_one(_SELECT_ONE_DRAWING, {"drawing_id": drawing_id})
        if row is None:
            return {"drawing_id": drawing_id, "items": 0, "skipped": "not_found"}
        row = dict(row)
        if version is None:
            version_row = await db.fetch_one(
                _SELECT_NEXT_VERSION, {"project_id": str(row["project_id"])}
            )
            version = int(version_row["v"]) if version_row else 1
        items = await _extract_one(db, row, version, with_vlm)
        return {"drawing_id": drawing_id, "items": items, "version": version}
    finally:
        await db.disconnect()


# ── 僵死状态回收(beat 定时)────────────────────────────────────────

_SELECT_STALE_STATUS = """
SELECT drawing_id, project_id, status, started_at, summary
FROM drawing_archive_status
WHERE status = :status
"""

_RESET_STATUS_SQL = """
UPDATE drawing_archive_status
SET status = :next_status,
    summary = COALESCE(summary, '{}'::jsonb) || CAST(:patch AS jsonb),
    updated_at = now()
WHERE drawing_id = :drawing_id
"""


@celery_app.task(bind=True, max_retries=1, default_retry_delay=300)
def reap_stale_archive_status(self) -> dict:
    """回收长时间卡在 extracting 的档案状态,并有限次重派抽取。

    抽取任务置位 extracting 后若进程被杀,状态会永久停在 extracting——
    既不会被重抽,也不出现在失败列表里(静默丢失)。实测有真实图纸卡了 13 天。
    判据与次数上限见 `services.drawing_archive_reaper`。
    """
    try:
        return asyncio.run(_reap_stale())
    except Exception as exc:  # noqa: BLE001
        logger.error("[drawing_info] 僵死状态回收失败: %s", exc)
        raise self.retry(exc=exc)


async def _reap_stale() -> dict:
    import json

    from services.drawing_archive_reaper import (
        ACTIVE_STATUS, attempts_summary, plan_reap,
    )

    db = _task_db()
    await db.connect()
    try:
        rows = [dict(r) for r in await db.fetch_all(
            _SELECT_STALE_STATUS, {"status": ACTIVE_STATUS})]
        plan = plan_reap(rows)
        for item in plan:
            await db.execute(_RESET_STATUS_SQL, {
                "drawing_id": item["drawing_id"],
                "next_status": item["next_status"],
                "patch": json.dumps(attempts_summary(item["reap_attempts"])),
            })
    finally:
        await db.disconnect()

    requeued = [i for i in plan if i["requeue"]]
    for item in requeued:
        extract_single_drawing_info.delay(str(item["drawing_id"]))

    result = {"scanned": len(rows), "reaped": len(plan), "requeued": len(requeued)}
    if plan:
        logger.warning("[drawing_info] 回收僵死 extracting: %s", result)
    return result
