"""分区号传播的编排（J1-3）—— 读识别结果 → 算轴距序列 → 传播 → 落库。

纯编排：判据全在 :mod:`services.axis_zone_propagation`（纯函数、已单测），
这里只负责取数与落库，便于离线测试与复跑。

**幂等**：可反复执行。每多确认一张覆盖广的锚图就再跑一次，匹配面扩一片。
人工确认的行不会被触碰（见 `_PROPAGATE_SQL` 的 WHERE）。
"""
from __future__ import annotations

import json
import logging
from typing import Any

from services import axis_recognition_repo as repo
from services.axis_zone_propagation import (
    axis_gap_sequences, propagate_zone_labels,
)

logger = logging.getLogger(__name__)

_FETCH_AXES_SQL = """
SELECT r.drawing_id, r.axes, t.scale_m_pt
FROM axis_recognition r
JOIN drawing_transform t ON t.drawing_id = r.drawing_id
WHERE r.project_id = CAST(:project_id AS uuid)
  AND r.status = 'ready' AND r.suspect_symbol_field = false
  AND r.axes IS NOT NULL
ORDER BY r.drawing_id
"""


def _rows_to_sequences(rows: Any) -> dict[str, dict[tuple, list[float]]]:
    """每图 → {(zone_index, label_kind, 角度): 轴距序列}。"""
    out: dict[str, dict[tuple, list[float]]] = {}
    for row in rows:
        raw = row["axes"]
        axes = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
        scale = float(row["scale_m_pt"] or 0)
        groups = axis_gap_sequences(axes, scale)
        if groups:
            out[str(row["drawing_id"])] = groups
    return out


async def run_zone_propagation(
    db: Any, project_id: str, *, dry_run: bool = False,
    extra_anchor_drawing_id: str | None = None,
) -> dict:
    """跑一轮传播，返回统计。

    统计里**必须报出 `anchor_zones`** —— 传播规模由锚覆盖决定
    （实测未匹配原因 91% 是「对不上任何锚」），
    只报成功数会让人以为是算法在起作用。
    """
    manual = await repo.fetch_manual_zones(db, project_id)
    if not manual and not extra_anchor_drawing_id:
        return {"anchor_zones": 0, "candidates": 0, "propagated": 0,
                "note": "无人工确认的分区，无锚可用"}

    rows = await db.fetch_all(_FETCH_AXES_SQL, {"project_id": project_id})
    by_drawing = _rows_to_sequences(rows)

    anchors = []
    for item in manual:
        groups = by_drawing.get(item["drawing_id"]) or {}
        for key, sequence in groups.items():
            if key[0] != item["zone_index"]:
                continue
            anchors.append({
                # `key` 含方向与角度:同一分区的 numeric 与 alpha 是两套独立
                # 序列,少了它会在 anchor_map 里被去重掉一半。
                "key": (item["drawing_id"], *key),
                "drawing_id": item["drawing_id"],
                "zone_index": item["zone_index"],
                "zone_label": item["zone_label"],
                "sequence": sequence})
    # **额外锚**（预估用）：把某图假设为已确认，看能多带动多少。
    # 分区号是什么不影响「能匹配上几张」，所以用占位标签。
    if extra_anchor_drawing_id:
        for key, sequence in (by_drawing.get(extra_anchor_drawing_id) or {}).items():
            anchors.append({
                "key": (extra_anchor_drawing_id, *key),
                "drawing_id": extra_anchor_drawing_id,
                "zone_index": key[0],
                "zone_label": f"?{key[0]}",
                "sequence": sequence})

    if not anchors:
        return {"anchor_zones": 0, "candidates": len(by_drawing), "propagated": 0,
                "note": "已确认分区的图没有可用轴距序列（轴线太少或无变换）"}

    anchor_ids = {a["drawing_id"] for a in anchors}
    candidates = [
        {"drawing_id": did, "zone_index": key[0], "sequence": sequence}
        for did, groups in by_drawing.items() if did not in anchor_ids
        for key, sequence in groups.items()
    ]
    confirmed = await repo.fetch_confirmed_keys(db, project_id)
    results = propagate_zone_labels(candidates, anchors,
                                    already_confirmed=confirmed)
    # **dry-run 只统计不落库**：试算多个候选时，「看看哪张划算」
    # 本身不该改动 axis_zone_confirmation。
    written = len(results) if dry_run else await repo.save_propagations(
        db, project_id=project_id, items=results)
    stats = {
        # **报去重后的真实锚数**:上一版报 len(anchors)(去重前),
        # 掩盖了「6 组锚被压成 3 组」这个 bug 整整一轮。
        "anchor_zones": len({a["key"] for a in anchors}),
        "anchor_drawings": len(anchor_ids),
        "candidates": len(candidates),
        "propagated": written,
        "drawings_covered": len({r.drawing_id for r in results}),
    }
    logger.info("[ZonePropagation] 锚 %d 组/%d 图 → 传播 %d 条,覆盖 %d 图",
                stats["anchor_zones"], stats["anchor_drawings"],
                stats["propagated"], stats["drawings_covered"])
    return stats


async def suggest_anchor_drawings(db: Any, project_id: str,
                                  limit: int = 10) -> list[dict]:
    """荐锚 —— 「该确认哪几张图最划算」（J1-3）。

    实测未匹配原因中「对不上任何锚」占 **91%**、歧义仅 1%
    ⇒ 瓶颈是锚覆盖不足；而人工确认一次的成本固定，
    所以该优先确认**覆盖最广**的图，而不是照单逐张确认 1052 张。
    """
    from services.anchor_candidates import rank_anchor_candidates

    rows = await db.fetch_all(_FETCH_AXES_SQL, {"project_id": project_id})
    by_drawing = _rows_to_sequences(rows)
    confirmed = {did for did, _zone in await repo.fetch_confirmed_keys(db, project_id)}
    titles = {
        str(r["id"]): (r["drawing_no"], r["title"])
        for r in await db.fetch_all(
            "SELECT id, drawing_no, title FROM drawings "
            "WHERE project_id = CAST(:pid AS uuid)", {"pid": project_id})
    }
    candidates = []
    for did, groups in by_drawing.items():
        drawing_no, title = titles.get(did, ("", ""))
        candidates.append({
            "drawing_id": did, "drawing_no": drawing_no, "title": title,
            "total_gaps": sum(len(seq) for seq in groups.values()),
            # **最长的一组**才是覆盖力 —— 匹配按组做，各组总和会把
            # 「11 个分区各 4 段」这种符号场误检抬成榜首（实测发生过）
            "max_gaps": max((len(seq) for seq in groups.values()), default=0),
            # 方向数决定能否构成交点 —— 单向图确认了也拿不到世界坐标
            "directions": len({key[1] for key in groups}),
            "zones": len({key[0] for key in groups}),
            "zone_confirmed": did in confirmed,
        })
    ranked = rank_anchor_candidates(candidates, limit=limit)
    await _attach_zone_estimates(db, project_id, ranked)
    # 按**实测解锁量**重排：覆盖力代理指标（最长序列 × 方向数）会被
    # 符号场误检刷榜 —— 实测前 4 名理由全带「轴线数远超常见轴网」
    # （最长序列 79/77/53/117 段），而它们一张也解锁不了。
    from services.anchor_candidates import rank_by_estimate

    return rank_by_estimate(ranked)


async def _attach_zone_estimates(db: Any, project_id: str,
                                 ranked: list[dict]) -> None:
    """给每条推荐补「确认它能**多**带动几张」。

    口径必须是**分区传播**：荐锚问的是确认分区号的价值，
    而非该图作为交点传播锚的价值（后者要求它自己有世界锚点，
    荐锚列表里的图大多没有 ⇒ 恒为 0，答非所问）。

    基线只算一次（N+1 次而非 2N 次）；试算失败就不补该项。
    """
    from services.axis_intersection_propagate import format_coverage_estimate

    try:
        base = await run_zone_propagation(db, project_id, dry_run=True)
        base_n = int(base.get("drawings_covered") or 0)
    except Exception:  # noqa: BLE001 — 基线算不出就整体不补，不猜
        return

    for item in ranked:
        try:
            boosted = await run_zone_propagation(
                db, project_id, dry_run=True,
                extra_anchor_drawing_id=str(item["drawing_id"]))
            gain = max(0, int(boosted.get("drawings_covered") or 0) - base_n)
            item["estimated_drawings"] = gain
            item["estimate"] = format_coverage_estimate(gain, 0)
        except Exception:  # noqa: BLE001 — 单项试算失败不影响其余推荐
            continue


async def estimate_zone_unlock(db: Any, project_id: str,
                               extra_anchor_drawing_id: str) -> int:
    """确认这张图的分区号，能**多**带动几张图（增量）。

    有它 − 没它 才是这次确认的贡献；只看「有它」会把已有锚的功劳算进来。
    增量为负说明试算有噪声，报 0 而不是负数（宁可保守）。
    """
    base = await run_zone_propagation(db, project_id, dry_run=True)
    boosted = await run_zone_propagation(
        db, project_id, dry_run=True,
        extra_anchor_drawing_id=extra_anchor_drawing_id)
    return max(0, int(boosted.get("drawings_covered") or 0)
               - int(base.get("drawings_covered") or 0))
