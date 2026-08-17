"""人审任务聚合(统一工作台数据源)。

**解决的问题**:人审入口散落在 6 处(比例尺确认在工程信息页、构件核对在模型页审校
模式、标高录入在另一面板、追溯在预览弹窗…),用户「找不到方便的入口做审核修正复核」。
本模块把全部待办聚成一个清单:每项含**数量、价值说明、跳转目标**,按价值排序。

排序依据(为什么先做哪个):
1. 可疑变换复核 —— 错的坐标变换让构件位置全错,**修一张见效一张**,优先级最高;
2. 比例尺确认  —— 解锁坐标变换,一次确认精确到位,连带解锁回投核对与金标签;
3. 构件核对    —— 逐个确认构件,驱动准确率单调上升(飞轮);
4. 楼层标高    —— 竖向真实性,影响整层构件高程。
"""
from __future__ import annotations

from typing import Any

_SUSPECT_SCALE_SQL = """
SELECT t.drawing_id, t.scale_m_pt
FROM drawing_transform t
WHERE t.project_id = :project_id
"""

_SCALE_PENDING_SQL = """
SELECT count(DISTINCT e.drawing_id) AS n
FROM drawing_extracted_info e
LEFT JOIN drawing_transform t ON t.drawing_id = e.drawing_id
WHERE e.project_id = :project_id AND e.is_active AND t.drawing_id IS NULL
  AND e.content ~ '1[:：]\\s*[0-9]{1,4}'
"""

_COMPONENT_PENDING_SQL = """
SELECT count(*) AS n FROM component_instances
WHERE project_id = :project_id AND review_state = 'conflict'
  AND model_version = (SELECT version FROM project_models WHERE project_id = :project_id)
"""

_ARCHIVE_PENDING_SQL = """
SELECT count(*) AS n FROM drawing_extracted_info
WHERE project_id = :project_id AND is_active AND source_kind = 'auto'
  AND category IN ('elevation', 'axis') AND confidence < 0.7
"""


async def collect_review_tasks(db: Any, project_id: str) -> list[dict]:
    """聚合各类待人审任务(按价值降序)。任一项失败不影响其余(逐项 guarded)。"""
    tasks: list[dict] = []

    async def _count(sql: str) -> int:
        try:
            row = await db.fetch_one(sql, {"project_id": project_id})
            return int(dict(row).get("n") or 0) if row else 0
        except Exception:  # noqa: BLE001 — 单项失败记 0,不影响其余
            return 0

    # 1) 可疑变换复核(需在 Python 侧判定标准比例尺)
    suspect = 0
    try:
        from services.scale_candidates import assess_existing_scale
        rows = await db.fetch_all(_SUSPECT_SCALE_SQL, {"project_id": project_id})
        suspect = sum(
            1 for r in rows
            if not assess_existing_scale(float(r["scale_m_pt"]))["is_standard"])
    except Exception:  # noqa: BLE001
        suspect = 0
    tasks.append({
        "key": "suspect_scale",
        "title": "复核可疑比例尺",
        "count": suspect,
        "why": "坐标变换算错会让整张图的构件位置全错,修一张见效一张",
        "route": "/project-info/{project_id}",
        "anchor": "scale-confirm",
        "severity": "high",
    })

    tasks.append({
        "key": "scale_pending",
        "title": "确认图纸比例尺",
        "count": await _count(_SCALE_PENDING_SQL),
        "why": "图上已写明 1:N,一键确认即精确建立坐标变换,连带解锁图上回投核对与金标签",
        "route": "/project-info/{project_id}",
        "anchor": "scale-confirm",
        "severity": "high",
    })

    # 轴线标定:自动轴号识别撞到 OCR 上限,人标少量基准即可绕开
    axis_pending = 0
    try:
        from services.axis_calibration_status import pending_calibration_count
        axis_pending = await pending_calibration_count(db, project_id)
    except Exception:  # noqa: BLE001 — 单项失败记 0,不影响其余
        axis_pending = 0
    tasks.append({
        "key": "axis_calibration",
        "title": "标定轴线基准",
        "count": axis_pending,
        "why": "自动轴号识别受 OCR 限制不可用;人标少量基准即为系统建立参考系,并可按轴距反算精确比例尺",
        "route": "/project-info/{project_id}",
        "anchor": "axis-calibration",
        "severity": "high",
    })

    tasks.append({
        "key": "component_review",
        "title": "核对低置信构件",
        "count": await _count(_COMPONENT_PENDING_SQL),
        "why": "逐个确认/否定/改类,模型准确率单调上升(人审飞轮)",
        "route": "/model/{project_id}",
        "anchor": "review-mode",
        "severity": "medium",
    })

    tasks.append({
        "key": "story_height",
        "title": "校正楼层标高/层高",
        "count": await _count(_ARCHIVE_PENDING_SQL),
        "why": "竖向真实性:标高错则整层构件高程全错;可参考图纸建议标高录入",
        "route": "/model/{project_id}",
        "anchor": "story-height",
        "severity": "medium",
    })

    order = {"high": 0, "medium": 1, "low": 2}
    return sorted(tasks, key=lambda t: (order.get(t["severity"], 9), -t["count"]))
