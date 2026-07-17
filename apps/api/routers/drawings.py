"""
图纸管理 API
- 上传：MinIO 存储 + 元数据入库 + 自动触发 AI 审图（Celery）
- 列表 / 详情 / 状态查询
- 下载签名 URL（5 分钟有效）
- AI 审查报告：问题列表、批注 PDF、Excel 清单
"""
import io
import logging
import uuid
import mimetypes
import json
import zipfile
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Request, Query
from fastapi import status as http_status
from fastapi.responses import Response
from pydantic import BaseModel

from core.config import settings
from core.storage import upload_file, presigned_get_url, get_file_bytes, object_exists
from core.workflow.drawing_state_machine import assert_valid_transition
from dependencies import get_db, get_current_user
from services.audit import write_audit
from services.ai_report_generator import generate_annotated_pdf, generate_excel_report
from services.ai_review_progress import estimate_total_seconds, normalize_report_progress, progress_payload
from services.drawing_filename_parser import parse_drawing_filename
from tasks.ai_review import run_ai_review

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/drawings", tags=["drawings"])

ALLOWED_TYPES = {
    "application/pdf",
    "image/vnd.dxf",
    "application/acad",
    "application/octet-stream",   # .dwg / .dxf 有时被识别为 octet-stream
    "application/x-ifc",
    "model/ifc",
}

MAX_FILE_SIZE = 200 * 1024 * 1024   # 单文件 200 MB
MAX_ZIP_SIZE = 500 * 1024 * 1024    # ZIP 整包 500 MB
ALLOWED_EXTENSIONS = {"pdf", "dwg", "dxf", "ifc"}   # 批量上传扩展名白名单
ESCALATION_IMPACT_THRESHOLD = 500_000               # 重大变更预警金额（元）


# ── 上传核心步骤（单张 / 批量共用）────────────────────────────

async def _activate_ai_review(db, drawing_id: str, report_id, file_size_kb: int) -> None:
    """图纸状态置 ai_reviewing，并写入报告初始进度（与单张上传路径一致）"""
    await db.execute(
        "UPDATE drawings SET status='ai_reviewing', updated_at=now() WHERE id=$1",
        drawing_id,
    )
    initial_progress = progress_payload(
        status="processing",
        stage_key="queued",
        started_at=datetime.now(timezone.utc),
        completed_keys=[],
        active_keys=["queued"],
        estimated_total_seconds=estimate_total_seconds(file_size_kb),
    )
    await db.execute(
        """
        UPDATE ai_review_reports
        SET status='processing',
            engine_results=jsonb_build_object('progress', CAST($2 AS jsonb))
        WHERE id=$1
        """,
        report_id,
        json.dumps(initial_progress, ensure_ascii=False),
    )


async def _persist_drawing(
    db, current_user: dict, *,
    project_id: str, filename: str, content: bytes,
    drawing_no: str, discipline: str, version: str, title: str,
    work_zone_id: str | None, estimated_impact: float | None,
    content_type: str | None,
) -> tuple[str, str]:
    """MinIO 上传 + drawings 落库，返回 (drawing_id, object_key)"""
    ext = (filename or "").rsplit(".", 1)[-1].lower()
    object_key = f"projects/{project_id}/drawings/{uuid.uuid4()}.{ext}"
    resolved_type = content_type or mimetypes.guess_type(filename or "")[0] or "application/octet-stream"
    upload_file(content, object_key, resolved_type)

    row = await db.fetch_one(
        """
        INSERT INTO drawings
            (project_id, work_zone_id, drawing_no, title, discipline, version,
             status, file_key, file_size_kb, estimated_impact, created_by)
        VALUES ($1,$2,$3,$4,$5,$6,'draft',$7,$8,$9,$10)
        RETURNING id
        """,
        project_id, work_zone_id, drawing_no, title, discipline, version,
        object_key, len(content) // 1024, estimated_impact,
        current_user["id"],
    )
    return str(row["id"]), object_key


async def _create_drawing_record(
    db, current_user: dict, request: Request, *,
    project_id: str, filename: str, content: bytes,
    drawing_no: str, discipline: str, version: str = "A", title: str = "",
    work_zone_id: str | None = None, estimated_impact: float | None = None,
    content_type: str | None = None, auto_review: bool = True,
) -> dict:
    """单张图纸落库核心步骤（单张上传与批量上传共用）：
    MinIO 上传 → drawings 落库 → 报告占位 → 审计日志 → auto_review 时触发 Celery 审图
    """
    drawing_id, object_key = await _persist_drawing(
        db, current_user,
        project_id=project_id, filename=filename, content=content,
        drawing_no=drawing_no, discipline=discipline, version=version, title=title,
        work_zone_id=work_zone_id, estimated_impact=estimated_impact,
        content_type=content_type,
    )
    # 创建 AI 审查报告占位记录
    report = await db.fetch_one(
        "INSERT INTO ai_review_reports (drawing_id, status) VALUES ($1,'pending') RETURNING id",
        drawing_id,
    )
    status = "draft"
    if auto_review:
        await _activate_ai_review(db, drawing_id, report["id"], len(content) // 1024)
        status = "ai_reviewing"

    await write_audit(
        db,
        user_id=current_user["id"],
        action="upload_drawing",
        resource="drawing",
        resource_id=drawing_id,
        new_state={"status": status, "drawing_no": drawing_no},
        ip_address=request.client.host if request.client else None,
    )
    if auto_review:
        run_ai_review.delay(drawing_id)

    # E1.5-2：导入即建档案——置 pending + 触发抽取(OCR/矢量/VLM 落档案层)。
    # 档案是全平台单一真相源(建模/工程信息/审图/算量都读它),故导入即抽取一次。
    try:
        await db.execute(
            """
            INSERT INTO drawing_archive_status (drawing_id, project_id, status)
            VALUES ($1, $2, 'pending')
            ON CONFLICT (drawing_id) DO UPDATE SET status='pending', updated_at=now()
            """,
            drawing_id, project_id,
        )
        from tasks.drawing_info_extract import extract_single_drawing_info
        extract_single_drawing_info.delay(drawing_id)
    except Exception as exc:  # noqa: BLE001 — 建档失败不阻断上传
        logger.warning("[drawings] 档案抽取触发失败 %s: %s", drawing_id, exc)

    # 重大变更预警（≥ 50 万，后续升级审批路径）
    if estimated_impact and estimated_impact >= ESCALATION_IMPACT_THRESHOLD:
        await db.execute(
            "UPDATE drawings SET finance_lock_status='pending_escalation' WHERE id=$1",
            drawing_id,
        )
    return {
        "drawing_id": drawing_id,
        "object_key": object_key,
        "status": status,
        "message": "图纸已上传，AI 审图任务已触发" if auto_review else "图纸已上传",
    }


# ── 上传图纸 ──────────────────────────────────────────────────

@router.post("", status_code=201)
async def upload_drawing(
    request: Request,
    project_id: str = Form(...),
    drawing_no: str = Form(...),
    discipline: str = Form(...),
    version: str = Form("A"),
    title: str = Form(""),
    work_zone_id: str | None = Form(None),
    estimated_impact: float | None = Form(None),
    file: UploadFile = File(...),
    db=Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """单张图纸上传：MinIO 存储 + 元数据入库 + 自动触发 AI 审图"""
    # 文件大小校验（粗校：Content-Length）
    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(413, f"文件超过最大限制 {MAX_FILE_SIZE // (1024*1024)}MB")

    content_type = file.content_type or mimetypes.guess_type(file.filename or "")[0] or "application/octet-stream"
    return await _create_drawing_record(
        db, current_user, request,
        project_id=project_id, filename=file.filename or "", content=content,
        drawing_no=drawing_no, discipline=discipline, version=version, title=title,
        work_zone_id=work_zone_id, estimated_impact=estimated_impact,
        content_type=content_type, auto_review=True,
    )


# ── 批量上传（蓝图 4.1）───────────────────────────────────────

def _parse_items_meta(raw: str) -> dict[str, dict]:
    """解析 items_meta JSON 字符串，按 filename 建索引；格式非法 → 400"""
    try:
        items = json.loads(raw or "[]")
        if not isinstance(items, list):
            raise ValueError("items_meta 必须是 JSON 数组")
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, f"INVALID_ITEMS_META: {exc}")
    return {
        item["filename"]: item
        for item in items
        if isinstance(item, dict) and item.get("filename")
    }


def _resolve_file_meta(filename: str, meta: dict) -> dict:
    """合并前端元数据与文件名解析结果（前端优先，缺失字段用解析器兜底）"""
    parsed = parse_drawing_filename(filename)
    return {
        "drawing_no": meta.get("drawing_no") or parsed["drawing_no"],
        "discipline": meta.get("discipline") or parsed["discipline"],
        "version": meta.get("version") or parsed["version"],
        "title": meta.get("title") or parsed["title"],
        "work_zone_id": meta.get("work_zone_id"),
    }


def _validate_batch_file(filename: str, content: bytes) -> None:
    """批量上传单文件校验：扩展名白名单 + 大小限制"""
    ext = (filename or "").rsplit(".", 1)[-1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"UNSUPPORTED_FILE_TYPE: .{ext}")
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(413, "FILE_TOO_LARGE")


@router.post("/batch", status_code=201)
async def batch_upload_drawings(
    request: Request,
    project_id: str = Form(...),
    items_meta: str = Form("[]"),
    auto_review: bool = Form(True),
    files: list[UploadFile] = File(...),
    db=Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """批量上传图纸：items_meta 按 filename 与 files 配对，
    缺失元数据用文件名解析器兜底；逐文件独立成败，一个失败不阻断其余。"""
    meta_map = _parse_items_meta(items_meta)
    created: list[dict] = []
    failed: list[dict] = []
    for upload in files:
        filename = upload.filename or ""
        try:
            content = await upload.read()
            _validate_batch_file(filename, content)
            meta = _resolve_file_meta(filename, meta_map.get(filename, {}))
            record = await _create_drawing_record(
                db, current_user, request,
                project_id=project_id, filename=filename, content=content,
                content_type=upload.content_type, auto_review=auto_review,
                **meta,
            )
            created.append({
                "drawing_id": record["drawing_id"],
                "drawing_no": meta["drawing_no"],
                "filename": filename,
            })
        except HTTPException as exc:
            failed.append({"filename": filename, "error": str(exc.detail)})
        except Exception as exc:  # noqa: BLE001 — 单文件失败不阻断其余文件
            logger.error("[BatchUpload] 文件 %s 上传失败: %s", filename, exc)
            failed.append({"filename": filename, "error": f"UPLOAD_FAILED: {exc}"})
    return {
        "created": created,
        "failed": failed,
        "review_triggered": len(created) if auto_review else 0,
    }


# ── ZIP 整套导入（蓝图 4.1）──────────────────────────────────

def _is_zip_slip(entry_name: str) -> bool:
    """检测 zip 条目路径穿越（zip-slip：绝对路径或 .. 上跳）"""
    normalized = entry_name.replace("\\", "/")
    return normalized.startswith("/") or ".." in normalized.split("/")


# ZIP 通用标志位第 11 位：文件名为 UTF-8 编码
_ZIP_UTF8_FLAG = 0x800


def _fix_zip_filename(info: zipfile.ZipInfo) -> str:
    """修复 zip 中文文件名乱码。

    Python zipfile 对未设 UTF-8 标志位的条目按 cp437 解码；国内工具打包的
    UTF-8/GBK 文件名会变成乱码。此处还原原始字节后按 utf-8 → gbk 依次尝试。
    """
    if info.flag_bits & _ZIP_UTF8_FLAG:
        return info.filename
    try:
        raw = info.filename.encode("cp437")
    except UnicodeEncodeError:
        return info.filename
    for encoding in ("utf-8", "gbk"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return info.filename


def _zip_entry_skip_reason(info: zipfile.ZipInfo, filename: str) -> str | None:
    """返回跳过原因（directory/hidden/extension）；None 表示应处理该条目"""
    parts = filename.replace("\\", "/").split("/")
    basename = parts[-1]
    if info.is_dir() or not basename:
        return "directory"
    if any(part.startswith(".") or part == "__MACOSX" for part in parts):
        return "hidden"
    ext = basename.rsplit(".", 1)[-1].lower() if "." in basename else ""
    if ext not in ALLOWED_EXTENSIONS:
        return "extension"
    return None


async def _import_zip_entries(
    archive: zipfile.ZipFile, db, current_user: dict, request: Request, *,
    project_id: str, auto_review: bool,
) -> tuple[list[dict], list[dict], list[str]]:
    """逐条目导入 zip 内图纸文件，返回 (created, failed, skipped)"""
    created: list[dict] = []
    failed: list[dict] = []
    skipped: list[str] = []
    for info in archive.infolist():
        filename = _fix_zip_filename(info)
        reason = _zip_entry_skip_reason(info, filename)
        if reason == "directory":
            continue
        if reason:
            skipped.append(filename)
            continue
        basename = filename.replace("\\", "/").split("/")[-1]
        if info.file_size > MAX_FILE_SIZE:
            failed.append({"filename": filename, "error": "FILE_TOO_LARGE"})
            continue
        try:
            meta = _resolve_file_meta(basename, {})
            record = await _create_drawing_record(
                db, current_user, request,
                project_id=project_id, filename=basename,
                content=archive.read(info), auto_review=auto_review,
                **meta,
            )
            created.append({
                "drawing_id": record["drawing_id"],
                "drawing_no": meta["drawing_no"],
                "filename": filename,
            })
        except HTTPException as exc:
            failed.append({"filename": filename, "error": str(exc.detail)})
        except Exception as exc:  # noqa: BLE001 — 单条目失败不阻断其余条目
            logger.error("[ImportZip] 条目 %s 导入失败: %s", filename, exc)
            failed.append({"filename": filename, "error": f"UPLOAD_FAILED: {exc}"})
    return created, failed, skipped


@router.post("/import-zip", status_code=201)
async def import_drawings_zip(
    request: Request,
    project_id: str = Form(...),
    auto_review: bool = Form(True),
    file: UploadFile = File(...),
    db=Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """ZIP 整套导入：解压（防 zip-slip）→ 文件名解析器生成元数据 → 逐条目独立成败"""
    content = await file.read()
    if len(content) > MAX_ZIP_SIZE:
        raise HTTPException(413, "ZIP_TOO_LARGE")
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile:
        raise HTTPException(400, "INVALID_ZIP_FILE")

    # 防 zip-slip：任一路径穿越条目 → 整包拒绝
    for info in archive.infolist():
        if _is_zip_slip(info.filename):
            raise HTTPException(400, "ZIP_SLIP_DETECTED")

    created, failed, skipped = await _import_zip_entries(
        archive, db, current_user, request,
        project_id=project_id, auto_review=auto_review,
    )
    return {
        "created": created,
        "failed": failed,
        "skipped": skipped,
        "review_triggered": len(created) if auto_review else 0,
    }


# ── 图纸列表 ──────────────────────────────────────────────────

@router.get("")
async def list_drawings(
    project_id: str | None = None,
    discipline: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
    db=Depends(get_db),
    _=Depends(get_current_user),
):
    conditions = []
    args: list = []
    idx = 1

    if project_id:
        conditions.append(f"d.project_id=${idx}")
        args.append(project_id)
        idx += 1
    if discipline:
        conditions.append(f"d.discipline=${idx}")
        args.append(discipline)
        idx += 1
    if status:
        conditions.append(f"d.status=${idx}")
        args.append(status)
        idx += 1

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    rows = await db.fetch_all(
        f"""
        SELECT d.id, d.drawing_no, d.title, d.discipline, d.version, d.status,
               d.current_stage, d.estimated_impact, d.created_at, d.updated_at,
               u.display_name AS creator_name,
               p.id AS project_id,
               p.name AS project_name
        FROM drawings d
        JOIN users u ON d.created_by = u.id
        JOIN projects p ON d.project_id = p.id
        {where}
        ORDER BY d.updated_at DESC
        LIMIT ${idx} OFFSET ${idx+1}
        """,
        *args, limit, offset,
    )
    total = await db.fetch_val(
        f"SELECT COUNT(*) FROM drawings d {where}", *args
    )
    return {"items": [dict(r) for r in rows], "total": total, "limit": limit, "offset": offset}


# ── 图纸详情 ──────────────────────────────────────────────────

@router.get("/{drawing_id}")
async def get_drawing(
    drawing_id: str,
    db=Depends(get_db),
    _=Depends(get_current_user),
):
    row = await db.fetch_one(
        """
        SELECT d.*, u.display_name AS creator_name, p.name AS project_name,
               p.id AS project_id
        FROM drawings d
        JOIN users u ON d.created_by = u.id
        JOIN projects p ON d.project_id = p.id
        WHERE d.id=$1
        """,
        drawing_id,
    )
    if not row:
        raise HTTPException(404, "图纸不存在")

    # 附加 AI 审查报告状态
    report = await db.fetch_one(
        "SELECT id, status, engine_results, total_issues, critical_issues, "
        "processing_ms, created_at, completed_at "
        "FROM ai_review_reports WHERE drawing_id=$1 ORDER BY created_at DESC LIMIT 1",
        drawing_id,
    )
    result = dict(row)
    if report:
        ai_report = dict(report)
        ai_report["progress"] = normalize_report_progress(ai_report, result.get("file_size_kb"))
        result["ai_report"] = ai_report
    else:
        result["ai_report"] = None
    return result


@router.get("/{drawing_id}/ai-review/progress")
async def get_ai_review_progress(
    drawing_id: str,
    db=Depends(get_db),
    _=Depends(get_current_user),
):
    row = await db.fetch_one(
        "SELECT file_size_kb FROM drawings WHERE id=$1",
        drawing_id,
    )
    if not row:
        raise HTTPException(404, "图纸不存在")
    report = await db.fetch_one(
        """
        SELECT id, status, engine_results, total_issues, critical_issues,
               processing_ms, created_at, completed_at
        FROM ai_review_reports
        WHERE drawing_id=$1
        ORDER BY created_at DESC LIMIT 1
        """,
        drawing_id,
    )
    if not report:
        raise HTTPException(404, "AI 审图任务不存在")
    ai_report = dict(report)
    return {
        "report_id": str(ai_report["id"]),
        "status": ai_report["status"],
        "progress": normalize_report_progress(ai_report, dict(row).get("file_size_kb")),
    }


@router.post("/{drawing_id}/ai-review/retry")
async def retry_ai_review(
    drawing_id: str,
    request: Request,
    db=Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    drawing = await db.fetch_one(
        "SELECT id, status, file_size_kb, drawing_no FROM drawings WHERE id=$1",
        drawing_id,
    )
    if not drawing:
        raise HTTPException(404, "图纸不存在")
    if drawing["status"] not in ("draft", "ai_reviewing"):
        raise HTTPException(409, f"当前状态 {drawing['status']} 不允许重新触发 AI 审图")

    report = await db.fetch_one(
        """
        SELECT id FROM ai_review_reports
        WHERE drawing_id=$1 AND status IN ('pending','processing','failed')
        ORDER BY created_at DESC LIMIT 1
        """,
        drawing_id,
    )
    if report is None:
        report = await db.fetch_one(
            "INSERT INTO ai_review_reports (drawing_id, status) VALUES ($1,'processing') RETURNING id",
            drawing_id,
        )

    initial_progress = progress_payload(
        status="processing",
        stage_key="queued",
        started_at=datetime.now(timezone.utc),
        completed_keys=[],
        active_keys=["queued"],
        estimated_total_seconds=estimate_total_seconds(drawing["file_size_kb"]),
    )
    await db.execute(
        """
        UPDATE ai_review_reports
        SET status='processing',
            engine_results=jsonb_build_object('progress', CAST($2 AS jsonb)),
            completed_at=NULL
        WHERE id=$1
        """,
        report["id"],
        json.dumps(initial_progress, ensure_ascii=False),
    )
    await db.execute(
        "UPDATE drawings SET status='ai_reviewing', current_stage='ai_reviewing', updated_at=now() WHERE id=$1",
        drawing_id,
    )
    await write_audit(
        db,
        user_id=current_user["id"],
        action="retry_ai_review",
        resource="drawing",
        resource_id=drawing_id,
        old_state={"status": drawing["status"]},
        new_state={"status": "ai_reviewing"},
        ip_address=request.client.host if request.client else None,
    )
    run_ai_review.delay(drawing_id)
    return {"ok": True, "status": "ai_reviewing", "message": "AI 审图任务已重新触发"}


# ── 图纸下载签名 URL ──────────────────────────────────────────

@router.get("/{drawing_id}/download-url")
async def get_download_url(
    drawing_id: str,
    db=Depends(get_db),
    _=Depends(get_current_user),
):
    row = await db.fetch_one("SELECT file_key FROM drawings WHERE id=$1", drawing_id)
    if not row or not row["file_key"]:
        raise HTTPException(404)
    url = presigned_get_url(row["file_key"], expires_seconds=300)
    return {"url": url, "expires_in": 300}


# ── 统一预览（Phase E1-4）─────────────────────────────────────

_PREVIEW_IMAGE_EXTS = {"png", "jpg", "jpeg", "webp"}
_PREVIEW_CAD_EXTS = {"dxf", "dwg"}


async def _render_preview_asset(
    project_id: str, drawing_id: str, file_key: str, file_ext: str
) -> None:
    """按需渲染 CAD 预览图并写回 model_assets key（与建模贴图互为缓存）。"""
    import asyncio

    from services.model_builder import _render_and_upload_sync

    await asyncio.to_thread(
        _render_and_upload_sync, project_id, drawing_id, file_key, file_ext
    )


@router.get("/{drawing_id}/preview")
async def get_preview(
    drawing_id: str,
    db=Depends(get_db),
    _=Depends(get_current_user),
):
    """统一预览入口：返回 {kind: pdf|image, url}。

    - PDF → 原文件 presigned（前端 iframe 内嵌）
    - 图片 → 原文件 presigned
    - DXF/DWG → 模型贴图资产 PNG（miss 时按需渲染，写回同 key 供建模复用）
    - 其余格式 → 422 PREVIEW_UNAVAILABLE（前端降级为下载）
    """
    row = await db.fetch_one(
        "SELECT id, project_id, file_key FROM drawings WHERE id=$1", drawing_id
    )
    if not row or not row["file_key"]:
        raise HTTPException(404)

    file_key = row["file_key"]
    project_id = str(row["project_id"])
    ext = file_key.rsplit(".", 1)[-1].lower() if "." in file_key else ""

    if ext == "pdf":
        return {"kind": "pdf", "url": presigned_get_url(file_key, expires_seconds=300)}
    if ext in _PREVIEW_IMAGE_EXTS:
        return {"kind": "image", "url": presigned_get_url(file_key, expires_seconds=300)}
    if ext in _PREVIEW_CAD_EXTS:
        asset_key = f"projects/{project_id}/model_assets/{drawing_id}.png"
        if not object_exists(asset_key):
            try:
                await _render_preview_asset(project_id, drawing_id, file_key, ext)
            except Exception as exc:  # noqa: BLE001 — 渲染失败对外统一 422
                logger.warning("[preview] CAD 渲染失败 drawing_id=%s: %s", drawing_id, exc)
                raise HTTPException(422, "PREVIEW_UNAVAILABLE") from exc
        return {"kind": "image", "url": presigned_get_url(asset_key, expires_seconds=300)}

    raise HTTPException(422, "PREVIEW_UNAVAILABLE")


# ── 图纸追溯(Phase G:识别了什么 + 用在哪)─────────────────────

@router.get("/{drawing_id}/trace")
async def get_drawing_trace(
    drawing_id: str,
    db=Depends(get_db),
    _=Depends(get_current_user),
):
    """正向追溯:这张图识别出的信息(按类别/抽取器)+ 模型用途(生成的构件/楼层)。"""
    from services.drawing_trace import build_drawing_trace
    trace = await build_drawing_trace(db, drawing_id)
    if trace is None:
        raise HTTPException(404, "DRAWING_NOT_FOUND")
    return trace


# ── AI 审查报告 ────────────────────────────────────────────────

async def _get_ai_report_or_404(drawing_id: str, db) -> dict:
    report = await db.fetch_one(
        "SELECT * FROM ai_review_reports WHERE drawing_id=$1 ORDER BY created_at DESC LIMIT 1",
        drawing_id,
    )
    if not report:
        raise HTTPException(404, "AI 审查报告不存在")
    if report["status"] != "done":
        raise HTTPException(409, f"AI 审查报告尚未完成，当前状态：{report['status']}")
    return dict(report)


@router.get("/{drawing_id}/ai-review/issues")
async def list_ai_review_issues(
    drawing_id: str,
    severity: str | None = Query(None, description="critical/major/minor/info"),
    status: str | None = Query(None, description="open/acknowledged/closed/waived"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db=Depends(get_db),
    _=Depends(get_current_user),
):
    """获取图纸 AI 审查问题列表（分页、可按严重程度/状态过滤）"""
    report = await _get_ai_report_or_404(drawing_id, db)
    report_id = report["id"]

    conditions = ["i.report_id=$1"]
    args: list = [str(report_id)]
    idx = 2
    if severity:
        conditions.append(f"i.severity=${idx}")
        args.append(severity)
        idx += 1
    if status:
        conditions.append(f"i.status=${idx}")
        args.append(status)
        idx += 1

    where = " AND ".join(conditions)
    issues = await db.fetch_all(
        f"""
        SELECT i.id, i.engine, i.severity, i.category, i.description,
               i.regulation_ref, i.location_x, i.location_y, i.suggestion, i.status,
               i.closed_at, i.created_at,
               i.discipline_code, i.location_json, i.concerns,
               i.issue_class, i.interface_primary, i.interface_related,
               i.risk_level, i.object_level, i.standard_question, i.evidence_gap,
               i.object_name, i.object_basis, i.scenario, i.scenario_reason,
               i.question_pack, i.doc_minutes, i.doc_reply,
               i.review_sop, i.review_method
        FROM ai_review_issues i
        WHERE {where}
        ORDER BY
            CASE i.severity WHEN 'critical' THEN 1 WHEN 'major' THEN 2
                            WHEN 'minor' THEN 3 ELSE 4 END,
            i.created_at
        LIMIT ${idx} OFFSET ${idx+1}
        """,
        *args, limit, offset,
    )
    total = await db.fetch_val(
        f"SELECT COUNT(*) FROM ai_review_issues i WHERE {where}", *args
    )
    return {
        "items": [dict(i) for i in issues],
        "total": total,
        "report_id": str(report_id),
        "report_summary": {
            "total_issues": report["total_issues"],
            "critical_issues": report["critical_issues"],
            "completed_at": str(report["completed_at"]) if report["completed_at"] else None,
        },
    }


@router.get("/{drawing_id}/ai-review/report-pdf")
async def download_ai_review_pdf(
    drawing_id: str,
    db=Depends(get_db),
    _=Depends(get_current_user),
):
    """下载批注版 AI 审查 PDF（实时生成或返回缓存版本）"""
    report = await _get_ai_report_or_404(drawing_id, db)

    # 优先返回已缓存版本
    if report.get("report_pdf_key"):
        try:
            pdf_bytes = get_file_bytes(report["report_pdf_key"], bucket=settings.minio_bucket_reports)
            return Response(
                content=pdf_bytes,
                media_type="application/pdf",
                headers={"Content-Disposition": f'attachment; filename="ai_review_{drawing_id[:8]}.pdf"'},
            )
        except Exception:
            pass  # 缓存失效则重新生成

    # 拉取原始图纸
    drawing = await db.fetch_one(
        "SELECT file_key, drawing_no, discipline FROM drawings WHERE id=$1", drawing_id
    )
    if not drawing or not drawing["file_key"]:
        raise HTTPException(404, "图纸文件不存在")

    try:
        original_bytes = get_file_bytes(drawing["file_key"])
    except Exception as e:
        raise HTTPException(500, f"图纸文件获取失败：{e}")

    # 拉取所有问题
    issues = await db.fetch_all(
        "SELECT * FROM ai_review_issues WHERE report_id=$1 ORDER BY severity, created_at",
        str(report["id"]),
    )

    pdf_bytes = generate_annotated_pdf(original_bytes, [dict(i) for i in issues])

    # 缓存到 MinIO（异步，失败不影响返回）
    try:
        cache_key = f"reports/{drawing_id}/ai_review.pdf"
        upload_file(pdf_bytes, cache_key, "application/pdf", bucket=settings.minio_bucket_reports)
        await db.execute(
            "UPDATE ai_review_reports SET report_pdf_key=$2 WHERE id=$1",
            str(report["id"]), cache_key,
        )
    except Exception:
        pass

    filename = f"ai_review_{drawing.get('drawing_no', drawing_id[:8])}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{drawing_id}/ai-review/report-excel")
async def download_ai_review_excel(
    drawing_id: str,
    db=Depends(get_db),
    _=Depends(get_current_user),
):
    """下载 Excel 问题清单（按严重程度分 Sheet）"""
    report = await _get_ai_report_or_404(drawing_id, db)

    # 优先返回已缓存版本
    if report.get("report_xlsx_key"):
        try:
            xlsx_bytes = get_file_bytes(report["report_xlsx_key"], bucket=settings.minio_bucket_reports)
            return Response(
                content=xlsx_bytes,
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                headers={"Content-Disposition": f'attachment; filename="ai_review_{drawing_id[:8]}.xlsx"'},
            )
        except Exception:
            pass

    drawing = await db.fetch_one(
        "SELECT drawing_no, discipline FROM drawings WHERE id=$1", drawing_id
    )
    issues = await db.fetch_all(
        "SELECT * FROM ai_review_issues WHERE report_id=$1 ORDER BY severity, created_at",
        str(report["id"]),
    )

    xlsx_bytes = generate_excel_report(
        issues=[dict(i) for i in issues],
        drawing_no=drawing["drawing_no"] if drawing else drawing_id[:8],
        discipline=drawing["discipline"] if drawing else "",
    )

    # 缓存到 MinIO
    try:
        cache_key = f"reports/{drawing_id}/ai_review.xlsx"
        upload_file(
            xlsx_bytes, cache_key,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            bucket=settings.minio_bucket_reports,
        )
        await db.execute(
            "UPDATE ai_review_reports SET report_xlsx_key=$2 WHERE id=$1",
            str(report["id"]), cache_key,
        )
    except Exception:
        pass

    filename = f"ai_review_{drawing['drawing_no'] if drawing else drawing_id[:8]}.xlsx"
    return Response(
        content=xlsx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
