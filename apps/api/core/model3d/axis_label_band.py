"""轴号带:把轴号圈聚成带,再由带直接给出轴线位置。

**依据**:GB/T 50001 §8.0.2「定位轴线的编号宜注写在平面图**下方及左侧**」。
于是同一族轴线的轴号圈必然排成一条**带**——横行标注竖向轴线、竖列标注横向
轴线。关键推论:**带方向与它所标注的轴线方向垂直**。

**为什么这条路比几何筛线强**:圈是确定性可检的(实测三张图 100% 精确),
一个圈就是一条轴线,位置由圈心给出。此前「先找线再猜哪条是轴线」的路线
在真值补全后实测正交召回仅 71%、旋转过检 44 条;而带一出,分区结构直接浮现:

    0° 带 24 个 → 分区 1 的 24 条数字轴线
    0° 带 12 个 → 分区 2 的 2-1~2-12
   90° 带 14 个 → 分区 2 的 14 条字母轴线
   42° 带 16 个 → 分区 3 的 16 条数字轴线
  132° 带 11 + 4 个 → 分区 3 的 15 条字母轴线(4 个在边界错台段上)
"""
from __future__ import annotations

import math

from core.model3d.axis_label_circle import circle_offsets

#: 带内共线容差 = 圈径 × 该比例。按圈径定而非绝对值,才能同时适配
#: A-01-02A 的 28pt 与 A-01-04A 的 16pt
BAND_TOLERANCE_RATIO = 0.25

#: 成带的最小成员数。两个圈不成带——附加轴线常成对出现,不能当主带
MIN_BAND_MEMBERS = 3

#: 默认候选带方向。正交两向 + 实测旋转分区的 42°/132°
DEFAULT_BAND_DIRECTIONS = (0.0, 90.0)

#: 一条轴线上两端的圈,其沿轴向偏移相同;归并容差(pt)
AXIS_MERGE_TOLERANCE_PT = 3.0

#: 带内沿带方向的最大允许间隔 = 中位间隔 × 该倍数。
#: 依据 §8.0.3「轴号沿边**依次**注写」——带是相邻轴号的**连续一串**,
#: 而不是任意共线的点集。没有这条约束时,竖列会把远处偶然共线的横行成员
#: 也吸进来(实测合成用例:825pt 外的圈被并入中位间隔仅 50pt 的带)。
MAX_ALONG_GAP_RATIO = 8.0


def axis_angle_of_band(band_angle_deg: float) -> float:
    """带方向 → 它所标注的轴线方向(垂直),归一化到 [0, 180)。

    搞反这一步会让整套配对全错:横行(0°)标注的是**竖向(90°)**轴线。
    """
    return (band_angle_deg + 90.0) % 180.0


def _tolerance(circles: list[dict]) -> float:
    diameters = [c.get("diameter_pt", 0.0) for c in circles]
    mean_d = sum(diameters) / len(diameters) if diameters else 0.0
    return max(mean_d * BAND_TOLERANCE_RATIO, 1.0)


def _cluster_by_offset(indices: list[int], offsets: list[float],
                       tol: float) -> list[list[int]]:
    """按法向偏移把下标聚成簇(偏移相差超过容差即断开)。"""
    if not indices:
        return []
    ordered = sorted(indices, key=lambda i: offsets[i])
    clusters, cur = [], [ordered[0]]
    for i in ordered[1:]:
        if abs(offsets[i] - offsets[cur[-1]]) <= tol:
            cur.append(i)
        else:
            clusters.append(cur)
            cur = [i]
    clusters.append(cur)
    return clusters


def _along(circle: dict, band_angle: float) -> float:
    """圈心沿带方向的一维坐标。"""
    rad = math.radians(band_angle)
    return circle["cx"] * math.cos(rad) + circle["cy"] * math.sin(rad)


def _split_by_along_gaps(members: list[int], circles: list[dict],
                         band_angle: float,
                         max_ratio: float = MAX_ALONG_GAP_RATIO) -> list[list[int]]:
    """按沿带方向的间隔把一簇切成若干**连续串**(§8.0.3 依次注写)。

    阈值取中位间隔的倍数,而不是绝对值——不同图纸的轴距量级差很多。
    """
    if len(members) < 2:
        return [members]
    ordered = sorted(members, key=lambda i: _along(circles[i], band_angle))
    gaps = [_along(circles[ordered[k + 1]], band_angle)
            - _along(circles[ordered[k]], band_angle)
            for k in range(len(ordered) - 1)]
    positive = sorted(g for g in gaps if g > 0)
    if not positive:
        return [ordered]
    median = positive[len(positive) // 2]
    limit = median * max_ratio
    runs, cur = [], [ordered[0]]
    for k, gap in enumerate(gaps):
        if gap > limit:
            runs.append(cur)
            cur = [ordered[k + 1]]
        else:
            cur.append(ordered[k + 1])
    runs.append(cur)
    return runs


def _band_record(members: list[int], circles: list[dict], band_angle: float,
                 offsets: list[float]) -> dict:
    """带的度量信息。span 是沿带方向的跨度,用于诊断带是否被截断。"""
    rad = math.radians(band_angle)
    along = [circles[i]["cx"] * math.cos(rad) + circles[i]["cy"] * math.sin(rad)
             for i in members]
    ordered = sorted(members)
    return {
        "band_angle_deg": round(band_angle, 1),
        "axis_angle_deg": round(axis_angle_of_band(band_angle), 1),
        "offset_pt": round(sum(offsets[i] for i in members) / len(members), 2),
        "members": ordered,
        "member_count": len(members),
        "span_pt": round(max(along) - min(along), 2),
        # 沿带区间(一维,沿 band_angle 方向)。分区配对要用它判「紧贴在跨度之外」
        "along_lo": round(min(along), 2),
        "along_hi": round(max(along), 2),
        # 带自带成员圈的坐标副本,使 bands_to_axes 无需再回查原始列表
        "circles": [dict(circles[i]) for i in ordered],
    }


def detect_bands(
    circles: list[dict],
    directions: tuple[float, ...] = DEFAULT_BAND_DIRECTIONS,
    min_members: int = MIN_BAND_MEMBERS,
) -> list[dict]:
    """轴号圈 → 带。

    同一批圈在不同方向上会**偶然共线**(实测 42° 方向能凑出若干 3 成员假带),
    所以按成员数从多到少**贪心独占**:大带先挑,小带只能用剩下的圈。
    """
    if not circles:
        return []
    tol = _tolerance(circles)

    candidates: list[tuple[list[int], float, list[float]]] = []
    for band_angle in directions:
        offsets = circle_offsets(circles, band_angle)
        for cluster in _cluster_by_offset(list(range(len(circles))), offsets, tol):
            for run in _split_by_along_gaps(cluster, circles, band_angle):
                if len(run) >= min_members:
                    candidates.append((run, band_angle, offsets))

    candidates.sort(key=lambda c: -len(c[0]))
    taken: set[int] = set()
    bands: list[dict] = []
    for cluster, band_angle, offsets in candidates:
        free = [i for i in cluster if i not in taken]
        if len(free) < min_members:
            continue
        taken.update(free)
        bands.append(_band_record(free, circles, band_angle, offsets))
    return bands


def bands_to_axes(bands: list[dict], *, page_h: float,
                  merge_tol: float = AXIS_MERGE_TOLERANCE_PT) -> list[dict]:
    """带 → 轴线候选。一个圈 = 一条轴线;**同一轴线两端的两个圈只算一条**。

    带自带成员圈坐标,故无需回查原始列表。
    """
    if not bands:
        return []

    # 同一轴线方向的多条带标注的是**同一批轴线**(如一条竖向轴线在图上方和
    # 下方各有一个轴号圈),所以先按轴线方向汇总所有圈,再按轴向偏移聚类,
    # 否则一条轴线会被上下两条带各计一次。
    pooled: dict[float, list[tuple[dict, int]]] = {}
    for band_id, band in enumerate(bands):
        for circle in band["circles"]:
            pooled.setdefault(band["axis_angle_deg"], []).append((circle, band_id))

    out: list[dict] = []
    for axis_angle, entries in pooled.items():
        members = [c for c, _ in entries]
        offsets = circle_offsets(members, axis_angle)
        for cluster in _cluster_by_offset(list(range(len(members))),
                                          offsets, merge_tol):
            off = sum(offsets[i] for i in cluster) / len(cluster)
            out.append({
                "angle_deg": axis_angle,
                "offset_pt": round(off, 2),
                "offset_norm": round(off / page_h, 6) if page_h else 0.0,
                "circle_count": len(cluster),
                "band_ids": sorted({entries[i][1] for i in cluster}),
                "source": "label_circle",
            })
    return sorted(out, key=lambda a: (a["angle_deg"], a["offset_pt"]))


def axes_from_circles(
    circles: list[dict], *, page_h: float,
    directions: tuple[float, ...] = DEFAULT_BAND_DIRECTIONS,
    min_members: int = MIN_BAND_MEMBERS,
) -> dict:
    """一步到位:圈 → {bands, axes}。这是对外的主入口。"""
    bands = detect_bands(circles, directions=directions, min_members=min_members)
    return {"bands": bands, "axes": bands_to_axes(bands, page_h=page_h)}
