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
ON CONFLICT (drawing_id, label_x, label_y) DO NOTHING
"""
# **真值优先**：被先验救回的单锚点图**自己就有坐标标注行**（真实测量），
# 而传播会给同一个轴号对写推算值 —— 撞唯一约束。
# DO NOTHING 让先写入的真实标注保留，推算值不覆盖它。
# （`_CLEAR_SQL` 只删 `auto:sequence_match%`，不碰 `auto:coord_annotation`。）

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
    skip_drawings: set[str] | None = None, generation: int = 0,
    prior: dict | None = None,
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
    anchor_world = solve_world_transform(raw_points, prior=prior)
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
        # 已被前几代覆盖的图跳过：清理是按目标图做的，重复处理会让
        # 后一代清掉前一代的结果（而前一代的世界坐标更可信，代数更低）
        if skip_drawings and did in skip_drawings:
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
                # 代数记进 note：误差逐代累积，下游可据此降级
                "note": f"{NOTE_PREFIX}:g{generation}:{anchor_drawing_id[:8]}",
                **point})
        written += len(points)
        covered.add(did)

    stats = {"anchor_points": len(raw_points), "candidates": len(candidates),
             "anchor_rmse_m": round(float(anchor_world.get("rmse_m") or 0), 4),
             "drawings": len(covered), "points": written,
             # 迭代传播要知道**具体覆盖了哪些图** —— 它们是下一代锚的来源
             "covered_drawings": sorted(covered)}
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


#: 传播最多迭代几代。
#:
#: **误差逐代累积**：第 2 代锚的世界坐标本身是第 1 代传播来的，
#: 拿它再拟合变换等于在估计值上再估计。取 3 代：既让覆盖扩散开
#: （实测第 0 代只有 **1 张**可用锚，而双向轴网有 856 张），
#: 又不至于让误差滚雪球。每代记进 note，下游可据此降级。
MAX_PROPAGATION_GENERATIONS = 3

#: 能作为下一代锚的最大残差（米）。
#:
#: 第 0 代真锚实测残差 **6 毫米**；传播来的图残差会更大，
#: 但超过半米就不该再当基准 —— 拿它作锚会把误差放大给下一代。
MAX_ANCHOR_RESIDUAL_M = 0.5


def next_generation_anchors(
    covered: set[str], previous_anchors: set[str],
    residuals: dict[str, float],
) -> list[str]:
    """本代新覆盖的图里，哪些够格当下一代锚（残差小者优先）。

    - 已用过的不再作锚：否则每代都在重复同一批匹配；
    - **算不出残差的不作锚**：变换没解出来就是判不出，不猜；
    - 残差超 `MAX_ANCHOR_RESIDUAL_M` 的不作锚：误差会放大给下一代。
    """
    fresh = [
        did for did in covered
        if did not in previous_anchors
        and residuals.get(did) is not None
        and float(residuals[did]) <= MAX_ANCHOR_RESIDUAL_M
    ]
    # 残差小者优先；同残差按 id 定序（顺序依赖会让重建结果漂移）
    return sorted(fresh, key=lambda d: (float(residuals[d]), d))


async def run_auto_intersection_propagation(db: Any, project_id: str) -> dict:
    """自动选锚 → 传播。**锚图由内容判据选出，不硬编码图号**。"""
    from services.anchor_candidates import pick_anchor_drawing

    candidates = await fetch_anchor_candidates(db, project_id)
    anchor_id = pick_anchor_drawing(
        [c for c in candidates if not c.get("suspect")])
    if anchor_id is None:
        return {"drawings": 0, "points": 0,
                "note": "项目内没有可作锚的图（需 ≥2 个坐标标注锚点且变换可解）"}

    # **迭代传播**：本代传播成功的图已带世界坐标，能拟合出自己的变换、
    # 成为下一代锚。实测第 0 代只有 1 张可用锚，而双向轴网有 856 张 ——
    # 用单张锚的序列去匹配全部，「对不上任何锚」占 91% 是必然的。
    # **可信先验**：第 0 代锚（残差合格）的比例与旋转，供只有 1 个坐标标注
    # 的图借用 —— 实测 13 张有标注的图里 **10 张只标了一处**，
    # 没有先验它们一张都用不上。
    prior_points = [dict(r) for r in await db.fetch_all(
        _FETCH_ANCHOR_POINTS_SQL, {"drawing_id": anchor_id})]
    prior = solve_world_transform(prior_points)
    if prior and prior.get("suspect"):
        prior = None

    # **用先验重评所有候选，扩充第 0 代锚集**。
    # 次序不能颠倒：先验只能由 ≥3 点的图解出，有了它单锚点图才可解 ——
    # 而实测 13 张有坐标标注的图里 **10 张只标了一处**。
    # （此前先验只接在下游，第 0 代锚集没扩，实测覆盖 40→40 纹丝不动。）
    extra_anchors: list[str] = []
    if prior:
        for cand in candidates:
            cid = str(cand.get("drawing_id") or "")
            if not cid or cid == anchor_id:
                continue
            pts = [dict(r) for r in await db.fetch_all(
                _FETCH_ANCHOR_POINTS_SQL, {"drawing_id": cid})]
            solved = solve_world_transform(pts, prior=prior)
            if solved and not solved.get("suspect"):
                extra_anchors.append(cid)
    if extra_anchors:
        logger.info("[IntersectionPropagation] 先验救回 %d 张单锚点图作锚",
                    len(extra_anchors))

    used: set[str] = set()
    covered_all: set[str] = set()
    total_points = 0
    per_generation: list[dict] = []
    anchors = [anchor_id, *extra_anchors]

    for generation in range(MAX_PROPAGATION_GENERATIONS):
        if not anchors:
            break
        gen_covered: set[str] = set()
        gen_points = 0
        for aid in anchors:
            stats = await run_intersection_propagation(
                db, project_id, aid,
                skip_drawings=used | covered_all | gen_covered,
                generation=generation, prior=prior)
            gen_points += int(stats.get("points") or 0)
            gen_covered |= set(stats.get("covered_drawings") or [])
            used.add(aid)
        per_generation.append({"generation": generation,
                               "anchors": len(anchors),
                               "drawings": len(gen_covered),
                               "points": gen_points})
        covered_all |= gen_covered
        total_points += gen_points
        if not gen_covered:
            break
        # 下一代锚 = 本代新覆盖且变换可解、残差够小的图
        residuals = await _residuals_of(db, gen_covered, prior)
        anchors = next_generation_anchors(gen_covered, used, residuals)

    return {
        "drawings": len(covered_all),
        "points": total_points,
        "anchor_drawing_id": anchor_id,
        "generations": per_generation,
    }


async def _residuals_of(db: Any, drawing_ids: set[str],
                        prior: dict | None = None) -> dict[str, float]:
    """这些图各自的变换残差 —— 决定它们够不够格当下一代锚。

    算不出的**不放进结果**（而不是给个大值）：`next_generation_anchors`
    据此判「判不出就不作锚」，两处口径一致。
    """
    out: dict[str, float] = {}
    for did in drawing_ids:
        try:
            points = [dict(r) for r in await db.fetch_all(
                _FETCH_ANCHOR_POINTS_SQL, {"drawing_id": did})]
            solved = solve_world_transform(points, prior=prior)
            if solved and not solved.get("suspect"):
                rmse = solved.get("rmse_m")
                if rmse is not None:
                    out[did] = float(rmse)
        except Exception:  # noqa: BLE001 — 单图算不出不阻断整轮
            continue
    return out
