"""人工标注事件记录:学习闭环的燃料。

`auto_value`(系统当时的自动值)与 `human_value`(人给的值)的**差异**就是学习信号:
- auto 为空、human 有值 → 系统没认出来(覆盖缺口);
- 两者不同        → 系统认错了(准确性缺口);
- 两者相同        → 人只是确认,不构成学习信号。

记录本身**绝不能影响主流程**:任何异常都吞掉——学习是辅助,不该因为记日志失败
而让人工标注白做。
"""
from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

_INSERT_SQL = """
INSERT INTO annotation_events
    (project_id, drawing_id, kind, field, auto_value, human_value,
     context_json, created_by)
VALUES (CAST(:project_id AS uuid), CAST(:drawing_id AS uuid), :kind, :field,
        :auto, :human, CAST(:context AS jsonb), CAST(:created_by AS uuid))
"""


async def record(
    db: Any, *, project_id: str, drawing_id: str | None, kind: str,
    human_value: str | None, auto_value: str | None = None,
    field: str | None = None, context: dict | None = None,
    created_by: str | None = None,
) -> None:
    """记一条标注事件(best-effort,失败静默)。"""
    try:
        await db.execute(_INSERT_SQL, {
            "project_id": project_id, "drawing_id": drawing_id, "kind": kind,
            "field": field, "auto": auto_value, "human": human_value,
            "context": json.dumps(context or {}, ensure_ascii=False),
            "created_by": created_by})
    except Exception as exc:  # noqa: BLE001 — 学习是辅助,不能拖累主流程
        logger.debug("[annotation_events] 记录跳过: %s", exc)
