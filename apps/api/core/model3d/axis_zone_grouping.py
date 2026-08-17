"""分区分组:把轴号带聚成分区(GB/T 50001 §8.0.5)。

轴号是「分区号-轴线号」。序列本身可由 §8.0.3 推出且实测逐带 100% 正确,
但**分区归属**若推不出来,所有带都会自称分区 1——实测 99 条真值合并后只剩
38 条唯一标签。

**可推的部分**:§8.0.2「编号宜注写在平面图**下方及左侧**」⇒ 同一分区的横行带与
竖列带互相**紧贴在对方跨度之外**。A-01-02A 实测间隙比:

    分区1  横行 y=2178 × 竖列 x= 846   →  9.5% / 8.6%   同区
    分区2  横行 y=1575 × 竖列 x=1505   →  6.1% / 12.5%  同区
    错配   分区1横行 × 分区2竖列        →    0% / 86%    不同区

**推不出的部分**:分区**编号**(哪个是 1)。§8.0.5 未规定书写顺序,只能由 OCR
锚定或人工确认——**每个分区一次**,不是每条轴线一次,所以人工成本极低。

**曾踩的坑**:用 `-offset_B` 换算带 B 在带 A 上的位置。该等式只在 αB = αA+90 时
成立,而带角度归一化到 [0,180) 后符号会翻,实测把分区 2 的两条带判成不同区
(比值 3.13 / 4.62)。现改为**直接投影成员圈坐标**,不做符号推导。
"""
from __future__ import annotations

import math

from core.model3d.axis_label_derive import (
    ALPHA_KIND, NUMERIC_KIND, label_kind_for_axis_angle,
)

#: 判为「紧贴」的间隙上限 = 对方沿带跨度 × 该比例。
#: 实测同区最大 12.5%,错配最小 52%,取 25% 有充分余量。
ADJACENCY_SPAN_RATIO = 0.25

#: 两带视为垂直的角度容差(度)
PERPENDICULAR_TOLERANCE_DEG = 3.0


def _along(circle: dict, angle_deg: float) -> float:
    rad = math.radians(angle_deg)
    return circle["cx"] * math.cos(rad) + circle["cy"] * math.sin(rad)


def _is_perpendicular(a: dict, b: dict) -> bool:
    delta = (a["band_angle_deg"] - b["band_angle_deg"]) % 180.0
    return abs(delta - 90.0) <= PERPENDICULAR_TOLERANCE_DEG


def _gap_ratio(host: dict, guest: dict) -> float:
    """guest 的位置投影到 host 的沿带轴上,距 host 区间的间隙 ÷ host 跨度。

    直接用 guest 的成员坐标投影,避免任何符号推导。
    """
    positions = [_along(c, host["band_angle_deg"]) for c in guest["circles"]]
    if not positions:
        return float("inf")
    pos = sum(positions) / len(positions)
    lo, hi = host["along_lo"], host["along_hi"]
    gap = 0.0 if lo <= pos <= hi else min(abs(pos - lo), abs(pos - hi))
    span = max(hi - lo, 1.0)
    return gap / span


def is_same_zone(a: dict, b: dict,
                 ratio: float = ADJACENCY_SPAN_RATIO) -> bool:
    """两条带是否属于同一分区。

    必须**互相**紧贴:单向成立不够——分区 1 的横行跨度很宽,几乎任何竖列
    投影进去都是 0 间隙,只有反向检查才能把跨区组合排掉。
    """
    if not _is_perpendicular(a, b):
        return False
    return _gap_ratio(a, b) <= ratio and _gap_ratio(b, a) <= ratio


def group_bands_into_zones(bands: list[dict],
                           ratio: float = ADJACENCY_SPAN_RATIO) -> list[dict]:
    """带列表 → 分区列表(连通分量;不改入参)。

    一个分区可含多于两条带——实测分区 1 的轴号列在**左右两侧**都有。
    未能配对的带自成一区,绝不丢弃(丢弃等于静默漏掉一批轴线)。

    返回 [{zone, bands, numeric_axes, alpha_axes}]。`zone` 恒为 None:
    分区编号推不出来,留给 OCR 锚定或人工确认。
    """
    n = len(bands)
    if n == 0:
        return []

    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(n):
        for j in range(i + 1, n):
            if is_same_zone(bands[i], bands[j], ratio):
                parent[find(i)] = find(j)

    groups: dict[int, list[dict]] = {}
    for i, band in enumerate(bands):
        groups.setdefault(find(i), []).append(band)

    zones = []
    for members in groups.values():
        counts = {NUMERIC_KIND: 0, ALPHA_KIND: 0}
        for band in members:
            kind = label_kind_for_axis_angle(band["axis_angle_deg"])
            counts[kind] += band["member_count"]
        zones.append({
            "zone": None,                     # §8.0.5 未规定编号,不猜
            "bands": members,
            "numeric_axes": counts[NUMERIC_KIND],
            "alpha_axes": counts[ALPHA_KIND],
        })
    return sorted(zones,
                  key=lambda z: -(z["numeric_axes"] + z["alpha_axes"]))


# ── 小带吸附(按分区二维范围)──────────────────────────────────────

#: 小带到分区范围的最大距离 = 分区对角线 × 该比例。
#: 实测:分区 2 的附加轴线列距其范围 372pt,而分区 2 对角线约 1000pt(37%);
#: 若误吸到分区 1 则距离 497pt / 对角线 1620pt(31%)——两者比值接近,
#: **不能只看比例**,所以判据是「取最近的那个分区」+ 一个宽松的上限兜底。
ATTACH_MAX_DISTANCE_RATIO = 0.6


def zone_extent(zone: dict) -> tuple[float, float, float, float]:
    """分区的页面包围盒(由其所有成员圈张成)。"""
    xs = [c["cx"] for b in zone["bands"] for c in b["circles"]]
    ys = [c["cy"] for b in zone["bands"] for c in b["circles"]]
    return (min(xs), min(ys), max(xs), max(ys))


def _box_distance(box: tuple[float, float, float, float],
                  point: tuple[float, float]) -> float:
    """点到矩形的距离(在框内为 0)。"""
    x0, y0, x1, y1 = box
    dx = max(x0 - point[0], 0.0, point[0] - x1)
    dy = max(y0 - point[1], 0.0, point[1] - y1)
    return math.hypot(dx, dy)


def _band_centroid(band: dict) -> tuple[float, float]:
    circles = band["circles"]
    return (sum(c["cx"] for c in circles) / len(circles),
            sum(c["cy"] for c in circles) / len(circles))


def _zone_axis_angles(zone: dict) -> set[float]:
    return {round(b["axis_angle_deg"], 0) for b in zone["bands"]}


def attach_small_bands(zones: list[dict], leftovers: list[dict],
                       max_ratio: float = ATTACH_MAX_DISTANCE_RATIO) -> list[dict]:
    """把未配对的小带吸附到最近的分区(不改入参)。

    **为什么用二维范围而不是一维带跨度**:小带作为 host 时跨度仅 167~352pt,
    `gap/span` 会爆到 2.03~4.50,判据失效;而按「最近的带」判又会出错——
    实测 band(x=2631, y938~1164)是**分区 2 的附加轴线** `2-1/k`,
    按最近的带会归到分区 1(0.07 < 0.56)。

    按分区**二维范围**就一目了然:分区 2 的 y[778,1529] 把它包住,
    而分区 1 的 y[1661,2179] 离它 497pt。

    另加**方向相容**约束:旋转带不能被吸进只有正交轴线的分区。
    吸不上的自成一区,绝不丢弃。
    """
    if not leftovers:
        return zones

    buckets: dict[int, list[dict]] = {}
    orphans: list[dict] = []
    for band in leftovers:
        angle = round(band["axis_angle_deg"], 0)
        best = None
        for idx, zone in enumerate(zones):
            if angle not in _zone_axis_angles(zone):
                continue                      # 方向不相容
            box = zone_extent(zone)
            diagonal = math.dist((box[0], box[1]), (box[2], box[3]))
            distance = _box_distance(box, _band_centroid(band))
            if distance > max_ratio * diagonal:
                continue                      # 太远,不硬塞
            if best is None or distance < best[0]:
                best = (distance, idx)
        if best is None:
            orphans.append(band)
        else:
            buckets.setdefault(best[1], []).append(band)

    out = []
    for idx, zone in enumerate(zones):
        extra = buckets.get(idx)
        if not extra:
            out.append(zone)
            continue
        merged = list(zone["bands"]) + extra
        counts = {NUMERIC_KIND: 0, ALPHA_KIND: 0}
        for band in merged:
            counts[label_kind_for_axis_angle(band["axis_angle_deg"])] += \
                band["member_count"]
        out.append({**zone, "bands": merged,
                    "numeric_axes": counts[NUMERIC_KIND],
                    "alpha_axes": counts[ALPHA_KIND]})

    for band in orphans:
        kind = label_kind_for_axis_angle(band["axis_angle_deg"])
        out.append({"zone": None, "bands": [band],
                    "numeric_axes": band["member_count"] if kind == NUMERIC_KIND else 0,
                    "alpha_axes": band["member_count"] if kind == ALPHA_KIND else 0})
    return sorted(out, key=lambda z: -(z["numeric_axes"] + z["alpha_axes"]))
