"""分区号传播 —— 把人工确认的分区号经轴距序列匹配扩散到其他图（J1-3）。

**背景**：§8.0.5 的分区编号**几何推不出**（国标本身的限制），只能人工确认；
而全项目 1052 张多分区图逐张确认不现实。

**J1 实测定的方向**：未匹配原因中「对不上任何锚」占 **91%**、歧义仅 **1%**
⇒ 瓶颈是锚覆盖不足而非算法。所以正解不是做批量点击界面，而是
**人工确认少数覆盖广的锚图，其余自动继承** —— 有杠杆的人工投入。

**三条硬规则**：

1. **只以人工确认的图为锚**。用传播结果当锚会让一次误传播沿链扩散，
   且无法回溯源头（`anchor_drawing_id` 才有意义）。
2. **唯一匹配才传播**。歧义判 unknown —— 错的分区号会让轴号身份全错，
   比没有分区号更糟。
3. **不覆盖人工确认**。确认是**按分区**的，同一张图的其他分区不受影响。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from services.axis_sequence_match import MIN_MATCH_GAPS, match_against_anchors

logger = logging.getLogger(__name__)

#: 比例比超过它就标记 `needs_review` —— **这是指标，不是门禁**。
#:
#: 我先后把它当门禁写错了两次，第二次才想明白它在数学上就不可能独立生效：
#: 比例比 = target 总长 ÷ anchor 对应段总长，而匹配时**每一段**都已要求
#: 偏差 ≤ `SCALE_TOLERANCE`(2%)。各段都在 2% 内，总和的比例比必然也在
#: 2% 内 —— **比例比永远超不过匹配容差**。
#:
#: - 第一版设 5%：比匹配容差宽，永不触发（实测「比例比否决 0」不是数据干净，
#:   是这条路径从未执行）。
#: - 第二版设 1%：确实会拒绝，但拒掉的是**真匹配**——实测 169 组里
#:   92 组(54%)落在 1%~1.5%，且**无一超过 1.5%**。集中成簇而非长尾，
#:   是系统性比例差而非误匹配的形态。当门禁用会把传播从 143 砍到 25。
#:
#: 所以改为：**照常传播，但标记出来供人审**。1.2% 的比例差在 100 米建筑上
#: 是 1.2 米——可感知，但远不是致命错位（对比未配准时的 83~103 米），
#: 值得人看一眼，不值得直接丢弃。
SCALE_RATIO_REVIEW_THRESHOLD = 0.01

#: 同一条轴线两端各注一个圈（§8.0.2 允许）会产生极小的“轴距”，不是真间距。
MIN_REAL_GAP_M = 0.05


@dataclass(frozen=True)
class ZonePropagation:
    """一条传播结论。字段与 `axis_zone_confirmation` 表对齐。"""

    drawing_id: str
    zone_index: int
    zone_label: str
    anchor_drawing_id: str
    anchor_zone_index: int
    scale_ratio: float
    #: 比例比偏离超阈值 —— 仍然传播，但要在界面上标出来（降级必须可见）。
    needs_review: bool = False
    source: str = "propagated"


def axis_gap_sequences(
    axes: Sequence[Mapping[str, Any]] | None, scale_m_pt: float,
    *, min_gaps: int = MIN_MATCH_GAPS,
) -> dict[tuple, list[float]]:
    """识别出的轴线 → 按 ``(zone_index, label_kind, 角度)`` 分组的轴距序列（米）。

    **必须按角度分组**：90° 的 numeric 与 0° 的 alpha 同属正交轴网，
    只保留 0° 会把整个 numeric 方向当斜轴丢掉（实测锚图的 numeric
    有 39 条全是 90°，一度被整组漏掉）。
    """
    if not axes or not scale_m_pt:
        return {}
    groups: dict[tuple, list[Mapping[str, Any]]] = {}
    for axis in axes:
        key = (axis.get("zone_index"), axis.get("label_kind"),
               round(float(axis.get("angle_deg") or 0.0), 0))
        groups.setdefault(key, []).append(axis)

    out: dict[tuple, list[float]] = {}
    for key, items in groups.items():
        ordered = sorted(items, key=lambda a: float(a["offset_pt"]))
        gaps = [
            round((float(b["offset_pt"]) - float(a["offset_pt"])) * scale_m_pt, 3)
            for a, b in zip(ordered, ordered[1:])
        ]
        gaps = [g for g in gaps if g > MIN_REAL_GAP_M]
        if len(gaps) >= min_gaps:
            out[key] = gaps
    return out


def propagate_zone_labels(
    candidates: Iterable[Mapping[str, Any]] | None,
    anchors: Sequence[Mapping[str, Any]] | None,
    *, already_confirmed: set[tuple[str, int]] | None = None,
    review_threshold: float = SCALE_RATIO_REVIEW_THRESHOLD,
) -> list[ZonePropagation]:
    """把锚的分区号传播给唯一匹配上的候选。

    ``candidates`` / ``anchors`` 每项形如
    ``{"drawing_id", "zone_index", "sequence"[, "zone_label"]}``。

    结果按 ``(drawing_id, zone_index)`` 排序，**与输入顺序无关** ——
    顺序依赖会让诊断脚本无法预测生产行为（上一轮为此被带偏三轮）。
    """
    if not candidates or not anchors:
        return []
    confirmed = already_confirmed or set()
    # **锚的身份必须含方向**:同一分区的 numeric 与 alpha 是两套独立序列，
    # 只用 `(drawing_id, zone_index)` 作键会被字典去重掉一半 ——
    # 实测锚图 6 组锚被压成 3 组，传播从 143 张掉到 46 张，
    # 而统计里报的还是去重前的 6，把这件事盖住了。
    anchor_map = {
        (a.get("key") or (str(a["drawing_id"]), int(a["zone_index"]))): a
        for a in anchors if a.get("sequence")
    }
    if not anchor_map:
        return []
    anchor_drawing_ids = {str(a["drawing_id"]) for a in anchor_map.values()}
    sequences = {key: a["sequence"] for key, a in anchor_map.items()}

    out: list[ZonePropagation] = []
    for cand in candidates:
        did = str(cand.get("drawing_id") or "")
        zone_index = int(cand.get("zone_index") or 0)
        if not did or (did, zone_index) in confirmed:
            continue
        if did in anchor_drawing_ids:
            continue                       # 锚图不传播给自己
        matched = match_against_anchors(cand.get("sequence"), sequences)
        if matched is None:
            continue
        anchor_key, gap_match = matched
        drift = abs(gap_match.scale_ratio - 1.0)
        anchor = anchor_map[anchor_key]
        out.append(ZonePropagation(
            drawing_id=did, zone_index=zone_index,
            zone_label=str(anchor.get("zone_label") or "").strip(),
            anchor_drawing_id=str(anchor["drawing_id"]),
            anchor_zone_index=int(anchor["zone_index"]),
            scale_ratio=round(gap_match.scale_ratio, 4),
            needs_review=drift > review_threshold,
        ))
    out.sort(key=lambda p: (p.drawing_id, p.zone_index))
    return out
