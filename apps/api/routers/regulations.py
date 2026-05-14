"""
规范知识库 API

三种输入途径：
  1. 手动录入：POST /books/{id}/articles
  2. 文件导入：POST /books/{id}/import  → Celery 异步
  3. 外部 API 接入：CRUD /api-sources

CRUD：规范文件（books）、条文（articles）、外部 API 配置（api-sources）
权限：管理员可写；其他角色只读（规范搜索 + 问答）
"""
import uuid
import mimetypes
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Query
from pydantic import BaseModel

from core.config import settings
from core.storage import upload_file, presigned_get_url
from dependencies import get_db, get_current_user, require_admin
from services.audit import write_audit
from services.regulation_importer import extract_text, infer_book_metadata
from tasks.regulation_import import import_regulation_file_task

router = APIRouter(prefix="/regulations", tags=["regulations"])

ALLOWED_IMPORT_TYPES = {"pdf", "docx", "doc"}
AUTO_CREATE_TYPES = {"pdf"}


def _parse_date(value: str | date | None) -> date | None:
    if not value or isinstance(value, date):
        return value
    return date.fromisoformat(value)


# ── 规范文件（Books） ─────────────────────────────────────────

class BookCreate(BaseModel):
    title: str
    std_no: Optional[str] = None
    version: Optional[str] = None
    discipline: Optional[str] = None
    publisher: Optional[str] = None
    effective_at: Optional[str] = None


class BookUpdate(BaseModel):
    title: Optional[str] = None
    std_no: Optional[str] = None
    version: Optional[str] = None
    discipline: Optional[str] = None
    publisher: Optional[str] = None
    effective_at: Optional[str] = None
    status: Optional[str] = None


@router.get("/books")
async def list_books(
    discipline: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db=Depends(get_db),
    _=Depends(get_current_user),
):
    conds, args = [], []
    i = 1
    if discipline:
        conds.append(f"discipline=${i}"); args.append(discipline); i += 1
    if status:
        conds.append(f"status=${i}"); args.append(status); i += 1

    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    rows = await db.fetch_all(
        f"""
        SELECT b.id, b.title, b.std_no, b.version, b.discipline, b.status,
               b.source_type, b.effective_at, b.created_at, b.updated_at,
               COUNT(a.id)::int AS article_count
        FROM regulation_books b
        LEFT JOIN regulation_articles a ON a.book_id = b.id
        {where}
        GROUP BY b.id
        ORDER BY b.updated_at DESC
        LIMIT ${i} OFFSET ${i+1}
        """,
        *args, limit, offset,
    )
    total = await db.fetch_val(f"SELECT COUNT(*) FROM regulation_books {where}", *args)
    return {"items": [dict(r) for r in rows], "total": total}


@router.post("/books", status_code=201)
async def create_book(
    body: BookCreate,
    db=Depends(get_db),
    current_user: dict = Depends(require_admin),
):
    row = await db.fetch_one(
        """
        INSERT INTO regulation_books
            (title, std_no, version, discipline, publisher, effective_at,
             status, source_type, created_by)
        VALUES ($1,$2,$3,$4,$5,$6,'draft','manual',$7)
        RETURNING id
        """,
        body.title, body.std_no, body.version, body.discipline,
        body.publisher, body.effective_at, current_user["id"],
    )
    return {"id": str(row["id"])}


@router.post("/books/import", status_code=201)
async def create_book_from_pdf(
    file: UploadFile = File(...),
    db=Depends(get_db),
    current_user: dict = Depends(require_admin),
):
    """上传 PDF，自动识别规范元数据并创建规范文件，再触发条文导入。"""
    ext = (file.filename or "").rsplit(".", 1)[-1].lower()
    if ext not in AUTO_CREATE_TYPES:
        raise HTTPException(400, "自动建档仅支持 PDF 文件")

    content = await file.read()
    if len(content) > 50 * 1024 * 1024:
        raise HTTPException(413, "文件超过 50MB 限制")

    try:
        text = extract_text(content, file.filename or "regulation.pdf")
        metadata = infer_book_metadata(text, file.filename or "regulation.pdf")
    except Exception:
        metadata = {
            "title": (file.filename or "未命名规范").rsplit(".", 1)[0],
            "std_no": None,
            "version": None,
            "discipline": "general",
            "publisher": None,
            "effective_at": None,
        }

    std_no = metadata.get("std_no")
    if std_no:
        existing_book_id = await db.fetch_val(
            "SELECT id FROM regulation_books WHERE std_no=$1",
            std_no,
        )
        if existing_book_id:
            raise HTTPException(409, f"规范编号 {std_no} 已存在，请在现有规范中导入文件")

    row = await db.fetch_one(
        """
        INSERT INTO regulation_books
            (title, std_no, version, discipline, publisher, effective_at,
             status, source_type, created_by)
        VALUES ($1,$2,$3,$4,$5,$6,'processing','file_import',$7)
        RETURNING id
        """,
        metadata["title"],
        metadata.get("std_no"),
        metadata.get("version"),
        metadata.get("discipline") or "general",
        metadata.get("publisher"),
        _parse_date(metadata.get("effective_at")),
        current_user["id"],
    )
    book_id = str(row["id"])

    content_type = (
        file.content_type
        or mimetypes.guess_type(file.filename or "")[0]
        or "application/pdf"
    )
    file_key = f"regulations/{book_id}/{uuid.uuid4()}.{ext}"
    upload_file(content, file_key, content_type)
    await db.execute(
        "UPDATE regulation_books SET file_key=$1, updated_at=now() WHERE id=$2",
        file_key, book_id,
    )
    import_regulation_file_task.delay(book_id, file_key, file.filename or f"file.{ext}")

    await write_audit(
        db, user_id=current_user["id"],
        action="regulation_pdf_uploaded", resource="regulation_book",
        resource_id=book_id, new_state={"file_key": file_key, "metadata": metadata},
    )
    return {"id": book_id, "file_key": file_key, "status": "processing", "metadata": metadata}


@router.patch("/books/{book_id}")
async def update_book(
    book_id: str,
    body: BookUpdate,
    db=Depends(get_db),
    current_user: dict = Depends(require_admin),
):
    updates, args = [], []
    i = 1
    for field in ("title", "std_no", "version", "discipline", "publisher", "effective_at", "status"):
        val = getattr(body, field, None)
        if val is not None:
            updates.append(f"{field}=${i}"); args.append(val); i += 1
    if not updates:
        raise HTTPException(400, "无更新字段")
    updates.append(f"updated_at=now()")
    await db.execute(
        f"UPDATE regulation_books SET {', '.join(updates)} WHERE id=${i}",
        *args, book_id,
    )
    return {"ok": True}


@router.delete("/books/{book_id}")
async def delete_book(
    book_id: str,
    db=Depends(get_db),
    _: dict = Depends(require_admin),
):
    await db.execute("DELETE FROM regulation_books WHERE id=$1", book_id)
    return {"ok": True}


@router.post("/books/{book_id}/publish")
async def publish_book(
    book_id: str,
    db=Depends(get_db),
    current_user: dict = Depends(require_admin),
):
    """将规范文件状态设为 active（发布上线）"""
    book = await db.fetch_one("SELECT id, status FROM regulation_books WHERE id=$1", book_id)
    if not book:
        raise HTTPException(404, "规范文件不存在")
    if book["status"] not in ("draft", "import_failed"):
        raise HTTPException(409, f"当前状态 {book['status']} 不可发布")

    count = await db.fetch_val(
        "SELECT COUNT(*) FROM regulation_articles WHERE book_id=$1", book_id
    )
    if count == 0:
        raise HTTPException(422, "该规范文件尚无条文，请先导入内容")

    await db.execute(
        "UPDATE regulation_books SET status='active', updated_at=now() WHERE id=$1", book_id
    )
    await write_audit(
        db, user_id=current_user["id"],
        action="publish_regulation_book", resource="regulation_book",
        resource_id=book_id, new_state={"status": "active"},
    )
    return {"ok": True}


@router.post("/books/{book_id}/unpublish")
async def unpublish_book(
    book_id: str,
    db=Depends(get_db),
    _: dict = Depends(require_admin),
):
    await db.execute(
        "UPDATE regulation_books SET status='draft', updated_at=now() WHERE id=$1", book_id
    )
    return {"ok": True}


# ── 文件导入（途径2）────────────────────────────────────────

@router.post("/books/{book_id}/import")
async def import_file(
    book_id: str,
    file: UploadFile = File(...),
    db=Depends(get_db),
    current_user: dict = Depends(require_admin),
):
    """上传 PDF/Word/Excel 到 MinIO，触发异步 NLP 提取流水线。"""
    book = await db.fetch_one("SELECT id FROM regulation_books WHERE id=$1", book_id)
    if not book:
        raise HTTPException(404, "规范文件不存在")

    ext = (file.filename or "").rsplit(".", 1)[-1].lower()
    if ext not in ALLOWED_IMPORT_TYPES:
        raise HTTPException(400, f"不支持的文件格式 .{ext}，支持：{ALLOWED_IMPORT_TYPES}")

    content = await file.read()
    if len(content) > 50 * 1024 * 1024:
        raise HTTPException(413, "文件超过 50MB 限制")

    content_type = (
        file.content_type
        or mimetypes.guess_type(file.filename or "")[0]
        or "application/octet-stream"
    )
    file_key = f"regulations/{book_id}/{uuid.uuid4()}.{ext}"
    upload_file(content, file_key, content_type)

    # 更新文件 key，触发异步任务
    await db.execute(
        "UPDATE regulation_books SET file_key=$1, status='processing', updated_at=now() WHERE id=$2",
        file_key, book_id,
    )
    import_regulation_file_task.delay(book_id, file_key, file.filename or f"file.{ext}")

    await write_audit(
        db, user_id=current_user["id"],
        action="regulation_import_started", resource="regulation_book",
        resource_id=book_id, new_state={"file_key": file_key, "filename": file.filename},
    )
    return {"ok": True, "file_key": file_key, "status": "processing"}


# ── 条文（Articles）CRUD ──────────────────────────────────────

class ArticleCreate(BaseModel):
    article_no: str
    title: Optional[str] = None
    content: str
    obligation_level: str = "SHOULD"
    is_mandatory: bool = False
    conditions: list = []


class ArticleUpdate(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    obligation_level: Optional[str] = None
    is_mandatory: Optional[bool] = None
    conditions: Optional[list] = None


@router.get("/books/{book_id}/articles")
async def list_articles(
    book_id: str,
    is_mandatory: Optional[bool] = None,
    obligation_level: Optional[str] = None,
    q: Optional[str] = Query(None, description="关键词搜索"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db=Depends(get_db),
    _=Depends(get_current_user),
):
    conds = ["book_id=$1"]
    args: list = [book_id]
    i = 2
    if is_mandatory is not None:
        conds.append(f"is_mandatory=${i}"); args.append(is_mandatory); i += 1
    if obligation_level:
        conds.append(f"obligation_level=${i}"); args.append(obligation_level); i += 1
    if q:
        conds.append(f"(article_no ILIKE ${i} OR content ILIKE ${i} OR title ILIKE ${i})")
        args.append(f"%{q}%"); i += 1

    where = " AND ".join(conds)
    rows = await db.fetch_all(
        f"""
        SELECT id, article_no, title,
               LEFT(content, 200) AS content_preview,
               obligation_level, is_mandatory, vector_id, created_at
        FROM regulation_articles
        WHERE {where}
        ORDER BY article_no
        LIMIT ${i} OFFSET ${i+1}
        """,
        *args, limit, offset,
    )
    total = await db.fetch_val(
        f"SELECT COUNT(*) FROM regulation_articles WHERE {where}", *args
    )
    return {"items": [dict(r) for r in rows], "total": total}


@router.get("/books/{book_id}/articles/{article_id}")
async def get_article(
    book_id: str,
    article_id: str,
    db=Depends(get_db),
    _=Depends(get_current_user),
):
    row = await db.fetch_one(
        "SELECT * FROM regulation_articles WHERE id=$1 AND book_id=$2",
        article_id, book_id,
    )
    if not row:
        raise HTTPException(404, "条文不存在")
    return dict(row)


@router.post("/books/{book_id}/articles", status_code=201)
async def create_article(
    book_id: str,
    body: ArticleCreate,
    db=Depends(get_db),
    current_user: dict = Depends(require_admin),
):
    """手动录入单条条文（途径1），自动触发向量化。"""
    import json as _json
    book = await db.fetch_one("SELECT id FROM regulation_books WHERE id=$1", book_id)
    if not book:
        raise HTTPException(404, "规范文件不存在")

    existing = await db.fetch_val(
        "SELECT id FROM regulation_articles WHERE book_id=$1 AND article_no=$2",
        book_id, body.article_no,
    )
    if existing:
        raise HTTPException(409, f"条文编号 {body.article_no} 已存在")

    row = await db.fetch_one(
        """
        INSERT INTO regulation_articles
            (book_id, article_no, title, content, obligation_level, is_mandatory, conditions)
        VALUES ($1,$2,$3,$4,$5,$6,$7)
        RETURNING id
        """,
        book_id, body.article_no, body.title, body.content,
        body.obligation_level, body.is_mandatory,
        _json.dumps(body.conditions, ensure_ascii=False),
    )
    article_id = str(row["id"])

    # 异步向量化（不阻塞响应）
    _trigger_vectorize.delay(article_id)

    return {"id": article_id}


@router.patch("/books/{book_id}/articles/{article_id}")
async def update_article(
    book_id: str,
    article_id: str,
    body: ArticleUpdate,
    db=Depends(get_db),
    _: dict = Depends(require_admin),
):
    import json as _json
    updates, args = [], []
    i = 1
    if body.title is not None:
        updates.append(f"title=${i}"); args.append(body.title); i += 1
    if body.content is not None:
        updates.append(f"content=${i}"); args.append(body.content); i += 1
    if body.obligation_level is not None:
        updates.append(f"obligation_level=${i}"); args.append(body.obligation_level); i += 1
    if body.is_mandatory is not None:
        updates.append(f"is_mandatory=${i}"); args.append(body.is_mandatory); i += 1
    if body.conditions is not None:
        updates.append(f"conditions=${i}")
        args.append(_json.dumps(body.conditions, ensure_ascii=False)); i += 1
    if not updates:
        raise HTTPException(400, "无更新字段")

    await db.execute(
        f"UPDATE regulation_articles SET {', '.join(updates)} WHERE id=${i} AND book_id=${i+1}",
        *args, article_id, book_id,
    )
    return {"ok": True}


@router.delete("/books/{book_id}/articles/{article_id}")
async def delete_article(
    book_id: str,
    article_id: str,
    db=Depends(get_db),
    _: dict = Depends(require_admin),
):
    await db.execute(
        "DELETE FROM regulation_articles WHERE id=$1 AND book_id=$2",
        article_id, book_id,
    )
    return {"ok": True}


# ── 外部 API 接入（途径3）────────────────────────────────────

class ApiSourceCreate(BaseModel):
    name: str
    endpoint_url: str
    auth_type: str = "api_key"
    auth_config: dict = {}
    sync_interval_hours: int = 24


class ApiSourceUpdate(BaseModel):
    name: Optional[str] = None
    endpoint_url: Optional[str] = None
    sync_interval_hours: Optional[int] = None
    is_active: Optional[bool] = None


@router.get("/api-sources")
async def list_api_sources(
    db=Depends(get_db),
    _: dict = Depends(require_admin),
):
    rows = await db.fetch_all(
        "SELECT id, name, endpoint_url, auth_type, sync_interval_hours, "
        "last_synced_at, is_active, created_at FROM regulation_api_sources ORDER BY created_at DESC"
    )
    return {"items": [dict(r) for r in rows]}


@router.post("/api-sources", status_code=201)
async def create_api_source(
    body: ApiSourceCreate,
    db=Depends(get_db),
    current_user: dict = Depends(require_admin),
):
    import json as _json
    row = await db.fetch_one(
        """
        INSERT INTO regulation_api_sources
            (name, endpoint_url, auth_type, auth_config, sync_interval_hours, created_by)
        VALUES ($1,$2,$3,$4,$5,$6)
        RETURNING id
        """,
        body.name, body.endpoint_url, body.auth_type,
        _json.dumps(body.auth_config), body.sync_interval_hours, current_user["id"],
    )
    return {"id": str(row["id"])}


@router.patch("/api-sources/{source_id}")
async def update_api_source(
    source_id: str,
    body: ApiSourceUpdate,
    db=Depends(get_db),
    _: dict = Depends(require_admin),
):
    updates, args = [], []
    i = 1
    for field in ("name", "endpoint_url", "sync_interval_hours", "is_active"):
        val = getattr(body, field, None)
        if val is not None:
            updates.append(f"{field}=${i}"); args.append(val); i += 1
    if not updates:
        raise HTTPException(400, "无更新字段")
    await db.execute(
        f"UPDATE regulation_api_sources SET {', '.join(updates)} WHERE id=${i}",
        *args, source_id,
    )
    return {"ok": True}


@router.delete("/api-sources/{source_id}")
async def delete_api_source(
    source_id: str,
    db=Depends(get_db),
    _: dict = Depends(require_admin),
):
    await db.execute("DELETE FROM regulation_api_sources WHERE id=$1", source_id)
    return {"ok": True}


# ── 规范搜索 + 问答（只读，所有角色可用）────────────────────

@router.get("/search")
async def search_regulations(
    q: str = Query(..., min_length=2),
    discipline: Optional[str] = None,
    limit: int = Query(20, ge=1, le=50),
    db=Depends(get_db),
    _=Depends(get_current_user),
):
    """全文关键词搜索规范条文。"""
    conds = ["(a.content ILIKE $1 OR a.article_no ILIKE $1 OR a.title ILIKE $1)"]
    args: list = [f"%{q}%"]
    i = 2
    if discipline:
        conds.append(f"b.discipline=${i}"); args.append(discipline); i += 1

    where = " AND ".join(conds)
    rows = await db.fetch_all(
        f"""
        SELECT a.id, a.article_no, a.title,
               LEFT(a.content, 300) AS content_preview,
               a.obligation_level, a.is_mandatory,
               b.title AS book_title, b.std_no
        FROM regulation_articles a
        JOIN regulation_books b ON a.book_id = b.id
        WHERE b.status = 'active' AND {where}
        ORDER BY a.is_mandatory DESC, a.obligation_level
        LIMIT ${i}
        """,
        *args, limit,
    )
    return {"items": [dict(r) for r in rows], "query": q}


# ── Celery 单条向量化任务（内部使用）────────────────────────

from core.celery_app import celery_app
import asyncio as _asyncio


@celery_app.task(name="tasks.regulation_import.vectorize_article")
def _trigger_vectorize(article_id: str) -> None:
    _asyncio.run(_do_vectorize(article_id))


async def _do_vectorize(article_id: str) -> None:
    import databases
    from services.regulation_importer import vectorize_articles
    db = databases.Database(settings.database_url)
    await db.connect()
    try:
        await vectorize_articles(db, [article_id])
    finally:
        await db.disconnect()


# ── 手动触发 API 数据源同步 ─────────────────────────────────────

@router.post("/api-sources/{source_id}/sync", status_code=202)
async def trigger_api_source_sync(
    source_id: str,
    db=Depends(get_db),
    _: dict = Depends(require_admin),
):
    """手动触发单个数据源立即同步（异步，返回任务 ID）"""
    from tasks.regulation_api_sync import sync_single_source_task
    source = await db.fetch_one(
        "SELECT id FROM regulation_api_sources WHERE id=$1", source_id
    )
    if not source:
        raise HTTPException(404, "数据源不存在")
    task = sync_single_source_task.apply_async(kwargs={"source_id": source_id})
    return {"task_id": task.id, "status": "queued"}
