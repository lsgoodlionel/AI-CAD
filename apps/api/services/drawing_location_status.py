"""未分层图的定位状态汇总 —— 供**图纸管理页**按类处理。纯函数。

`unzoned_reason` 判出了每张图为什么定位不了，但那个结果此前只写进
`scene.quality.unclassified_drawings`，只有工程模型页读得到；
而人是在**图纸管理**里看图的。本模块把它整理成图纸管理页要的形态：
按原因分组计数 + 每张图带建议动作，让人成批处理而不是逐张翻。

**待办数与总数分开报**（`actionable` vs `total`）：说明、目录、系统图
本就没有楼层，把它们混进待办会让人去处理一个不存在的问题 ——
`building_unit_fallback` 那轮原报「1866 张未分配」，拆开后真正要处理的
只有 907 张，**虚高 2.1 倍**。
"""
from __future__ import annotations

from typing import Any

from services.unzoned_reason import classify_unzoned


def summarize_location_status(
    unclassified: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """未分层清单 → {items, by_reason, total, actionable}。

    `items` 保留原有字段（图号/图名/单体）并补上 `reason`/`action`/
    `needs_floor_input`/`hint`；`actionable` 只数**真正需要人补楼层**的。
    """
    items: list[dict[str, Any]] = []
    by_reason: dict[str, int] = {}
    actionable = 0

    for entry in unclassified or []:
        item = dict(entry)
        classified = classify_unzoned(item)
        item.update(classified.as_dict())
        items.append(item)
        by_reason[classified.reason] = by_reason.get(classified.reason, 0) + 1
        if classified.needs_floor_input:
            actionable += 1

    return {
        "items": items,
        "by_reason": by_reason,
        # 总数照实报，不藏 —— 藏了人会以为图纸少了
        "total": len(items),
        # 待办只数要人动手的那部分
        "actionable": actionable,
    }
