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
       dei.confidence, dei.extraction_version,
       d.drawing_no, d.title AS drawing_title, d.discipline
FROM drawing_extracted_info dei
JOIN drawings d ON d.id = dei.drawing_id
WHERE dei.project_id = :project_id {where}
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
        _ITEMS_COUNT_SQL.format(where=where), params
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
    db=Depends(get_db),
    _user=Depends(get_current_user),
):
    """触发全项目工程信息重抽(异步,Celery default 队列)。"""
    exists = await db.fetch_one(_PROJECT_EXISTS_SQL, {"project_id": project_id})
    if exists is None:
        raise HTTPException(status_code=404, detail="PROJECT_NOT_FOUND")
    async_result = extract_project_drawing_info.delay(project_id)
    return {"task_id": str(async_result.id), "project_id": project_id}
