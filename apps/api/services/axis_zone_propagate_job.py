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


async def run_zone_propagation(db: Any, project_id: str) -> dict:
    """跑一轮传播，返回统计。

    统计里**必须报出 `anchor_zones`** —— 传播规模由锚覆盖决定
    （实测未匹配原因 91% 是「对不上任何锚」），
    只报成功数会让人以为是算法在起作用。
    """
    manual = await repo.fetch_manual_zones(db, project_id)
    if not manual:
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
    written = await repo.save_propagations(db, project_id=project_id,
                                           items=results)
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
