"""
图纸管理 API
- 上传：MinIO 存储 + 元数据入库 + 自动触发 AI 审图（Celery）
- 列表 / 详情 / 状态查询
- 下载签名 URL（5 分钟有效）
- AI 审查报告：问题列表、批注 PDF、Excel 清单
"""
import uuid
import mimetypes
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Request, Query
from fastapi import status as http_status
from fastapi.responses import Response
from pydantic import BaseModel

from core.config import settings
from core.storage import upload_file, presigned_get_url, get_file_bytes
from core.workflow.drawing_state_machine import assert_valid_transition
from dependencies import get_db, get_current_user
from services.audit import write_audit
from services.ai_report_generator import generate_annotated_pdf, generate_excel_report
from tasks.ai_review import run_ai_review

router = APIRouter(prefix="/drawings", tags=["drawings"])

ALLOWED_TYPES = {
    "application/pdf",
    "image/vnd.dxf",
    "application/acad",
    "application/octet-stream",   # .dwg / .dxf 有时被识别为 octet-stream
    "application/x-ifc",
    "model/ifc",
}

MAX_FILE_SIZE = 200 * 1024 * 1024   # 200 MB


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
    # 文件大小校验（粗校：Content-Length）
    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(413, f"文件超过最大限制 {MAX_FILE_SIZE // (1024*1024)}MB")

    # 构建 MinIO 对象路径
    ext = (file.filename or "").rsplit(".", 1)[-1].lower()
    object_key = f"projects/{project_id}/drawings/{uuid.uuid4()}.{ext}"

    # 上传到 MinIO
    content_type = file.content_type or mimetypes.guess_type(file.filename or "")[0] or "application/octet-stream"
    upload_file(content, object_key, content_type)

    # 写入数据库
    drawing_id = await db.fetch_one(
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
    drawing_uuid = drawing_id["id"]

    # 创建 AI 审查报告占位记录
    report = await db.fetch_one(
        "INSERT INTO ai_review_reports (drawing_id, status) VALUES ($1,'pending') RETURNING id",
        str(drawing_uuid),
    )

    # 更新状态：draft → ai_reviewing
    await db.execute(
        "UPDATE drawings SET status='ai_reviewing', updated_at=now() WHERE id=$1",
        str(drawing_uuid),
    )
    await db.execute(
        "UPDATE ai_review_reports SET status='processing' WHERE id=$1",
        report["id"],
    )

    # 写审计日志
    await write_audit(
        db,
        user_id=current_user["id"],
        action="upload_drawing",
        resource="drawing",
        resource_id=str(drawing_uuid),
        new_state={"status": "ai_reviewing", "drawing_no": drawing_no},
        ip_address=request.client.host if request.client else None,
    )

    # 触发异步 AI 审图（Celery）
    run_ai_review.delay(str(drawing_uuid))

    # 重大变更预警（≥ 50 万，后续升级审批路径）
    if estimated_impact and estimated_impact >= 500_000:
        await db.execute(
            "UPDATE drawings SET finance_lock_status='pending_escalation' WHERE id=$1",
            str(drawing_uuid),
        )

    return {
        "drawing_id": str(drawing_uuid),
        "object_key": object_key,
        "status": "ai_reviewing",
        "message": "图纸已上传，AI 审图任务已触发",
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
        "SELECT id, status, total_issues, critical_issues, completed_at "
        "FROM ai_review_reports WHERE drawing_id=$1 ORDER BY created_at DESC LIMIT 1",
        drawing_id,
    )
    result = dict(row)
    result["ai_report"] = dict(report) if report else None
    return result


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
               i.closed_at, i.created_at
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
