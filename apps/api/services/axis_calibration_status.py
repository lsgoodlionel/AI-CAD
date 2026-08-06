"""轴线标定进度(人工标定基准的统一入口数据源)。

**解决的问题**:标定入口原本只藏在「工程信息 → 明细某一行 → 预览弹窗 → 底部按钮」,
找不到。本模块给出「哪些图该标、标了几条」的清单,让工程信息页有一个显式面板,
人审工作台也能列出这项待办。

优先级:**平面图优先**——轴网画在平面图上,剖面/立面/详图上的轴线是投影引用,
先标平面图才立得住参考系。
"""
from __future__ import annotations

from typing import Any

#: 平面图标题关键词(drawings 表无 view_type 列,按图名判别,与既有做法一致)
PLAN_KEYWORDS = ("平面", "布置图")
#: 建立参考系建议的最少轴线条数(一个方向至少两条才能定间距/反算比例尺)
SUGGESTED_MIN_AXES = 4


def is_plan_title(title: str | None) -> bool:
    """图名是否像平面图——轴网画在平面图上,标定优先。"""
    return bool(title) and any(k in title for k in PLAN_KEYWORDS)


def calibration_state(axis_count: int) -> str:
    """已标条数 → 状态:none(未标)| partial(不足以定参考系)| ready。"""
    if axis_count <= 0:
        return "none"
    return "partial" if axis_count < SUGGESTED_MIN_AXES else "ready"


def prioritize(rows: list[dict]) -> list[dict]:
    """排序:未标的平面图最前,其次部分标定,已就绪的沉底(不改原列表)。"""
    state_rank = {"none": 0, "partial": 1, "ready": 2}

    def key(r: dict) -> tuple:
        state = calibration_state(int(r.get("axis_count") or 0))
        return (state_rank[state], 0 if is_plan_title(r.get("title")) else 1,
                str(r.get("drawing_no") or ""))

    return sorted(rows, key=key)


_LIST_SQL = """
SELECT d.id AS drawing_id, d.drawing_no, d.title, d.discipline,
       count(m.id) AS axis_count
FROM drawings d
LEFT JOIN manual_axis_references m ON m.drawing_id = d.id
WHERE d.project_id = :project_id
GROUP BY d.id, d.drawing_no, d.title, d.discipline
"""

_PENDING_SQL = """
SELECT count(*) AS n FROM drawings d
WHERE d.project_id = :project_id
  AND (d.title LIKE '%平面%' OR d.title LIKE '%布置图%')
  AND NOT EXISTS (SELECT 1 FROM manual_axis_references m WHERE m.drawing_id = d.id)
"""


async def list_calibration_status(
    db: Any, project_id: str, *, plan_only: bool = True,
    page: int = 1, page_size: int = 20,
) -> dict:
    """图纸标定进度清单(未标平面图优先)。"""
    rows = [dict(r) for r in await db.fetch_all(_LIST_SQL, {"project_id": project_id})]
    if plan_only:
        rows = [r for r in rows if is_plan_title(r.get("title"))]
    ordered = prioritize(rows)
    start = max(page - 1, 0) * page_size
    page_rows = [
        {**r, "axis_count": int(r.get("axis_count") or 0),
         "state": calibration_state(int(r.get("axis_count") or 0))}
        for r in ordered[start:start + page_size]
    ]
    return {"total": len(ordered), "page": page, "page_size": page_size,
            "items": page_rows}


async def pending_calibration_count(db: Any, project_id: str) -> int:
    """尚未标定任何轴线的平面图数(人审工作台待办计数)。"""
    row = await db.fetch_one(_PENDING_SQL, {"project_id": project_id})
    return int(dict(row).get("n") or 0) if row else 0
