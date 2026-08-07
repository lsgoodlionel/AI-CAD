"""交点传播 —— 把匹配上的图落成世界锚点（J1 收尾，`placements` 的输入）。

链路：轴距序列匹配 → 局部轴线的锚图轴号 → 锚图同名轴号对的世界坐标
→ 写 ``axis_intersections`` → `placements_for_project` 求解该图的世界摆放。

**实测天花板**：143 张匹配成功的图里只有 **12 张双向**（131 张单向）。
一个交点要 x、y 两个轴号，单向拿不到世界坐标。
降低 `MIN_MATCH_GAPS` 换不来多少：门槛 4 时双向仍是 12，门槛 3 时升到 20
但歧义组从 13 涨到 96 —— 3 段轴距（4 条轴线）辨识度太低，
「碰巧唯一」的概率上升，而歧义判定挡不住碰巧唯一。

⇒ 要继续扩大**只能增加锚图**（人工确认分区号），不是调阈值。
"""
from __future__ import annotations

import json
import logging
from typing import Any

from services.anchor_label_mapping import anchor_labels_for_local_axes
from services.axis_sequence_match import match_against_anchors
from services.axis_zone_propagation import (
    MIN_REAL_GAP_M, SCALE_RATIO_REVIEW_THRESHOLD,
)
from services.drawing_anchor import apply_similarity, solve_world_transform

logger = logging.getLogger(__name__)

#: 传播来的交点在 `note` 里标明来源与锚图 —— 与 `auto:coord_annotation`
#: （从坐标标注直接读出的真锚点）区分开，误传播可回溯。
NOTE_PREFIX = "auto:sequence_match"

_FETCH_AXES_SQL = """
SELECT r.drawing_id, r.axes, r.page_w, r.page_h, t.scale_m_pt
FROM axis_recognition r
JOIN drawing_transform t ON t.drawing_id = r.drawing_id
WHERE r.project_id = CAST(:project_id AS uuid)
  AND r.status = 'ready' AND r.suspect_symbol_field = false
  AND r.axes IS NOT NULL
ORDER BY r.drawing_id
"""

_FETCH_ANCHOR_POINTS_SQL = """
SELECT label_x, label_y, x_norm, y_norm, world_x, world_y, world_z
FROM axis_intersections
WHERE drawing_id = CAST(:drawing_id AS uuid)
  AND label_x <> '' AND label_y <> ''
"""

_UPSERT_SQL = """
INSERT INTO axis_intersections
    (project_id, drawing_id, label_x, label_y, x_norm, y_norm,
     world_x, world_y, note)
VALUES (CAST(:project_id AS uuid), CAST(:drawing_id AS uuid),
        :label_x, :label_y, :x_norm, :y_norm, :world_x, :world_y, :note)
"""

_CLEAR_SQL = """
DELETE FROM axis_intersections
WHERE drawing_id = CAST(:drawing_id AS uuid) AND note LIKE :prefix
"""


def _axis_rows(axes: Any) -> list[dict]:
    parsed = json.loads(axes) if isinstance(axes, (str, bytes)) else axes
    return [a for a in (parsed or []) if a.get("offset_pt") is not None]


def _grouped(rows: list[dict], scale: float) -> dict[tuple, list[dict]]:
    """按 (zone, kind, 角度) 分组并按 offset 排序；同轴重复圈合并。"""
    groups: dict[tuple, list[dict]] = {}
    for axis in rows:
        key = (axis.get("zone_index"), axis.get("label_kind"),
               round(float(axis.get("angle_deg") or 0.0), 0))
        groups.setdefault(key, []).append(axis)
    out: dict[tuple, list[dict]] = {}
    for key, items in groups.items():
        ordered = sorted(items, key=lambda a: float(a["offset_pt"]))
        deduped = [ordered[0]] if ordered else []
        for axis in ordered[1:]:
            gap = (float(axis["offset_pt"]) - float(deduped[-1]["offset_pt"])) * scale
            if gap > MIN_REAL_GAP_M:
                deduped.append(axis)
        if len(deduped) >= 2:
            out[key] = deduped
    return out


def _gaps(items: list[dict], scale: float) -> list[float]:
    return [round((float(b["offset_pt"]) - float(a["offset_pt"])) * scale, 3)
            for a, b in zip(items, items[1:])]


def _match_direction(items: list[dict], scale: float,
                     anchor_seqs: dict, anchor_axes: dict) -> list[str | None]:
    """一个方向的匹配 → 局部每条轴线的锚图轴号；匹配不上返回空。"""
    matched = match_against_anchors(_gaps(items, scale), anchor_seqs)
    if matched is None:
        return []
    key, gap_match = matched
    if abs(gap_match.scale_ratio - 1.0) > SCALE_RATIO_REVIEW_THRESHOLD * 2:
        return []                       # 比例明显不一致，坐标系不可信
    labels = [str(a.get("label") or "") for a in anchor_axes[key]]
    return anchor_labels_for_local_axes(len(items), gap_match, labels)


async def run_intersection_propagation(
    db: Any, project_id: str, anchor_drawing_id: str,
) -> dict:
    """把锚图的世界坐标经序列匹配传播成其他图的交点；返回统计。

    幂等：每次先清掉本方法先前写入的行（按 `note` 前缀识别），
    **不触碰** `auto:coord_annotation` 等真锚点。
    """
    # **拟合锚图的变换，而不是查锚点表**。
    # 查表法只能覆盖锚图上恰好有坐标标注的那 21 个交点组合，
    # 实测 12 张双向匹配的图里只有 2 张的轴号对落在表内 —— 产出 4 个交点。
    # 拟合出「归一化图纸坐标 → 工程坐标」的相似变换后，
    # **任意**轴号对都能求出世界坐标。
    raw_points = [dict(r) for r in await db.fetch_all(
        _FETCH_ANCHOR_POINTS_SQL, {"drawing_id": anchor_drawing_id})]
    anchor_world = solve_world_transform(raw_points)
    if anchor_world is None:
        return {"anchor_points": len(raw_points), "drawings": 0, "points": 0,
                "note": "锚图的世界锚点不足以解出变换(需 ≥2 个)"}
    if anchor_world.get("suspect"):
        return {"anchor_points": len(raw_points), "drawings": 0, "points": 0,
                "rmse_m": anchor_world.get("rmse_m"),
                "note": "锚图变换残差过大,不用它传播(宁可没有,不可摆错)"}

    rows = await db.fetch_all(_FETCH_AXES_SQL, {"project_id": project_id})
    anchor_axes: dict[tuple, list[dict]] = {}
    candidates: list[tuple[str, dict, float, float, float]] = []
    for row in rows:
        did = str(row["drawing_id"])
        scale = float(row["scale_m_pt"] or 0)
        groups = _grouped(_axis_rows(row["axes"]), scale)
        if did == anchor_drawing_id:
            anchor_axes = groups
        elif groups:
            candidates.append((did, groups, scale,
                               float(row["page_w"] or 0), float(row["page_h"] or 0)))
    if not anchor_axes:
        return {"anchor_points": len(raw_points), "drawings": 0, "points": 0,
                "note": "锚图没有可用轴距序列"}
    anchor_page = next(((float(r["page_w"] or 0), float(r["page_h"] or 0))
                        for r in rows if str(r["drawing_id"]) == anchor_drawing_id),
                       (0.0, 0.0))
    if not anchor_page[0] or not anchor_page[1]:
        return {"anchor_points": len(raw_points), "drawings": 0, "points": 0,
                "note": "锚图缺页面尺寸,无法归一化"}
    # 锚图的轴距必须用**锚图自己的**比例换算成米，否则与候选图不同量纲。
    anchor_scale = next((float(r["scale_m_pt"] or 0) for r in rows
                         if str(r["drawing_id"]) == anchor_drawing_id), 1.0)
    anchor_seqs = {k: _gaps(v, anchor_scale) for k, v in anchor_axes.items()}
    # 轴号 → 锚图上的 offset_pt（同名取先到者；识别已保证同向轴号唯一）
    anchor_offset: dict[str, float] = {}
    for items in anchor_axes.values():
        for axis in items:
            label = str(axis.get("label") or "").strip()
            if label:
                anchor_offset.setdefault(label, float(axis["offset_pt"]))

    written = 0
    covered: set[str] = set()
    for did, groups, scale, page_w, page_h in candidates:
        if not page_w or not page_h:
            continue
        numeric = [(k, v) for k, v in groups.items() if k[1] == "numeric"]
        alpha = [(k, v) for k, v in groups.items() if k[1] == "alpha"]
        points: list[dict] = []
        for _kx, items_x in numeric:
            labels_x = _match_direction(items_x, scale, anchor_seqs, anchor_axes)
            if not labels_x:
                continue
            for _ky, items_y in alpha:
                labels_y = _match_direction(items_y, scale, anchor_seqs, anchor_axes)
                if not labels_y:
                    continue
                for axis_x, label_x in zip(items_x, labels_x):
                    for axis_y, label_y in zip(items_y, labels_y):
                        # 锚图上这两条轴线的归一化位置 → 经锚图变换得世界坐标。
                        # 任意轴号对都能算，不再受限于锚点表里的 21 个组合。
                        ax = anchor_offset.get(label_x or "")
                        ay = anchor_offset.get(label_y or "")
                        if ax is None or ay is None:
                            continue
                        world = apply_similarity(
                            (ax / anchor_page[0], ay / anchor_page[1]), anchor_world)
                        points.append({
                            "label_x": label_x, "label_y": label_y,
                            "x_norm": float(axis_x["offset_pt"]) / page_w,
                            "y_norm": float(axis_y["offset_pt"]) / page_h,
                            "world_x": world[0], "world_y": world[1],
                        })
        if not points:
            continue
        await db.execute(_CLEAR_SQL, {"drawing_id": did, "prefix": f"{NOTE_PREFIX}%"})
        for point in points:
            await db.execute(_UPSERT_SQL, {
                "project_id": project_id, "drawing_id": did,
                "note": f"{NOTE_PREFIX}:{anchor_drawing_id[:8]}", **point})
        written += len(points)
        covered.add(did)

    stats = {"anchor_points": len(raw_points), "candidates": len(candidates),
             "anchor_rmse_m": round(float(anchor_world.get("rmse_m") or 0), 4),
             "drawings": len(covered), "points": written}
    logger.info("[IntersectionPropagation] 锚点 %d 条 → %d 张图 / %d 个交点",
                stats["anchor_points"], stats["drawings"], stats["points"])
    return stats


_FETCH_REAL_ANCHORS_SQL = """
SELECT drawing_id, x_norm, y_norm, world_x, world_y, world_z
FROM axis_intersections
WHERE project_id = CAST(:project_id AS uuid)
  AND world_x IS NOT NULL AND world_y IS NOT NULL
  AND note LIKE 'auto:coord_annotation%'
ORDER BY drawing_id
"""


async def fetch_anchor_candidates(db: Any, project_id: str) -> list[dict]:
    """有**真**世界锚点（坐标标注读出）的图及其变换残差。

    只认 `auto:coord_annotation` —— 传播来的 `auto:sequence_match` 不能当锚，
    否则一次误传播会沿链扩散且无法回溯源头（与分区号传播同一条规则）。
    """
    grouped: dict[str, list[dict]] = {}
    for row in await db.fetch_all(_FETCH_REAL_ANCHORS_SQL, {"project_id": project_id}):
        grouped.setdefault(str(row["drawing_id"]), []).append(dict(row))

    out: list[dict] = []
    for drawing_id, points in grouped.items():
        transform = solve_world_transform(points)
        out.append({
            "drawing_id": drawing_id,
            "anchor_points": len(points),
            # 解不出变换时 rmse_m 为 None —— `pick_anchor_drawing` 会据此排除，
            # 不会拿一张解不出变换的图去传播。
            "rmse_m": None if transform is None else float(transform["rmse_m"]),
            "suspect": bool(transform.get("suspect")) if transform else True,
        })
    return out


async def run_auto_intersection_propagation(db: Any, project_id: str) -> dict:
    """自动选锚 → 传播。**锚图由内容判据选出，不硬编码图号**。"""
    from services.anchor_candidates import pick_anchor_drawing

    candidates = await fetch_anchor_candidates(db, project_id)
    anchor_id = pick_anchor_drawing(
        [c for c in candidates if not c.get("suspect")])
    if anchor_id is None:
        return {"drawings": 0, "points": 0,
                "note": "项目内没有可作锚的图（需 ≥2 个坐标标注锚点且变换可解）"}
    stats = await run_intersection_propagation(db, project_id, anchor_id)
    stats["anchor_drawing_id"] = anchor_id
    return stats
