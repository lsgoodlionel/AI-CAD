"""待确认分区的**解锁价值**排序。

分区号必须人工确认是**有意设计**（§8.0.5 的分区号无法从图内推导，
三个分区各自从 1 开始，不确认就撞身份）。但「先确认哪张」不该靠人翻 ——
实测大歌剧院 800+ 张待确认里，只有 **13 张**卡着 5 个楼层的 4064 根柱。

排序口径：
- `unlocks`：确认后能获得轴网的构件数，**按楼层去重**
  （一层的柱数不因用了 5 张图就算 5 遍 —— 高估投入产出比比不排序更糟）
- `effort`：要点几次（分区数）
"""
from __future__ import annotations

from typing import Any


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def rank_pending_zones(rows: list[dict] | None) -> list[dict]:
    """待确认图纸 → 按解锁价值降序的清单。

    `rows` 每项：`drawing_id` / `title` / `floor_key` / `component_count`
    / `zone_count`。脏数据按 0 计，不抛异常 —— 这是给人看的清单，
    少一行胜过整页 500。
    """
    if not rows:
        return []

    # **同层去重**：楼层的构件数只算一次，均摊到该层的各张待确认图上
    per_floor: dict[str, list[dict]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        per_floor.setdefault(str(row.get("floor_key") or ""), []).append(row)

    ranked: list[dict] = []
    for floor_key, floor_rows in per_floor.items():
        total = max((_int(r.get("component_count")) for r in floor_rows),
                    default=0)
        share = total // len(floor_rows) if floor_rows else 0
        remainder = total - share * len(floor_rows)
        for index, row in enumerate(floor_rows):
            ranked.append({
                "drawing_id": str(row.get("drawing_id") or ""),
                "title": str(row.get("title") or ""),
                "floor_key": floor_key,
                # 首张多背余数，保证同层合计 == 该层构件数
                "unlocks": share + (remainder if index == 0 else 0),
                "effort": _int(row.get("zone_count")),
            })
    ranked.sort(key=lambda r: (-r["unlocks"], r["effort"], r["drawing_id"]))
    return ranked


def pending_rows_from_scene(scene: dict | None,
                            pending: dict[str, dict]) -> list[dict]:
    """scene 的楼层构件数 × 识别表的待确认分区 → `rank_pending_zones` 的输入。

    **构件数只能从 scene 取** —— 识别表不知道自己那张图被哪层用了，
    而「解锁多少构件」正是按层算的。

    已有轴网的层直接跳过：它不需要人再确认什么。
    """
    floors = (scene or {}).get("floors") or []
    rows: list[dict] = []
    for floor in floors:
        if not isinstance(floor, dict):
            continue
        axes = floor.get("axes")
        if axes and (axes.get("x") or axes.get("y")):
            continue                      # 已有轴网，不必排队
        elements = floor.get("elements") or {}
        srcs: dict[str, int] = {}
        count = 0
        for kind in ("columns", "walls", "beams", "slabs"):
            for item in elements.get(kind) or []:
                count += 1
                src = str((item or {}).get("src") or "")
                if src:
                    srcs[src] = srcs.get(src, 0) + 1
        for src in srcs:
            info = pending.get(src)
            if not info:
                continue
            rows.append({
                "drawing_id": src,
                "title": info.get("title") or "",
                "floor_key": str(floor.get("key") or ""),
                "component_count": count,
                "zone_count": info.get("zone_count") or 0,
            })
    return rows
