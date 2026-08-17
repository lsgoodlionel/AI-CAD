"""人工手描轴线的位置记忆(免得同图/同版式重复标注)。

**场景**:候选线抽取(HoughLinesP)会漏——点划线太淡、被标注遮挡、跨栏断开。
人手描了一条,若不记住,同一张图重开一次、或换一张同版式图纸,还得再描一遍。

**与 `manual_axis_references` 的分工**(分开存是因为语义不同):

| | 存什么 | 用途 |
|---|---|---|
| `manual_axis_references` | **已命名**的轴线基准(带轴号) | 直接进建模参考系——**结论** |
| `axis_line_memory` | **未命名**的线位置 | 补进候选线供人点选——**线索** |

**复用范围**:同一张图必然复用;同版式(页面宽高比同桶)图纸也复用——
同一套图的轴网位置往往一致,这正是「同类图纸不必重复标注」的依据。
记忆只是**补候选**,不会自动变成轴线基准,故误补的代价仅是多一条灰线。
"""
from __future__ import annotations

from typing import Any

#: 与已有候选线去重的距离阈值(归一化):比这更近就认为是同一条
DEDUPE_TOLERANCE = 0.006


def line_position(line: dict) -> float:
    """线的位置坐标:竖线取 x 中点,横线取 y 中点。"""
    if line.get("direction") == "x":
        return (float(line["x1_norm"]) + float(line["x2_norm"])) / 2
    return (float(line["y1_norm"]) + float(line["y2_norm"])) / 2


def is_duplicate(
    line: dict, existing: list[dict], tol: float = DEDUPE_TOLERANCE,
) -> bool:
    """该线是否与已有候选/记忆重复(同方向且位置接近)。"""
    pos = line_position(line)
    return any(
        e.get("direction") == line.get("direction")
        and abs(line_position(e) - pos) <= tol
        for e in existing
    )


def merge_candidates(
    detected: list[dict], remembered: list[dict], tol: float = DEDUPE_TOLERANCE,
) -> list[dict]:
    """自动检出的候选 + 记忆里的线 → 合并去重(不改入参)。

    记忆线带 `from_memory=True`,前端可用不同颜色标出「这条是以前人标过的」。
    """
    merged = [dict(d) for d in detected]
    for line in remembered:
        if is_duplicate(line, merged, tol):
            continue
        merged.append({**line, "from_memory": True})
    return merged


# ── 仓储 ─────────────────────────────────────────────────────────

_INSERT_SQL = """
INSERT INTO axis_line_memory
    (project_id, drawing_id, direction, x1_norm, y1_norm, x2_norm, y2_norm,
     page_aspect, created_by)
VALUES (CAST(:project_id AS uuid), CAST(:drawing_id AS uuid), :direction,
        :x1, :y1, :x2, :y2, :aspect, CAST(:created_by AS uuid))
RETURNING id
"""

#: 同图优先,其次同版式(宽高比同桶);同版式的按命中次数排序
_FETCH_SQL = """
SELECT id, direction, x1_norm, y1_norm, x2_norm, y2_norm,
       (drawing_id = CAST(:drawing_id AS uuid)) AS same_drawing
FROM axis_line_memory
WHERE project_id = CAST(:project_id AS uuid)
  AND (drawing_id = CAST(:drawing_id AS uuid)
       OR (CAST(:aspect AS real) IS NOT NULL AND page_aspect IS NOT NULL
           AND abs(page_aspect - CAST(:aspect AS real)) < 0.02))
ORDER BY same_drawing DESC, hit_count DESC
LIMIT 200
"""

_BUMP_SQL = """
UPDATE axis_line_memory SET hit_count = hit_count + 1
WHERE id = CAST(:id AS uuid)
"""


async def remember_line(
    db: Any, *, project_id: str, drawing_id: str, line: dict,
    page_aspect: float | None, created_by: str | None,
) -> str | None:
    """记住一条人工手描的轴线位置。已有近似记忆则跳过(不重复堆积)。"""
    existing = await fetch_memory(
        db, project_id=project_id, drawing_id=drawing_id, page_aspect=page_aspect)
    if is_duplicate(line, existing):
        return None
    row = await db.fetch_one(_INSERT_SQL, {
        "project_id": project_id, "drawing_id": drawing_id,
        "direction": str(line["direction"]),
        "x1": float(line["x1_norm"]), "y1": float(line["y1_norm"]),
        "x2": float(line["x2_norm"]), "y2": float(line["y2_norm"]),
        "aspect": page_aspect, "created_by": created_by})
    return str(row["id"]) if row is not None else None


async def fetch_memory(
    db: Any, *, project_id: str, drawing_id: str, page_aspect: float | None,
) -> list[dict]:
    """取该图可用的轴线记忆(同图 + 同版式)。"""
    rows = await db.fetch_all(_FETCH_SQL, {
        "project_id": project_id, "drawing_id": drawing_id, "aspect": page_aspect})
    return [{
        "id": str(r["id"]), "direction": r["direction"],
        "x1_norm": float(r["x1_norm"]), "y1_norm": float(r["y1_norm"]),
        "x2_norm": float(r["x2_norm"]), "y2_norm": float(r["y2_norm"]),
        "same_drawing": bool(r["same_drawing"]),
    } for r in rows]


async def bump_hit(db: Any, memory_id: str) -> None:
    """记忆被采用一次——用得越多在同版式里排得越前。"""
    await db.execute(_BUMP_SQL, {"id": memory_id})
