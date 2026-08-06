"""轴线交叉点与工程坐标原点的仓储层。

交叉点做两件事:
- **选点定轴**:点一个点、写下轴号对(如 `1轴-A轴`),同时生成竖向 + 横向两条轴线;
- **整图定位**:同名交叉点跨图对齐(需求 4);带工程坐标的交叉点解出整图的世界变换(需求 5)。

原点(0,0,0)**按专业分别定义**:各专业图纸常用不同局部原点,共用一个会整体错位。
"""
from __future__ import annotations

from typing import Any

_UPSERT_SQL = """
INSERT INTO axis_intersections
    (project_id, drawing_id, label_x, label_y, x_norm, y_norm,
     world_x, world_y, world_z, note, created_by)
VALUES (CAST(:project_id AS uuid), CAST(:drawing_id AS uuid), :label_x, :label_y,
        :x, :y, :wx, :wy, :wz, :note, CAST(:created_by AS uuid))
ON CONFLICT (drawing_id, label_x, label_y) DO UPDATE SET
    x_norm = EXCLUDED.x_norm, y_norm = EXCLUDED.y_norm,
    world_x = EXCLUDED.world_x, world_y = EXCLUDED.world_y,
    world_z = EXCLUDED.world_z, note = EXCLUDED.note, updated_at = now()
RETURNING id
"""

_FETCH_DRAWING_SQL = """
SELECT id, label_x, label_y, x_norm, y_norm, world_x, world_y, world_z, note
FROM axis_intersections WHERE drawing_id = CAST(:drawing_id AS uuid)
ORDER BY label_x, label_y
"""

_FETCH_PROJECT_SQL = """
SELECT drawing_id, id, label_x, label_y, x_norm, y_norm,
       world_x, world_y, world_z
FROM axis_intersections WHERE project_id = CAST(:project_id AS uuid)
"""

_DELETE_SQL = """
DELETE FROM axis_intersections
WHERE drawing_id = CAST(:drawing_id AS uuid)
  AND label_x = :label_x AND label_y = :label_y
"""

_ORIGIN_UPSERT_SQL = """
INSERT INTO project_coordinate_origins
    (project_id, discipline, drawing_id, intersection_id, note, created_by)
VALUES (CAST(:project_id AS uuid), :discipline, CAST(:drawing_id AS uuid),
        CAST(:intersection_id AS uuid), :note, CAST(:created_by AS uuid))
ON CONFLICT (project_id, discipline) DO UPDATE SET
    drawing_id = EXCLUDED.drawing_id,
    intersection_id = EXCLUDED.intersection_id,
    note = EXCLUDED.note
RETURNING id
"""

_ORIGIN_LIST_SQL = """
SELECT o.discipline, o.drawing_id, o.intersection_id, o.note,
       d.drawing_no, d.title,
       i.label_x, i.label_y, i.world_x, i.world_y, i.world_z
FROM project_coordinate_origins o
LEFT JOIN drawings d ON d.id = o.drawing_id
LEFT JOIN axis_intersections i ON i.id = o.intersection_id
WHERE o.project_id = CAST(:project_id AS uuid)
ORDER BY o.discipline
"""

#: 项目里有图纸的各专业(提示「哪些专业还没定义原点」)。
#: 只取**图框实读专业**——`discipline` 是粗粒度兜底枚举(architecture/mep…),
#: 混进来会让清单出现「structure 和 结构」这种同义重复。
_DISCIPLINES_SQL = """
SELECT discipline_label AS discipline, COUNT(*) AS n
FROM drawings WHERE project_id = CAST(:project_id AS uuid)
  AND discipline_label IS NOT NULL
GROUP BY discipline_label ORDER BY n DESC
"""


def _row(r: Any) -> dict:
    d = dict(r)
    for key in ("x_norm", "y_norm"):
        if d.get(key) is not None:
            d[key] = float(d[key])
    for key in ("world_x", "world_y", "world_z"):
        if d.get(key) is not None:
            d[key] = float(d[key])
    if d.get("id") is not None:
        d["id"] = str(d["id"])
    if d.get("drawing_id") is not None:
        d["drawing_id"] = str(d["drawing_id"])
    return d


async def save_intersection(
    db: Any, *, project_id: str, drawing_id: str, point: dict,
    created_by: str | None,
) -> str | None:
    """保存/更新一个交叉点(同图同轴号对幂等覆盖)。"""
    row = await db.fetch_one(_UPSERT_SQL, {
        "project_id": project_id, "drawing_id": drawing_id,
        "label_x": str(point["label_x"]).strip(),
        "label_y": str(point["label_y"]).strip(),
        "x": float(point["x_norm"]), "y": float(point["y_norm"]),
        "wx": point.get("world_x"), "wy": point.get("world_y"),
        "wz": point.get("world_z"), "note": point.get("note"),
        "created_by": created_by})
    return str(row["id"]) if row is not None else None


async def fetch_drawing_intersections(db: Any, drawing_id: str) -> list[dict]:
    return [_row(r) for r in await db.fetch_all(
        _FETCH_DRAWING_SQL, {"drawing_id": drawing_id})]


async def fetch_project_intersections(db: Any, project_id: str) -> dict[str, list[dict]]:
    """全项目交叉点,按 drawing_id 分组(跨图对齐用)。"""
    out: dict[str, list[dict]] = {}
    for r in await db.fetch_all(_FETCH_PROJECT_SQL, {"project_id": project_id}):
        d = _row(r)
        out.setdefault(d["drawing_id"], []).append(d)
    return out


async def delete_intersection(
    db: Any, drawing_id: str, label_x: str, label_y: str,
) -> None:
    await db.execute(_DELETE_SQL, {
        "drawing_id": drawing_id, "label_x": label_x, "label_y": label_y})


async def set_origin(
    db: Any, *, project_id: str, discipline: str, drawing_id: str,
    intersection_id: str | None, note: str | None, created_by: str | None,
) -> str | None:
    """定义某专业的工程坐标原点(0,0,0)所在图纸与交叉点。"""
    row = await db.fetch_one(_ORIGIN_UPSERT_SQL, {
        "project_id": project_id, "discipline": discipline,
        "drawing_id": drawing_id, "intersection_id": intersection_id,
        "note": note, "created_by": created_by})
    return str(row["id"]) if row is not None else None


async def list_origins(db: Any, project_id: str) -> dict:
    """各专业原点定义 + **还没定义的专业清单**(缺一个专业就整体错位,必须点名)。"""
    defined = [_row(r) for r in await db.fetch_all(
        _ORIGIN_LIST_SQL, {"project_id": project_id})]
    rows = await db.fetch_all(_DISCIPLINES_SQL, {"project_id": project_id})
    counts = {r["discipline"]: int(r["n"]) for r in rows if r["discipline"]}
    done = {d["discipline"] for d in defined}
    # 按图纸张数降序:先定义图多的专业收益最大
    missing = [{"discipline": k, "drawings": v}
               for k, v in counts.items() if k not in done]
    return {"origins": defined, "missing_disciplines": missing,
            "defined": len(done), "total_disciplines": len(counts)}
