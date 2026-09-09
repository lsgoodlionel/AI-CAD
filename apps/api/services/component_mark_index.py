"""构件编号 → 图纸的索引（按编号追溯）。

**来源**：会审 133 条检查项里「定位信息是否完整」要求人能答出
「问题具体对应哪张图、哪个部位」，而常见冲突正是
「只有疑问没有坐标，导致无法核图、无法追责、无法复核」。

**实测**：大歌剧院 587 个构件编号里 214 个（36%）跨多图出现，
`M1124` 出现在 84 张图上 —— 一个构件的信息本就分散在多图里。

平法图集不另出柱表梁表（配筋直接标在平面图上，这正是「平面整体表示」
的含义），所以关联形态不是「平面图 → 构件表」，
而是**同一编号在多图间的共现**。
"""
from __future__ import annotations

from typing import Any, Iterable

from core.model3d.component_mark import parse_component_mark


def build_mark_index(rows: Iterable[dict] | None) -> dict[str, dict]:
    """档案行 → `{编号: {kind, drawings, floors, titles}}`。

    `rows` 每项需有 `content` 与 `drawing_id`，可选 `title` / `floor_key` /
    `discipline`。非构件编号（材料牌号、说明文字）一律跳过 ——
    **判不出就不收**。

    **专业必须传下去**：机电图上有大量与平法代号同形的编号
    （实测 `LN1`~`LN14` 共 1144 次全在配电系统图上，是照明回路号），
    不传专业就会把它们统统收成「受扭非框架梁」。
    """
    index: dict[str, dict] = {}
    for row in rows or ():
        if not isinstance(row, dict):
            continue
        mark = parse_component_mark(row.get("content"),
                                    discipline=row.get("discipline"))
        if mark is None:
            continue
        entry = index.setdefault(mark.raw, {
            "kind": mark.kind, "code": mark.code,
            "drawings": set(), "floors": set(), "titles": set(),
        })
        drawing_id = str(row.get("drawing_id") or "")
        if drawing_id:
            entry["drawings"].add(drawing_id)
        floor = row.get("floor_key")
        if floor:
            entry["floors"].add(str(floor))
        title = row.get("title")
        if title:
            entry["titles"].add(str(title))
    return index


def mark_summary(index: dict[str, dict] | None, limit: int = 100) -> list[dict]:
    """索引 → 清单，**跨图多的排前面**（那是最需要并起来看的）。"""
    out = [
        {
            "mark": mark,
            "kind": entry.get("kind"),
            "drawing_count": len(entry.get("drawings") or ()),
            "floor_count": len(entry.get("floors") or ()),
            "drawings": sorted(entry.get("drawings") or ()),
            "floors": sorted(entry.get("floors") or ()),
        }
        for mark, entry in (index or {}).items()
    ]
    out.sort(key=lambda item: (-item["drawing_count"], item["mark"]))
    return out[:limit]
