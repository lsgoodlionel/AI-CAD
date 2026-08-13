"""工程信息聚合 API(Phase E1-2)。

图纸抽取信息(drawing_extracted_info,migration 029)的项目级消费端:
- GET  /projects/{project_id}/info/summary  按类别计数 + 抽取覆盖率
- GET  /projects/{project_id}/info/items    分页明细(联表 drawings 溯源)
- GET  /projects/{project_id}/info/axes     轴网专用聚合(供工程模型 E2 消费)
- POST /projects/{project_id}/info/extract  触发全项目重抽(Celery)

蓝图:docs/PHASE_E_BLUEPRINT.md §3。
"""
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from dependencies import get_db, get_current_user
from tasks.drawing_info_extract import extract_project_drawing_info

router = APIRouter(prefix="/projects", tags=["project-info"])

_SUMMARY_BY_CATEGORY_SQL = """
SELECT category, COUNT(*) AS cnt
FROM drawing_extracted_info
WHERE project_id = :project_id
GROUP BY category
ORDER BY cnt DESC
"""

_COVERAGE_SQL = """
SELECT
    (SELECT COUNT(*) FROM drawings WHERE project_id = :project_id) AS total_drawings,
    (SELECT COUNT(DISTINCT drawing_id) FROM drawing_extracted_info
      WHERE project_id = :project_id) AS extracted_drawings
"""

_ITEMS_COUNT_SQL = """
SELECT COUNT(*)
FROM drawing_extracted_info dei
JOIN drawings d ON d.id = dei.drawing_id
WHERE dei.project_id = :project_id {where}
"""

_ITEMS_SQL = """
SELECT dei.id, dei.drawing_id, dei.category, dei.content,
       dei.value_json, dei.location_json, dei.extractor,
       dei.confidence, dei.extraction_version, dei.source_kind,
       d.drawing_no, d.title AS drawing_title, d.discipline
FROM drawing_extracted_info dei
JOIN drawings d ON d.id = dei.drawing_id
WHERE dei.project_id = :project_id AND dei.is_active = true {where}
ORDER BY dei.category, d.drawing_no, dei.id
LIMIT :limit OFFSET :offset
"""

_AXES_SQL = """
SELECT dei.id, dei.drawing_id, dei.content, dei.value_json,
       dei.location_json, dei.extractor, dei.confidence,
       d.drawing_no, d.title AS drawing_title, d.discipline
FROM drawing_extracted_info dei
JOIN drawings d ON d.id = dei.drawing_id
WHERE dei.project_id = :project_id AND dei.category = 'axis'
ORDER BY d.drawing_no, dei.content
"""

_PROJECT_EXISTS_SQL = "SELECT id FROM projects WHERE id = :project_id"


def _parse_jsonb(value: Any) -> Any:
    """databases 返回的 JSONB 可能是 str,统一反序列化;非法值原样透传。"""
    if value is None or not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return value


def _item_dict(row: Any) -> dict:
    d = dict(row)
    d["value_json"] = _parse_jsonb(d.get("value_json"))
    d["location_json"] = _parse_jsonb(d.get("location_json"))
    if d.get("confidence") is not None:
        d["confidence"] = float(d["confidence"])
    return d


@router.get("/{project_id}/info/summary")
async def info_summary(
    project_id: str,
    db=Depends(get_db),
    _user=Depends(get_current_user),
):
    """按类别计数 + 抽取覆盖率(工程信息页顶部)。"""
    cat_rows = await db.fetch_all(
        _SUMMARY_BY_CATEGORY_SQL, {"project_id": project_id}
    )
    coverage = await db.fetch_one(_COVERAGE_SQL, {"project_id": project_id})
    return {
        "categories": [
            {"category": r["category"], "count": int(r["cnt"])} for r in cat_rows
        ],
        "coverage": dict(coverage) if coverage else
            {"total_drawings": 0, "extracted_drawings": 0},
    }


@router.get("/{project_id}/info/items")
async def info_items(
    project_id: str,
    category: str | None = Query(default=None, max_length=40),
    extractor: str | None = Query(default=None, max_length=40),
    discipline: str | None = Query(default=None, max_length=40),
    q: str | None = Query(default=None, max_length=100),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    db=Depends(get_db),
    _user=Depends(get_current_user),
):
    """分页明细。每行携带来源图纸(drawing_id/图号/标题)——溯源硬约束。"""
    clauses: list[str] = []
    params: dict[str, Any] = {"project_id": project_id}
    if category:
        clauses.append("AND dei.category = :category")
        params["category"] = category
    if extractor:
        clauses.append("AND dei.extractor = :extractor")
        params["extractor"] = extractor
    if discipline:
        clauses.append("AND d.discipline = :discipline")
        params["discipline"] = discipline
    if q:
        clauses.append("AND dei.content ILIKE :q")
        params["q"] = f"%{q}%"
    where = " ".join(clauses)

    total = await db.fetch_val(
        _ITEMS_COUNT_SQL.format(where=where + " AND dei.is_active = true"), params
    )
    rows = await db.fetch_all(
        _ITEMS_SQL.format(where=where),
        {**params, "limit": page_size, "offset": (page - 1) * page_size},
    )
    return {
        "total": int(total or 0),
        "page": page,
        "page_size": page_size,
        "items": [_item_dict(r) for r in rows],
    }


@router.get("/{project_id}/info/axes")
async def info_axes(
    project_id: str,
    db=Depends(get_db),
    _user=Depends(get_current_user),
):
    """轴网聚合(category='axis'),供工程模型轴网层(E2)与工程信息页消费。"""
    rows = await db.fetch_all(_AXES_SQL, {"project_id": project_id})
    return {"axes": [_item_dict(r) for r in rows]}


@router.post("/{project_id}/info/extract", status_code=202)
async def trigger_extract(
    project_id: str,
    with_vlm: bool = Query(default=False, description="是否含 VLM 读图(慢,~40s/图)"),
    db=Depends(get_db),
    _user=Depends(get_current_user),
):
    """触发全项目工程信息重抽(异步,Celery default 队列)。with_vlm=True 含 VLM 读图。"""
    exists = await db.fetch_one(_PROJECT_EXISTS_SQL, {"project_id": project_id})
    if exists is None:
        raise HTTPException(status_code=404, detail="PROJECT_NOT_FOUND")
    async_result = extract_project_drawing_info.delay(project_id, with_vlm)
    return {"task_id": str(async_result.id), "project_id": project_id, "with_vlm": with_vlm}


_SCAN_OVERALL_SQL = """
SELECT
    (SELECT COUNT(*) FROM drawings WHERE project_id = :project_id) AS total,
    COUNT(*) FILTER (WHERE status = 'ready')      AS ready,
    COUNT(*) FILTER (WHERE status = 'extracting') AS extracting,
    COUNT(*) FILTER (WHERE status = 'pending')    AS pending,
    COUNT(*) FILTER (WHERE error IS NOT NULL)     AS failed,
    COUNT(*)                                      AS with_status
FROM drawing_archive_status WHERE project_id = :project_id
"""

_SCAN_ROWS_SQL = """
SELECT s.drawing_id, d.drawing_no, d.title, d.discipline,
       s.status, s.item_count, s.extractors_done, s.summary, s.updated_at,
       s.error, s.started_at
FROM drawing_archive_status s
JOIN drawings d ON d.id = s.drawing_id
WHERE s.project_id = :project_id {where}
ORDER BY s.updated_at DESC
LIMIT :limit OFFSET :offset
"""


@router.get("/{project_id}/info/scan-progress")
async def scan_progress(
    project_id: str,
    status: str | None = Query(default=None, description="按状态过滤 pending/extracting/ready"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    db=Depends(get_db),
    _user=Depends(get_current_user),
):
    """扫描进度:总进度 + 每图状态/已完成抽取器/各类计数/内容摘要(进度页轮询)。"""
    overall_row = await db.fetch_one(_SCAN_OVERALL_SQL, {"project_id": project_id})
    overall = dict(overall_row) if overall_row else {
        "total": 0, "ready": 0, "extracting": 0, "pending": 0,
    }
    for k in ("total", "ready", "extracting", "pending", "failed", "with_status"):
        overall[k] = int(overall.get(k) or 0)
    # 未处理 = 总图纸 - 有 status 行的图纸(从未进抽取的图,卡进度的隐形大头)
    overall["unprocessed"] = max(overall["total"] - overall["with_status"], 0)
    # ready 占比(有效产出线);另给 processed 占比(已处理线),区分「没跑」与「跑了空」
    overall["percent"] = (
        round(overall["ready"] * 100 / overall["total"]) if overall["total"] else 0
    )
    overall["processed_percent"] = (
        round(overall["with_status"] * 100 / overall["total"]) if overall["total"] else 0
    )

    where = ""
    params: dict[str, Any] = {"project_id": project_id}
    if status:
        where = "AND s.status = :status"
        params["status"] = status
    rows = await db.fetch_all(
        _SCAN_ROWS_SQL.format(where=where),
        {**params, "limit": page_size, "offset": (page - 1) * page_size},
    )
    drawings = []
    for r in rows:
        d = dict(r)
        d["extractors_done"] = _parse_jsonb(d.get("extractors_done")) or []
        d["summary"] = _parse_jsonb(d.get("summary")) or {}
        drawings.append(d)
    return {"overall": overall, "page": page, "drawings": drawings}


# ── 比例尺确认(攻 drawing_transform 瓶颈,人审在环)────────────────────
#
# 实测:三条自动路径(尺寸链 3.6% / 图幅众数 50.9% / OCR 文字自动选 24-26%)
# 均不达标,且现有变换本身质量存疑(平均 confidence 0.007、仅 46% 合标准比例尺)。
# 故改为「高置信候选 + 人一键确认」:1310 张有候选、1205 张(92%)首选为标准比例尺、
# 1085 张唯一候选可一键确认 → 覆盖率 30.5%→~77%,且是精确值。

_SCALE_QUEUE_SQL = """
SELECT e.drawing_id, d.drawing_no, d.title, d.discipline,
       max(t.scale_m_pt) AS current_scale,
       array_agg(e.content) AS texts
FROM drawing_extracted_info e
JOIN drawings d ON d.id = e.drawing_id
LEFT JOIN drawing_transform t ON t.drawing_id = e.drawing_id
WHERE e.project_id = :project_id AND e.is_active
  AND (t.drawing_id IS NULL OR :include_suspect)
  AND e.content ~ '1[:：]\\s*[0-9]{1,4}'
GROUP BY e.drawing_id, d.drawing_no, d.title, d.discipline
LIMIT :limit OFFSET :offset
"""


@router.get("/{project_id}/scale-candidates")
async def list_scale_candidates(
    project_id: str,
    only_single: bool = Query(default=False, description="仅列唯一候选(可一键确认)"),
    include_suspect: bool = Query(default=False,
        description="纳入「已有但非标准比例尺」的变换供复核(实测 376/705 非标准)"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    db=Depends(get_db),
    _user=Depends(get_current_user),
):
    """待确认比例尺队列:无坐标变换但图上写明 `1:N` 的图纸 + 候选(按票数排序)。"""
    from services.scale_candidates import build_scale_candidates

    from services.scale_candidates import assess_existing_scale

    rows = await db.fetch_all(_SCALE_QUEUE_SQL, {
        "project_id": project_id, "limit": page_size, "offset": (page - 1) * page_size,
        "include_suspect": include_suspect,
    })
    items = []
    for r in rows:
        candidates = build_scale_candidates(list(r["texts"] or []))
        if not candidates:
            continue
        if only_single and len(candidates) > 1:
            continue
        current = r["current_scale"]
        existing = assess_existing_scale(float(current)) if current else None
        # 复核模式下只列「已有但非标准」的(标准的无需复核)
        if include_suspect and existing is not None and existing["is_standard"]:
            continue
        items.append({
            "drawing_id": str(r["drawing_id"]),
            "drawing_no": r["drawing_no"],
            "title": r["title"],
            "discipline": r["discipline"],
            "candidates": candidates,
            "single": len(candidates) == 1,
            "current_scale": float(current) if current else None,
            "current_is_standard": existing["is_standard"] if existing else None,
            "current_label": (f"1:{existing['denominator']:.0f}"
                              if existing and existing.get("denominator") else None),
        })
    return {"project_id": project_id, "page": page, "items": items}


class ScaleConfirmBody(BaseModel):
    denominator: int          # 人选定的比例尺分母(1:N 的 N)


@router.post("/{project_id}/drawings/{drawing_id}/scale-confirm", status_code=201)
async def confirm_drawing_scale(
    project_id: str,
    drawing_id: str,
    body: ScaleConfirmBody,
    db=Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """人确认图纸比例尺 → 精确换算 + 从 PDF 取真实页高 + 档案轴号定原点 → 落变换。

    page_h 必须来自真实 PDF(档案坐标推断不可靠);轴号缺失时原点退化为页面左下角。
    """
    from services.scale_candidates import build_confirmed_transform
    from services.drawing_transform import (
        TRANSFORM_SOURCE_MANUAL, DrawingTransform, persist_transform,
    )

    row = await db.fetch_one(
        "SELECT file_key, title FROM drawings WHERE id=:d AND project_id=:p",
        {"d": drawing_id, "p": project_id})
    if row is None:
        raise HTTPException(status_code=404, detail="DRAWING_NOT_FOUND")
    # 不按比例的图(N.T.S / 目录/说明/原理图等)不得建立坐标变换:
    # 其上的 1:N 多是别的图纸的比例,误采会让构件位置全错
    from services.non_scaled_drawings import is_non_scaled
    non_scaled, reason = is_non_scaled(row["title"])
    if non_scaled:
        raise HTTPException(status_code=422, detail=f"NON_SCALED_DRAWING: {reason}")

    page_h = await _pdf_page_height(row["file_key"])
    if not page_h:
        raise HTTPException(status_code=422, detail="PAGE_SIZE_UNAVAILABLE")

    axis_rows = await db.fetch_all("""
        SELECT location_json FROM drawing_extracted_info
        WHERE drawing_id=:d AND category='axis' AND is_active""", {"d": drawing_id})
    axis_points = []
    for a in axis_rows:
        loc = _parse_jsonb(a["location_json"]) or {}
        x, y = loc.get("x"), loc.get("y")
        if x is None:
            bbox = loc.get("bbox")
            if isinstance(bbox, (list, tuple)) and len(bbox) >= 4:
                x, y = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
        if x is not None and y is not None:
            axis_points.append({"x": float(x), "y": float(y)})

    from services.annotation_events import record as record_event
    from services.scale_candidates import assess_existing_scale
    existing = await db.fetch_one(
        "SELECT scale_m_pt FROM drawing_transform WHERE drawing_id=:d",
        {"d": drawing_id})
    auto_label = None
    if existing is not None:
        auto_label = assess_existing_scale(float(existing["scale_m_pt"])).get("label")
    await record_event(
        db, project_id=project_id, drawing_id=drawing_id, kind="scale",
        human_value=f"1:{body.denominator}", auto_value=auto_label,
        created_by=str(current_user["id"]))

    payload = build_confirmed_transform(body.denominator, axis_points, page_h)
    if payload is None:
        raise HTTPException(status_code=400, detail="INVALID_SCALE")
    await persist_transform(db, project_id=project_id, drawing_id=drawing_id,
        transform=DrawingTransform(
            scale_m_pt=payload["scale_m_pt"], origin_x=payload["origin_x"],
            origin_y=payload["origin_y"], page_h=payload["page_h"],
            confidence=payload["confidence"],
            source=TRANSFORM_SOURCE_MANUAL))
    return {"success": True, "data": {**payload, "drawing_id": drawing_id}, "error": None}


async def _pdf_page_height(file_key: str | None) -> float | None:
    """从 PDF 首页读页高(pt)。非 PDF/读取失败 → None(端点返回 422)。"""
    if not file_key or not str(file_key).lower().endswith(".pdf"):
        return None
    try:
        import asyncio

        import fitz

        from core.storage import get_file_bytes

        data = await asyncio.get_event_loop().run_in_executor(
            None, get_file_bytes, file_key)
        with fitz.open(stream=data, filetype="pdf") as doc:
            if doc.page_count < 1:
                return None
            return float(doc[0].rect.height)
    except Exception:  # noqa: BLE001 — 读不到页高则不落变换(宁缺勿错)
        return None


@router.get("/{project_id}/review-tasks")
async def get_review_tasks(
    project_id: str,
    db=Depends(get_db),
    _user=Depends(get_current_user),
):
    """人审统一工作台:各类待办数量 + 价值说明 + 跳转目标(按价值排序)。

    解决入口散落(比例尺在工程信息页、构件核对在模型页审校模式、标高在另一面板…)
    导致「找不到方便入口做审核修正复核」的问题。
    """
    from services.review_tasks import collect_review_tasks
    tasks = await collect_review_tasks(db, project_id)
    return {
        "project_id": project_id,
        "tasks": [{**t, "route": t["route"].format(project_id=project_id)} for t in tasks],
        "total_pending": sum(t["count"] for t in tasks),
    }


class ScaleBatchConfirmBody(BaseModel):
    """批量确认:仅处理「唯一候选 + 命中标准比例尺」的高置信项。"""
    limit: int = 200
    require_single: bool = True      # 仅唯一候选(无歧义)
    require_standard: bool = True    # 仅标准比例尺(换算精确)


@router.post("/{project_id}/scale-confirm-batch", status_code=201)
async def confirm_scales_batch(
    project_id: str,
    body: ScaleBatchConfirmBody,
    db=Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """**批量确认高置信比例尺** —— 1310 张逐张点不现实,提供一键处理。

    只处理无歧义项(唯一候选 + 标准比例尺,实测 93% 首选命中标准值),
    每张仍走与单张确认相同的精确换算路径(读真实 PDF 页高 + 档案轴号定原点)。
    有歧义的(多候选/非标准)一律留给人逐张判断。
    """
    from services.scale_candidates import build_scale_candidates, build_confirmed_transform
    from services.drawing_transform import (
        TRANSFORM_SOURCE_MANUAL, DrawingTransform, persist_transform,
    )
    from services.non_scaled_drawings import is_non_scaled

    rows = await db.fetch_all(_SCALE_QUEUE_SQL, {
        "project_id": project_id, "limit": max(body.limit * 3, 100), "offset": 0,
        "include_suspect": False,
    })
    confirmed, skipped, failed = 0, 0, 0
    details: list[dict] = []
    for r in rows:
        if confirmed >= body.limit:
            break
        # 跳过不按比例的图(N.T.S / 文字类):其上的 1:N 是别的图纸的比例
        non_scaled, _ = is_non_scaled(r["title"], list(r["texts"] or []))
        if non_scaled:
            skipped += 1
            continue
        candidates = build_scale_candidates(list(r["texts"] or []))
        if not candidates:
            skipped += 1
            continue
        if body.require_single and len(candidates) > 1:
            skipped += 1
            continue
        top = candidates[0]
        if body.require_standard and not top["is_standard"]:
            skipped += 1
            continue
        drawing_id = str(r["drawing_id"])
        file_row = await db.fetch_one(
            "SELECT file_key FROM drawings WHERE id=:d", {"d": drawing_id})
        page_h = await _pdf_page_height(file_row["file_key"] if file_row else None)
        if not page_h:
            failed += 1
            continue
        axis_rows = await db.fetch_all("""
            SELECT location_json FROM drawing_extracted_info
            WHERE drawing_id=:d AND category='axis' AND is_active""", {"d": drawing_id})
        axis_points = []
        for a in axis_rows:
            loc = _parse_jsonb(a["location_json"]) or {}
            x, y = loc.get("x"), loc.get("y")
            if x is None:
                bbox = loc.get("bbox")
                if isinstance(bbox, (list, tuple)) and len(bbox) >= 4:
                    x, y = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
            if x is not None and y is not None:
                axis_points.append({"x": float(x), "y": float(y)})
        payload = build_confirmed_transform(top["denominator"], axis_points, page_h)
        if payload is None:
            failed += 1
            continue
        await persist_transform(db, project_id=project_id, drawing_id=drawing_id,
            transform=DrawingTransform(
                scale_m_pt=payload["scale_m_pt"], origin_x=payload["origin_x"],
                origin_y=payload["origin_y"], page_h=payload["page_h"],
                confidence=payload["confidence"]))
        confirmed += 1
        if len(details) < 5:
            details.append({"drawing_no": r["drawing_no"], "scale": top["label"]})
    return {
        "success": True,
        "data": {"confirmed": confirmed, "skipped_ambiguous": skipped,
                 "failed": failed, "samples": details},
        "error": None,
    }


# ── 人工标定轴线基准(绕开 OCR 轴号瓶颈)────────────────────────────

class ManualAxisBody(BaseModel):
    label: str                          # 轴号 1/2/3… 或 A/B/C…
    direction: str                      # x=竖向 | y=横向 | skew=斜向
    x1_norm: float                      # 归一化页面坐标(同除 page_h)
    y1_norm: float
    x2_norm: float
    y2_norm: float
    spacing_to_prev_mm: float | None = None   # 与上一条同向轴线的实际轴距(可反算比例尺)
    note: str | None = None
    from_handdraw: bool = False   # 是手描的还是点选候选线(学习信号:手描多=候选太严)


@router.get("/{project_id}/drawings/{drawing_id}/manual-axes")
async def list_manual_axes(
    project_id: str,
    drawing_id: str,
    db=Depends(get_db),
    _user=Depends(get_current_user),
):
    """读该图已标定的轴线基准。"""
    from services.manual_axis import fetch_drawing_axes
    axes = await fetch_drawing_axes(db, drawing_id)
    return {"drawing_id": drawing_id, "axes": axes, "count": len(axes)}


@router.post("/{project_id}/drawings/{drawing_id}/manual-axes", status_code=201)
async def save_manual_axis(
    project_id: str,
    drawing_id: str,
    body: ManualAxisBody,
    db=Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """标定一条轴线基准(同图同向同轴号幂等覆盖)。

    这是绕开 OCR 轴号瓶颈的入口:人指定少量基准,系统在此之上做大范围识别。
    """
    # skew = 斜向轴线(放射柱网/异形平面常见),同样是合法轴线
    if body.direction not in ("x", "y", "skew"):
        raise HTTPException(status_code=400, detail="INVALID_DIRECTION")
    if not str(body.label).strip():
        raise HTTPException(status_code=400, detail="LABEL_REQUIRED")
    from services.manual_axis import axis_position, save_axis
    payload = body.model_dump()
    if axis_position(payload) is None:
        raise HTTPException(status_code=400,
                            detail="AXIS_NOT_STRAIGHT: 轴线须近似垂直或水平")
    axis_id = await save_axis(db, project_id, drawing_id, payload,
                              created_by=str(current_user["id"]))

    # 学习闭环:手描说明自动候选没覆盖到,是可量化的阈值信号
    from services.annotation_events import record as record_event
    from services.axis_geometry import line_angle_deg
    span = abs(float(body.y2_norm) - float(body.y1_norm)) \
        if body.direction == "x" else abs(float(body.x2_norm) - float(body.x1_norm))
    await record_event(
        db, project_id=project_id, drawing_id=drawing_id, kind="axis",
        field=body.direction, human_value=body.label,
        context={"source": "handdrawn" if body.from_handdraw else "candidate",
                 "span": round(span, 4),
                 "angle_deg": round(line_angle_deg(payload), 2)},
        created_by=str(current_user["id"]))

    # 记住这条线的位置(与轴号分开存):下次同图/同版式图纸自动补进候选,不必重描
    remembered = None
    try:
        from services.axis_line_memory import remember_line
        from services.title_block_apply import page_size
        from services.title_block_template import aspect_bucket

        row = await db.fetch_one(
            "SELECT file_key FROM drawings WHERE id=:d", {"d": drawing_id})
        size = await page_size(row["file_key"]) if row else None
        remembered = await remember_line(
            db, project_id=project_id, drawing_id=drawing_id, line=payload,
            page_aspect=aspect_bucket(*size) if size else None,
            created_by=str(current_user["id"]))
    except Exception:  # noqa: BLE001 — 记忆失败不影响标定本身
        remembered = None
    return {"success": True, "error": None,
            "data": {"id": axis_id, "memory_id": remembered}}


# ── 学习闭环:标注 → 分析 → 建议 → 人审采纳 → 生效 ──────────────

@router.post("/{project_id}/optimization/run", status_code=201)
async def run_learning_optimization(
    project_id: str,
    db=Depends(get_db),
    _user=Depends(get_current_user),
):
    """跑一轮学习分析:扫人工标注事件 → 产出可执行建议 + 过程日志。"""
    from services.optimization_engine import run_optimization
    return {"success": True, "error": None,
            "data": await run_optimization(db, project_id, trigger="manual")}


@router.get("/{project_id}/optimization/runs")
async def list_optimization_runs(
    project_id: str,
    limit: int = Query(20, ge=1, le=100),
    db=Depends(get_db),
    _user=Depends(get_current_user),
):
    """学习过程日志(实时查看):每轮扫了多少事件、推出什么、逐步经过。"""
    rows = await db.fetch_all(
        "SELECT id, trigger, events_scanned, findings, steps_json, "
        "       started_at, finished_at, error "
        "FROM optimization_runs WHERE project_id = CAST(:p AS uuid) "
        "ORDER BY started_at DESC LIMIT :n",
        {"p": project_id, "n": limit})
    items = []
    for r in rows:
        d = dict(r)
        d["id"] = str(d["id"])
        d["steps_json"] = _parse_jsonb(d.get("steps_json")) or []
        items.append(d)
    return {"items": items, "count": len(items)}


@router.get("/{project_id}/optimization/suggestions")
async def list_improvement_suggestions(
    project_id: str,
    status: str | None = Query(None),
    db=Depends(get_db),
    _user=Depends(get_current_user),
):
    """改进建议清单(按预估影响降序)。auto_applicable 者采纳即生效。"""
    where = "WHERE project_id = CAST(:p AS uuid)"
    params: dict = {"p": project_id}
    if status:
        where += " AND status = :s"
        params["s"] = status
    rows = await db.fetch_all(
        "SELECT id, category, title, detail, evidence_json, impact, confidence, "
        "       auto_applicable, status, created_at, applied_at "
        f"FROM improvement_suggestions {where} "
        "ORDER BY (status='pending') DESC, impact DESC, confidence DESC LIMIT 200",
        params)
    items = []
    for r in rows:
        d = dict(r)
        d["id"] = str(d["id"])
        d["evidence"] = _parse_jsonb(d.pop("evidence_json")) or {}
        d["confidence"] = float(d["confidence"])
        items.append(d)
    return {"items": items, "count": len(items),
            "pending": sum(1 for i in items if i["status"] == "pending")}


class SuggestionReviewBody(BaseModel):
    accept: bool


@router.post("/{project_id}/optimization/suggestions/{suggestion_id}/review",
             status_code=201)
async def review_improvement_suggestion(
    project_id: str,
    suggestion_id: str,
    body: SuggestionReviewBody,
    db=Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """人审建议。

    - 可自动生效的采纳后**当场写入规则并生效**(词表/OCR 纠错/阈值);
    - 需开发介入的采纳后只标为待导出,**系统行为不变**——不假装已解决。
    """
    from services.optimization_engine import review_suggestion
    res = await review_suggestion(db, suggestion_id, accept=body.accept,
                                  user_id=str(current_user["id"]))
    if not res.get("ok"):
        raise HTTPException(status_code=404, detail=res.get("error"))
    return {"success": True, "error": None, "data": res}


@router.get("/{project_id}/optimization/export")
async def export_optimization_package(
    project_id: str,
    db=Depends(get_db),
    _user=Depends(get_current_user),
):
    """导出给开发的结构化包:需开发介入的建议 + 完整证据 + 过程日志。"""
    from services.optimization_engine import build_export

    run_row = await db.fetch_one(
        "SELECT id, events_scanned, steps_json FROM optimization_runs "
        "WHERE project_id = CAST(:p AS uuid) ORDER BY started_at DESC LIMIT 1",
        {"p": project_id})
    run = {"run_id": str(run_row["id"]) if run_row else None,
           "scanned": run_row["events_scanned"] if run_row else 0,
           "steps": _parse_jsonb(run_row["steps_json"]) if run_row else []}
    rows = await db.fetch_all(
        "SELECT category, title, detail, evidence_json, impact, confidence, "
        "       auto_applicable FROM improvement_suggestions "
        "WHERE project_id = CAST(:p AS uuid) AND status IN ('pending','exported') "
        "ORDER BY impact DESC", {"p": project_id})
    suggestions = [{
        "category": r["category"], "title": r["title"], "detail": r["detail"],
        "evidence": _parse_jsonb(r["evidence_json"]) or {},
        "impact": r["impact"], "confidence": float(r["confidence"]),
        "auto_applicable": r["auto_applicable"],
    } for r in rows]
    return build_export(run, suggestions)


@router.get("/{project_id}/optimization/learned-rules")
async def list_learned_rules(
    project_id: str,
    db=Depends(get_db),
    _user=Depends(get_current_user),
):
    """已生效的学习规则——「采纳后系统到底变了什么」的凭据。"""
    rows = await db.fetch_all(
        "SELECT rule_type, rule_key, rule_value, hit_count, created_at "
        "FROM learned_rules WHERE project_id IS NULL "
        "   OR project_id = CAST(:p AS uuid) ORDER BY created_at DESC",
        {"p": project_id})
    return {"items": [dict(r) for r in rows], "count": len(rows)}


@router.post("/{project_id}/discipline-backfill", status_code=201)
async def backfill_discipline_from_title_block(
    project_id: str,
    dry_run: bool = Query(False),
    db=Depends(get_db),
    _user=Depends(get_current_user),
):
    """按图框「专业」栏全面修正图纸专业(读档案层已有 OCR,不重新识图)。

    专业原先靠文件名/标题猜,实测大量错判(地质剖面 → general、卫生间排水详图 → general)。
    图框「专业」栏是设计单位填写的权威值。dry_run=True 只统计不写库。
    """
    from services.title_block_discipline import backfill_project
    res = await backfill_project(db, project_id, dry_run=dry_run)
    return {"success": True, "error": None, "data": res}


class TitleBlockRegionBody(BaseModel):
    field: str                # discipline | drawing_no | title
    x1: float                 # 归一化页面坐标(同除 page_h)
    y1: float
    x2: float
    y2: float
    remember: bool = True     # 是否存为模板记忆(默认存)
    global_memory: bool = False   # 存为跨项目全局记忆
    value: str | None = None      # 人工直接给值(自动识别糊了时,人说了算)


@router.post("/{project_id}/drawings/{drawing_id}/title-block/region",
             status_code=201)
async def read_title_block_region(
    project_id: str,
    drawing_id: str,
    body: TitleBlockRegionBody,
    db=Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """人工框选图框某字段的区域 → 读值写库 + 记成模板(供同版式图纸自动套用)。"""
    from services.title_block_apply import read_by_region
    from services.title_block_template import (
        SUPPORTED_FIELDS, normalize_region, save_template,
    )

    if body.field not in SUPPORTED_FIELDS:
        raise HTTPException(status_code=400, detail="UNSUPPORTED_FIELD")
    # 按页高换算最小框选门槛:超大图上一格字段的归一化尺寸极小,
    # 用固定归一化门槛会把合法框选判成空
    from services.title_block_apply import page_size as _page_size
    row = await db.fetch_one(
        "SELECT file_key FROM drawings WHERE id=:d AND project_id=:p",
        {"d": drawing_id, "p": project_id})
    size = await _page_size(row["file_key"]) if row else None
    region = normalize_region(body.x1, body.y1, body.x2, body.y2,
                              page_h_pt=size[1] if size else None)
    if region is None:
        raise HTTPException(
            status_code=400,
            detail="EMPTY_REGION: 框选区域太小,请拖出一个能盖住字段的矩形")

    res = await read_by_region(db, project_id=project_id, drawing_id=drawing_id,
                               field=body.field, region=region,
                               override=(body.value or "").strip() or None)
    if res.get("error"):
        raise HTTPException(status_code=422, detail=res["error"])
    if res["value"] is None:
        # 不猜:把区域原文回给人确认(重识别常把「建筑」认成「建 个人」)。
        # 200 而非 422——这不是失败,是需要人拍板的一步。
        return {"success": False, "error": "NEEDS_CONFIRMATION", "data": {
            "value": None, "raw_text": res.get("raw_text") or "",
            "page_aspect": res["page_aspect"], "template_id": None}}

    # 学习闭环:记下「系统原本没读出来 / 读错了,人给的是什么」
    from services.annotation_events import record as record_event
    await record_event(
        db, project_id=project_id, drawing_id=drawing_id,
        kind="title_block" if body.field != "discipline" else "discipline",
        field=body.field, human_value=res["value"],
        auto_value=None,
        context={"raw_text": res.get("raw_text") or "",
                 "page_aspect": res.get("page_aspect"),
                 "manual_override": bool(body.value)},
        created_by=str(current_user["id"]))

    template_id = None
    if body.remember:
        template_id = await save_template(
            db, project_id=None if body.global_memory else project_id,
            field=body.field, region=region, page_aspect=res["page_aspect"],
            source_drawing_id=drawing_id, created_by=str(current_user["id"]))
    return {"success": True, "error": None, "data": {
        "value": res["value"], "raw_text": res.get("raw_text") or "",
        "page_aspect": res["page_aspect"], "template_id": template_id}}


@router.post("/{project_id}/title-block/apply", status_code=202)
async def apply_title_block_templates(
    project_id: str,
    field: str = Query("discipline"),
    limit: int = Query(500, ge=1, le=3000),
    ocr_budget: int = Query(400, ge=1, le=3000),
    _user=Depends(get_current_user),
):
    """把区域记忆套到尚未读到该字段的图纸——**异步执行**,返回 task_id。

    档案里读不到时要做区域重识别(实测每次数秒),几百张图要跑几分钟,
    放在 HTTP 请求里必被前端超时掐断(用户看到的「批量刷新失败」就是这么来的)。
    """
    from tasks.title_block_apply import apply_title_block_templates_task
    async_result = apply_title_block_templates_task.delay(
        project_id, field, limit, ocr_budget)
    return {"success": True, "error": None,
            "data": {"task_id": str(async_result.id)}}


@router.get("/{project_id}/title-block/apply/{task_id}")
async def get_title_block_apply_status(
    project_id: str,
    task_id: str,
    _user=Depends(get_current_user),
):
    """查批量套用进度/结果。state: PENDING|PROGRESS|SUCCESS|FAILURE。"""
    from core.celery_app import celery_app
    res = celery_app.AsyncResult(task_id)
    payload: dict = {"task_id": task_id, "state": res.state}
    if res.state == "SUCCESS":
        payload["data"] = res.result
    elif res.state == "FAILURE":
        payload["error"] = str(res.info)[:300]
    elif res.state == "PROGRESS" and isinstance(res.info, dict):
        payload["progress"] = res.info
    return payload


@router.get("/{project_id}/title-block/templates")
async def list_title_block_templates(
    project_id: str,
    db=Depends(get_db),
    _user=Depends(get_current_user),
):
    """记忆库:本项目 + 全局的图框字段区域模板(按命中次数排序)。"""
    rows = await db.fetch_all(
        "SELECT id, project_id, field, x1, y1, x2, y2, page_aspect, hit_count, "
        "       created_at, last_used_at "
        "FROM title_block_templates "
        "WHERE project_id = CAST(:pid AS uuid) OR project_id IS NULL "
        "ORDER BY hit_count DESC, created_at DESC LIMIT 200",
        {"pid": project_id})
    items = []
    for r in rows:
        d = dict(r)
        d["id"] = str(d["id"])
        d["scope"] = "project" if d.get("project_id") else "global"
        d.pop("project_id", None)
        items.append(d)
    return {"items": items, "count": len(items)}


@router.post("/{project_id}/directory-rebuild", status_code=201)
async def rebuild_drawing_directory(
    project_id: str,
    dry_run: bool = Query(False),
    db=Depends(get_db),
    _user=Depends(get_current_user),
):
    """按图纸目录重建默认排序(目录在前 → 目录列出 → 其余按图号自然序)。"""
    from services.drawing_directory import rebuild_directory
    res = await rebuild_directory(db, project_id, dry_run=dry_run)
    return {"success": True, "error": None, "data": res}


@router.get("/{project_id}/axis-calibration")
async def axis_calibration_status(
    project_id: str,
    plan_only: bool = Query(True),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db=Depends(get_db),
    _user=Depends(get_current_user),
):
    """图纸轴线标定进度(未标定的平面图优先)——人工标定的统一入口数据源。"""
    from services.axis_calibration_status import list_calibration_status
    return await list_calibration_status(
        db, project_id, plan_only=plan_only, page=page, page_size=page_size)


@router.get("/{project_id}/drawings/{drawing_id}/auto-axes")
async def list_auto_recognized_axes(
    project_id: str,
    drawing_id: str,
    db=Depends(get_db),
    _user=Depends(get_current_user),
):
    """**自动已识别的轴线 + 轴号**(归一化坐标),供人工标定时对照参考。

    数据来自档案层 `category='axis'` 的抽取项(OCR/矢量文字读到的轴号 + 页面位置)。
    人工标定时看得见系统认成什么,才能判断该补哪条、该改哪条——否则等于蒙着眼标。

    注:这些轴号本身可信度低(蓝图 §9.12 实测位置序与数值序仅 0.3% 一致,
    建模时会被合理性校验剔除),此处**仅作参考展示**,不代表可直接采用。
    """
    from services.axis_geometry import line_through_point
    from services.title_block_apply import page_size

    row = await db.fetch_one(
        "SELECT file_key FROM drawings WHERE id=:d AND project_id=:p",
        {"d": drawing_id, "p": project_id})
    if row is None:
        raise HTTPException(status_code=404, detail="DRAWING_NOT_FOUND")
    size = await page_size(row["file_key"])
    if size is None:
        return {"axes": [], "count": 0, "reason": "PAGE_SIZE_UNAVAILABLE"}
    page_h = size[1]

    rows = await db.fetch_all(
        "SELECT content, location_json, confidence, extractor "
        "FROM drawing_extracted_info "
        "WHERE drawing_id = :d AND is_active AND category = 'axis'",
        {"d": drawing_id})

    axes: list[dict] = []
    for r in rows:
        label = str(r["content"] or "").strip()
        if not label:
            continue
        loc = _parse_jsonb(r["location_json"]) or {}
        x_pt, y_pt = loc.get("x"), loc.get("y")
        if x_pt is None:
            bbox = loc.get("bbox")
            if isinstance(bbox, (list, tuple)) and len(bbox) >= 4:
                x_pt, y_pt = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
        if x_pt is None or y_pt is None:
            continue
        # 轴号惯例:纯数字 → 竖向轴线(定 x);字母 → 横向轴线(定 y)
        direction = "x" if label.isdigit() else "y"
        xn, yn = float(x_pt) / page_h, float(y_pt) / page_h
        line = line_through_point(xn, yn, 90.0 if direction == "x" else 0.0)
        axes.append({
            "label": label, "direction": direction, **line,
            "confidence": float(r["confidence"]) if r["confidence"] is not None else None,
            "extractor": r["extractor"],
        })
    return {"axes": axes, "count": len(axes)}


@router.get("/{project_id}/drawings/{drawing_id}/manual-axes/line-candidates")
async def list_axis_line_candidates(
    project_id: str,
    drawing_id: str,
    min_span: float = Query(0.25, ge=0.05, le=0.95),
    db=Depends(get_db),
    _user=Depends(get_current_user),
):
    """图上可直接选中的候选轴线(归一化坐标)。

    让人「照着图纸内容直接选某一条线」而不必手描端点:前端点哪儿就吸附到最近候选。
    抽取失败返回空列表(降级为手描两点),不报错。
    """
    from core.model3d.axis_line_detector import detect_axis_line_candidates

    row = await db.fetch_one(
        "SELECT file_key FROM drawings WHERE id=:d AND project_id=:p",
        {"d": drawing_id, "p": project_id})
    if row is None:
        raise HTTPException(status_code=404, detail="DRAWING_NOT_FOUND")
    file_key = row["file_key"]
    if not file_key or not str(file_key).lower().endswith(".pdf"):
        return {"drawing_id": drawing_id, "candidates": [], "count": 0}

    import asyncio

    from core.storage import get_file_bytes
    from services.axis_line_memory import fetch_memory, merge_candidates
    from services.title_block_apply import page_size

    loop = asyncio.get_event_loop()
    try:
        data = await loop.run_in_executor(None, get_file_bytes, file_key)
        cands = await loop.run_in_executor(
            None, lambda: detect_axis_line_candidates(data, min_span=min_span))
    except Exception:  # noqa: BLE001 — 取不到候选就退回手描,不阻断标定
        cands = []

    # 补上人工手描过的线:自动检出会漏(点划线淡/被标注遮挡),
    # 记忆让同图重开、同版式换图都不必再描一遍
    size = await page_size(file_key)
    aspect = None
    if size:
        from services.title_block_template import aspect_bucket
        aspect = aspect_bucket(*size)
    remembered = await fetch_memory(
        db, project_id=project_id, drawing_id=drawing_id, page_aspect=aspect)
    merged = merge_candidates(cands, remembered)
    return {"drawing_id": drawing_id, "candidates": merged, "count": len(merged),
            "detected": len(cands), "from_memory": len(merged) - len(cands)}


class AxisLineBody(BaseModel):
    x1_norm: float
    y1_norm: float
    x2_norm: float
    y2_norm: float


class ManualAxisBatchBody(BaseModel):
    lines: list[AxisLineBody]           # 一次选中的多条线
    direction: str                      # x=竖向轴线 | y=横向轴线
    start_label: str                    # 起始轴号
    end_label: str | None = None        # 终止轴号(填了则校验条数)
    direction_order: str                # left_to_right|right_to_left|top_to_bottom|bottom_to_top
    spacing_mm: list[float] | None = None   # 按顺序的相邻轴距(长度 = 条数-1)


@router.post("/{project_id}/drawings/{drawing_id}/manual-axes/batch",
             status_code=201)
async def save_manual_axes_batch(
    project_id: str,
    drawing_id: str,
    body: ManualAxisBatchBody,
    db=Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """一次选多条线 + 起止轴号 + 命名方向 → 自动派轴号并批量入库。

    条数与起止轴号对不上时 400 报错——错配轴号比不标更糟,宁可让人重选。
    """
    if body.direction not in ("x", "y"):
        raise HTTPException(status_code=400, detail="INVALID_DIRECTION")
    from services.axis_label_sequence import assign_labels
    from services.manual_axis import axis_position, save_axis

    lines = [ln.model_dump() for ln in body.lines]
    try:
        refs = assign_labels(
            lines, start=body.start_label, end=body.end_label,
            direction=body.direction, direction_order=body.direction_order,
            spacing_mm=body.spacing_mm)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    skewed = [r["label"] for r in refs if axis_position(r) is None]
    if skewed:
        raise HTTPException(
            status_code=400,
            detail=f"AXIS_NOT_STRAIGHT: 轴线 {','.join(skewed)} 非横平竖直")

    saved = 0
    for ref in refs:
        await save_axis(db, project_id, drawing_id, ref,
                        created_by=str(current_user["id"]))
        saved += 1
    return {"success": True, "error": None, "data": {
        "saved": saved,
        "labels": [r["label"] for r in refs],
    }}


class RelabelAxesBody(BaseModel):
    """把已单条标定的若干轴线合并成一组,按起止轴号 + 方向统一重派。"""
    labels: list[str]                 # 要合并的现有轴号
    direction: str                    # 这些轴线的方向
    start_label: str
    end_label: str | None = None
    direction_order: str


@router.post("/{project_id}/drawings/{drawing_id}/manual-axes/relabel",
             status_code=201)
async def relabel_manual_axes(
    project_id: str,
    drawing_id: str,
    body: RelabelAxesBody,
    db=Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """多选已标定的单条轴线 → 合并成一组统一重新派号。

    单条标定时轴号是一条条填的,难免填错或想改成连续序列;这里选中若干条,
    按命名方向排序后统一重派,**旧轴号先删再写**,避免新旧混存。
    """
    if body.direction not in ("x", "y"):
        raise HTTPException(status_code=400, detail="INVALID_DIRECTION")
    from services.axis_label_sequence import assign_labels
    from services.manual_axis import delete_axis, fetch_drawing_axes, save_axis

    existing = await fetch_drawing_axes(db, drawing_id)
    wanted = {str(x).strip() for x in body.labels}
    picked = [a for a in existing
              if a["direction"] == body.direction and str(a["label"]) in wanted]
    if not picked:
        raise HTTPException(status_code=400,
                            detail="NO_AXES_SELECTED: 选中的轴号在该图中不存在")

    try:
        refs = assign_labels(
            [{k: float(a[k]) for k in
              ("x1_norm", "y1_norm", "x2_norm", "y2_norm")} for a in picked],
            start=body.start_label, end=body.end_label,
            direction=body.direction, direction_order=body.direction_order)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    for axis in picked:                    # 先删旧号,避免新旧混存
        await delete_axis(db, drawing_id, axis["direction"], axis["label"])
    for ref in refs:
        await save_axis(db, project_id, drawing_id, ref,
                        created_by=str(current_user["id"]))
    return {"success": True, "error": None, "data": {
        "relabeled": len(refs), "labels": [r["label"] for r in refs]}}


class MoveAxisBody(BaseModel):
    """把一条已标定的轴线平移到穿过新点(角度不变)。"""
    label: str
    direction: str
    x_norm: float          # 拖到的位置
    y_norm: float


@router.post("/{project_id}/drawings/{drawing_id}/manual-axes/move",
             status_code=201)
async def move_manual_axis(
    project_id: str,
    drawing_id: str,
    body: MoveAxisBody,
    db=Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """平移已标定的轴线(自动识别转存的、人工画的都可以微调)。

    传的是「拖到哪」而非「拖了多远」,避免多次拖动累积误差。
    """
    from services.axis_geometry import move_to
    from services.manual_axis import fetch_drawing_axes, save_axis

    axes = await fetch_drawing_axes(db, drawing_id)
    target = next((a for a in axes if str(a["label"]) == body.label.strip()
                   and a["direction"] == body.direction), None)
    if target is None:
        raise HTTPException(status_code=404, detail="AXIS_NOT_FOUND")

    moved = move_to({k: float(target[k]) for k in
                     ("x1_norm", "y1_norm", "x2_norm", "y2_norm")},
                    body.x_norm, body.y_norm)
    payload = {**dict(target), **moved}
    await save_axis(db, project_id, drawing_id, payload,
                    created_by=str(current_user["id"]))
    return {"success": True, "error": None, "data": moved}


class IntersectionBody(BaseModel):
    """选点定轴:在图上点一个点,写下轴号对,生成竖向 + 横向两条轴线。"""
    label_x: str                       # 竖向轴号,如 1
    label_y: str                       # 横向轴号,如 A
    x_norm: float
    y_norm: float
    angle_x_deg: float = 90.0          # 竖向轴线角度(斜向柱网可改)
    angle_y_deg: float = 0.0           # 横向轴线角度
    world_x: float | None = None       # 工程坐标(米),可空
    world_y: float | None = None
    world_z: float | None = None
    note: str | None = None
    create_axes: bool = True           # 是否同时生成两条轴线


@router.post("/{project_id}/drawings/{drawing_id}/intersections",
             status_code=201)
async def save_axis_intersection(
    project_id: str,
    drawing_id: str,
    body: IntersectionBody,
    db=Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """选点标注交叉点(如 1轴-A轴),并在该点生成竖向 + 横向轴线。

    交叉点的身份是**轴号对**,这正是跨图对齐与整图世界定位的锚点。
    """
    if not body.label_x.strip() or not body.label_y.strip():
        raise HTTPException(status_code=400, detail="LABELS_REQUIRED")
    from services.axis_geometry import line_through_point, orientation
    from services.axis_intersection_repo import save_intersection
    from services.manual_axis import save_axis

    point_id = await save_intersection(
        db, project_id=project_id, drawing_id=drawing_id,
        point=body.model_dump(), created_by=str(current_user["id"]))

    created: list[str] = []
    if body.create_axes:
        for label, angle in ((body.label_x.strip(), body.angle_x_deg),
                             (body.label_y.strip(), body.angle_y_deg)):
            line = line_through_point(body.x_norm, body.y_norm, angle)
            await save_axis(db, project_id, drawing_id, {
                **line, "label": label, "direction": orientation(line),
            }, created_by=str(current_user["id"]))
            created.append(label)
    return {"success": True, "error": None, "data": {
        "intersection_id": point_id, "axes_created": created}}


@router.get("/{project_id}/drawings/{drawing_id}/intersections")
async def list_axis_intersections(
    project_id: str,
    drawing_id: str,
    db=Depends(get_db),
    _user=Depends(get_current_user),
):
    from services.axis_intersection_repo import fetch_drawing_intersections
    points = await fetch_drawing_intersections(db, drawing_id)
    return {"drawing_id": drawing_id, "intersections": points,
            "count": len(points)}


@router.delete("/{project_id}/drawings/{drawing_id}/intersections")
async def delete_axis_intersection(
    project_id: str,
    drawing_id: str,
    label_x: str = Query(...),
    label_y: str = Query(...),
    db=Depends(get_db),
    _user=Depends(get_current_user),
):
    from services.axis_intersection_repo import delete_intersection
    await delete_intersection(db, drawing_id, label_x, label_y)
    return {"success": True, "error": None}


@router.get("/{project_id}/drawings/{drawing_id}/world-anchor")
async def solve_drawing_world_anchor(
    project_id: str,
    drawing_id: str,
    db=Depends(get_db),
    _user=Depends(get_current_user),
):
    """由该图带工程坐标的交叉点解出「图纸 → 工程坐标系」的变换(需求 5)。

    需 ≥2 个填了坐标的交叉点。残差大会标 `suspect`——点配错或轴号重名时
    照样能硬解出一个变换,必须暴露出来。
    """
    from services.axis_intersection_repo import fetch_drawing_intersections
    from services.drawing_anchor import solve_world_transform

    points = await fetch_drawing_intersections(db, drawing_id)
    transform = solve_world_transform(points)
    if transform is None:
        return {"success": False, "error": "NEED_TWO_COORDINATED_POINTS",
                "data": {"coordinated": sum(
                    1 for p in points if p.get("world_x") is not None)}}
    return {"success": True, "error": None, "data": transform}


@router.get("/{project_id}/drawings/{drawing_id}/align-to")
async def align_drawing_to(
    project_id: str,
    drawing_id: str,
    target_drawing_id: str = Query(..., description="要对齐到的参考图纸"),
    db=Depends(get_db),
    _user=Depends(get_current_user),
):
    """用同名交叉点把本图对齐到另一张图的坐标系(需求 4,≥2 个同名交叉点)。"""
    from services.axis_intersection_repo import fetch_drawing_intersections
    from services.drawing_anchor import align_drawings

    src = await fetch_drawing_intersections(db, drawing_id)
    dst = await fetch_drawing_intersections(db, target_drawing_id)
    transform = align_drawings(src, dst)
    if transform is None:
        shared = len({(p["label_x"], p["label_y"]) for p in src}
                     & {(p["label_x"], p["label_y"]) for p in dst})
        return {"success": False, "error": "NEED_TWO_SHARED_INTERSECTIONS",
                "data": {"shared": shared}}
    return {"success": True, "error": None, "data": transform}


class OriginBody(BaseModel):
    discipline: str                    # 图框实读专业(建筑/结构/给排水…)
    drawing_id: str
    intersection_id: str | None = None
    note: str | None = None


@router.post("/{project_id}/coordinate-origins", status_code=201)
async def set_coordinate_origin(
    project_id: str,
    body: OriginBody,
    db=Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """定义某专业的工程坐标原点(0,0,0)在哪张图的哪个交叉点。

    **每个专业各定义一次**:各专业图纸常用不同局部原点,共用一个会整体错位。
    """
    from services.axis_intersection_repo import set_origin
    origin_id = await set_origin(
        db, project_id=project_id, discipline=body.discipline.strip(),
        drawing_id=body.drawing_id, intersection_id=body.intersection_id,
        note=body.note, created_by=str(current_user["id"]))
    return {"success": True, "error": None, "data": {"id": origin_id}}


@router.get("/{project_id}/coordinate-origins")
async def list_coordinate_origins(
    project_id: str,
    db=Depends(get_db),
    _user=Depends(get_current_user),
):
    """各专业原点定义 + 还没定义的专业清单(缺一个就整体错位,必须点名)。"""
    from services.axis_intersection_repo import list_origins
    return await list_origins(db, project_id)


@router.delete("/{project_id}/drawings/{drawing_id}/manual-axes")
async def delete_manual_axis(
    project_id: str,
    drawing_id: str,
    direction: str = Query(...),
    label: str = Query(...),
    db=Depends(get_db),
    _user=Depends(get_current_user),
):
    from services.manual_axis import delete_axis
    await delete_axis(db, drawing_id, direction, label)
    return {"success": True, "error": None}


@router.post("/{project_id}/drawings/{drawing_id}/manual-axes/derive-scale",
             status_code=201)
async def derive_scale_from_manual_axes(
    project_id: str,
    drawing_id: str,
    db=Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """由人工标定的**相邻轴线实际轴距**反算比例尺并落坐标变换。

    比从图纸文字读 `1:N` 更可靠——直接量的是图上距离与实物距离的比值。
    需至少一条轴线填了 `spacing_to_prev_mm`。
    """
    from services.manual_axis import fetch_drawing_axes, scale_from_spacing
    from services.drawing_transform import (
        TRANSFORM_SOURCE_MANUAL, DrawingTransform, persist_transform,
    )

    row = await db.fetch_one(
        "SELECT file_key FROM drawings WHERE id=:d AND project_id=:p",
        {"d": drawing_id, "p": project_id})
    if row is None:
        raise HTTPException(status_code=404, detail="DRAWING_NOT_FOUND")
    page_h = await _pdf_page_height(row["file_key"])
    if not page_h:
        raise HTTPException(status_code=422, detail="PAGE_SIZE_UNAVAILABLE")

    refs = await fetch_drawing_axes(db, drawing_id)
    got = scale_from_spacing(refs, page_h)
    if got is None:
        raise HTTPException(
            status_code=400,
            detail="NO_SPACING: 需至少两条同向轴线且填写相邻轴距(mm)")

    # 原点取标定轴线的最小位置(与 pt_to_meter 同口径)
    from services.manual_axis import axis_position
    xs = [axis_position(r) * page_h for r in refs
          if r["direction"] == "x" and axis_position(r) is not None]
    ys = [page_h - axis_position(r) * page_h for r in refs
          if r["direction"] == "y" and axis_position(r) is not None]
    await persist_transform(db, project_id=project_id, drawing_id=drawing_id,
        transform=DrawingTransform(
            scale_m_pt=got["scale_m_pt"],
            origin_x=min(xs) if xs else 0.0,
            origin_y=min(ys) if ys else 0.0,
            # **同一个「0 兜底」缺陷**（见 element_recognizer._min_labeled_pos）：
            # 人只标了一个方向的基准线时，另一方向的 0 不是「原点在 0」
            # 而是「没标」。标出来，下游才分得清。
            origin_x_estimated=not xs,
            origin_y_estimated=not ys,
            page_h=float(page_h),
            confidence=1.0,           # 人工量定 → 满置信
            source=TRANSFORM_SOURCE_MANUAL))
    return {"success": True, "data": {**got, "page_h": page_h}, "error": None}
