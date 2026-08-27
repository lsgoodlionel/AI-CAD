"""轴号圈识别:定位轴线编号所在的圆,并据此锚定轴线。

**为什么这是关键路径**:GB/T 50001 §8.0.2 规定「定位轴线的编号宜注写在
平面图**下方及左侧**,轴号应注写在轴线端部的**圆内**,**圆心应在定位轴线的
延长线上**」。也就是说——**一个圈 = 一条轴线**,而且圈心给出了轴线的精确位置。

于是识别架构可以反转:不再「先找线、再猜哪些是轴线」(靠几何阈值,易过检漏检),
而是「**先定圈(确定性)、每个圈锚定一条轴线**」。圈还同时充当量器:

    圈 ↔ 线 都有   → 确认轴线(高置信)
    圈 有、线 无   → **漏检,但位置和方向已知,可直接补出**
    线 有、圈 无   → 疑似误检

**实测依据**(A-01-02A / 03A / 04A 三张 A0 定位图):

    图纸        全弧方形 path    直径分布
    A-01-02A    108 个           28.0pt × 108          (= 9.88mm,§8.0.2 合规)
    A-01-03A    107 个           28.0pt × 107
    A-01-04A    129 个           16.0pt × 126 + 3 离群

三张图**只存在 4 弧一种形式**,放宽弧数只会引入噪声;而**图内直径完全均匀,
跨图却不同**(28.0 vs 16.0)——所以直径必须按**图内众数**判定,不能硬编码常量。

此前三次圈定位失败(同 path 分组 / 包围盒中心聚类 / 拟合圆心聚类)的共同原因是
**在跨 path 之间聚类圆弧**;而圈本身就是一个完整 path,根本不需要聚类。
"""
from __future__ import annotations

import math

from core.model3d.axis_normal import normal_offset
from core.model3d.drawing_conventions import LABEL_CIRCLE_DIAMETER_MM

#: 贝塞尔画圆的标准段数(每段 90°)。实测三张图无其他形式
ARCS_PER_CIRCLE = 4

#: 包围盒方形容差。实测正方形严格相等,留 3% 余量应对导出误差
SQUARE_TOLERANCE = 0.03

#: 同一批圈归为「同一直径」的容差(pt)。实测同批直径 28.02 完全一致
DIAMETER_CLUSTER_TOLERANCE_PT = 0.5

#: 圈的物理尺度下限/上限(pt)。用于排掉标注符号里的小圆点与大装饰圆,
#: 不作为国标合规判据——A-01-04A 的 16.0pt 低于国标但确实是轴号圈
MIN_DIAMETER_PT = 6.0
MAX_DIAMETER_PT = 60.0

#: §8.0.2 轴号圆直径 8~10mm(用于合规提示,不用于过滤)。
#: 单一来源在 `drawing_conventions` —— 同一条国标不在两处各写一遍
STANDARD_DIAMETER_MM = LABEL_CIRCLE_DIAMETER_MM

#: 轴号圈到**轴线端点**的最大距离，按半径的比例。
#:
#: GB/T 50001 §8.0.2:「编号注写在轴线端部的圆内…**圆心应在定位轴线的
#: 延长线上**」⇒ 轴线画到圈心附近；而桩、钢立柱是孤立的圆，圆内没有线。
#:
#: **实测依据**(三张真值图 + 两张误检图，见 `tests/test_axis_circle_axis_proximity.py`):
#: 真值图在 0.30r 处**全部跳到 100%**(0.20r 时只有 56~76%)，
#: 而基坑图 29.7%、围护体图 46.0% —— 分界尖锐，不是凑出来的阈值。
#:
#: 没有这条判据时，「58 基础底板换撑平面布置图」报出 **862 个圈**，
#: 而真正的轴网定位图只有 108 个。
AXIS_PROXIMITY_MAX_RATIO = 0.30

#: 圈心到轴线的最大法向距离(pt)。实测最小轴距 4500mm ≈ 26pt(1:350),
#: 容差必须远小于它,否则会把圈串到相邻轴线上
CIRCLE_TO_AXIS_TOLERANCE_PT = 3.0

_PT_PER_MM = 72.0 / 25.4


def diameter_mm(diameter_pt: float) -> float:
    """pt → mm(图纸出图尺寸)。"""
    return round(diameter_pt / _PT_PER_MM, 2)


def is_standard_diameter(diameter_pt: float) -> bool:
    """是否落在 §8.0.2 规定的 8~10mm。仅作合规提示。"""
    lo, hi = STANDARD_DIAMETER_MM
    return lo <= diameter_mm(diameter_pt) <= hi


def circle_candidates(paths: list[dict]) -> list[dict]:
    """path 列表 → 圆候选。

    paths 每项形如 {"rect": (x0, y0, x1, y1), "kinds": ["c", "c", "c", "c"]},
    `kinds` 是该 path 内图元类型序列(与 fitz `get_drawings()["items"]` 对齐)。

    判据:**全部图元为贝塞尔弧 + 恰 4 段 + 包围盒正方形 + 尺度在物理区间内**。
    """
    out = []
    for p in paths:
        kinds = p.get("kinds") or []
        if len(kinds) != ARCS_PER_CIRCLE or any(k != "c" for k in kinds):
            continue
        x0, y0, x1, y1 = p["rect"]
        w, h = x1 - x0, y1 - y0
        if w <= 0 or h <= 0:
            continue
        if abs(w - h) / max(w, h) > SQUARE_TOLERANCE:
            continue
        if not (MIN_DIAMETER_PT <= w <= MAX_DIAMETER_PT):
            continue
        out.append({
            "cx": round((x0 + x1) / 2, 3),
            "cy": round((y0 + y1) / 2, 3),
            "diameter_pt": round((w + h) / 2, 2),
        })
    return out


def dominant_diameter(circles: list[dict],
                      tol: float = DIAMETER_CLUSTER_TOLERANCE_PT) -> float:
    """图内直径众数。

    **不能用均值**:A-01-04A 有 126 个 16.0pt 加 3 个离群(32.0/29.6/13.6),
    均值会被拉偏。按容差分桶取最大桶的均值。
    """
    if not circles:
        return 0.0
    buckets: list[list[float]] = []
    for d in sorted(c["diameter_pt"] for c in circles):
        if buckets and abs(d - buckets[-1][-1]) <= tol:
            buckets[-1].append(d)
        else:
            buckets.append([d])
    best = max(buckets, key=len)
    return round(sum(best) / len(best), 2)


#: 判定「水平直径线」的容差。
#: 线段近水平：两端 y 差不超过此值（pt）。
INDEX_LINE_FLATNESS_PT = 1.5
#: 线段所在高度与圈心的偏差不超过半径的此比例 —— 直径线过圆心。
INDEX_LINE_CENTER_RATIO = 0.18
#: 线段横向必须贯通圆的此比例以上 —— 分数式附加轴线的分数线只占小半。
INDEX_LINE_SPAN_RATIO = 1.2


def drop_index_symbol_circles(circles: list[dict] | None,
                              segments: list | None) -> list[dict]:
    """丢掉**详图索引符号**——它们不是定位轴线圈。

    GB/T 50001 §6：索引符号用细实线画**水平直径**把圆分成上下两半
    （上半=详图编号，下半=图纸编号）；§8.0.2 的定位轴线圈里只有编号，
    没有这条线。

    **实测**（轨道交通「首层框架梁平面整体配筋图」）：一整排索引符号被
    读成了「1~6 轴」，构成该图轴号识别的全部误检（精确率 72.7%）。

    判据要同时满足三样，少一样都会误伤：
      * 线段**近水平** —— 否则 §8.0.2 引到圈心的轴线会被当成直径；
      * 高度**过圈心** —— 否则圈外的尺寸线会命中；
      * 横向**贯通全圆** —— 否则 §8.0.6 分数式附加轴线的分数线会命中。

    **取不到线段时原样返回**：判不出就不判，不能把整张图清空。
    """
    if not circles or not segments:
        return list(circles or [])

    flat = [((x0, y0), (x1, y1)) for (x0, y0), (x1, y1) in segments
            if abs(y0 - y1) <= INDEX_LINE_FLATNESS_PT]
    if not flat:
        return list(circles)

    kept = []
    for circle in circles:
        cx, cy = circle["cx"], circle["cy"]
        radius = float(circle.get("diameter_pt") or 0.0) / 2.0
        if radius <= 0:
            kept.append(circle)
            continue
        crossed = any(
            abs((y0 + y1) / 2.0 - cy) <= radius * INDEX_LINE_CENTER_RATIO
            and min(x0, x1) <= cx - radius * (INDEX_LINE_SPAN_RATIO / 2)
            and max(x0, x1) >= cx + radius * (INDEX_LINE_SPAN_RATIO / 2)
            for (x0, y0), (x1, y1) in flat)
        if not crossed:
            kept.append(circle)
    return kept


def find_circles(paths: list[dict],
                 tol: float = DIAMETER_CLUSTER_TOLERANCE_PT) -> dict:
    """一步到位:path 列表 → {circles, diameter_pt, dropped, standard}。

    只保留众数直径的圈,离群计入 `dropped`。
    """
    cands = circle_candidates(paths)
    # **必须在选主导直径之前过滤**：桩多时桩径会成为众数，
    # 把整张图的检测带偏（实测基坑图 862 个圈全是桩）。
    endpoints = [pt for p in paths for pt in (p.get("line_points") or ())]
    cands = filter_circles_near_axes(cands, endpoints)
    # §6 索引符号（带水平直径线的圆）不是定位轴线圈
    segments = [(endpoints[i], endpoints[i + 1])
                for i in range(0, len(endpoints) - 1, 2)]
    cands = drop_index_symbol_circles(cands, segments)
    dom = dominant_diameter(cands, tol)
    kept = [c for c in cands if abs(c["diameter_pt"] - dom) <= tol]
    return {
        "circles": kept,
        "diameter_pt": dom,
        "dropped": len(cands) - len(kept),
        "standard": is_standard_diameter(dom) if dom else False,
        "diameter_mm": diameter_mm(dom) if dom else 0.0,
    }


# ── 圈 → 轴线(§8.0.2 圆心在轴线延长线上)──────────────────────

def circle_offsets(circles: list[dict], angle_deg: float) -> list[float]:
    """圈心在指定方向上的法向偏移——与轴线用同一套法向,才能配得上。"""
    return [normal_offset(c["cx"], c["cy"], angle_deg) for c in circles]


def assign_circles_to_axes(
    circles: list[dict],
    axes: list[dict],
    angle_deg: float,
    tol: float = CIRCLE_TO_AXIS_TOLERANCE_PT,
) -> dict:
    """把圈配到轴线上,并给出三类度量信号。

    返回:
        confirmed          —— 有圈支撑的轴线下标(高置信)
        axes_without_circle—— 无圈支撑的轴线下标(**疑似误检**)
        orphan_circles     —— 未配到轴线的圈下标(**漏检,可由圈补出**)
        circles_per_axis   —— {轴线下标: [圈下标]}(一条轴线两端可各有一圈)
    """
    axis_offsets = [a["offset_pt"] for a in axes]
    per_axis: dict[int, list[int]] = {}
    orphans: list[int] = []

    for ci, off in enumerate(circle_offsets(circles, angle_deg)):
        if not axis_offsets:
            orphans.append(ci)
            continue
        best = min(range(len(axis_offsets)),
                   key=lambda ai: abs(axis_offsets[ai] - off))
        if abs(axis_offsets[best] - off) <= tol:
            per_axis.setdefault(best, []).append(ci)
        else:
            orphans.append(ci)

    confirmed = sorted(per_axis)
    return {
        "confirmed": confirmed,
        "axes_without_circle": [i for i in range(len(axes)) if i not in per_axis],
        "orphan_circles": orphans,
        "circles_per_axis": per_axis,
    }


# ── IO 层(优雅降级)──────────────────────────────────────────────

def paths_from_pdf(pdf_bytes: bytes) -> tuple[list[dict], float, float]:
    """PDF 首页 → (path 记录列表, 页宽 pt, 页高 pt)。解析失败返回空。"""
    try:
        import fitz
    except ImportError:                                    # pragma: no cover
        return [], 0.0, 0.0
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        page = doc[0]
    except Exception:                                      # pragma: no cover
        return [], 0.0, 0.0
    paths = []
    for p in page.get_drawings():
        r = p["rect"]
        # 线段端点供 §8.0.2 邻近判据用（`filter_circles_near_axes`）。
        # 放在 path 记录里而不是改签名，老调用方不受影响。
        points = [(pt.x, pt.y) for it in p["items"] if it[0] == "l"
                  for pt in (it[1], it[2])]
        paths.append({"rect": (r.x0, r.y0, r.x1, r.y1),
                      "kinds": [it[0] for it in p["items"]],
                      "line_points": points})
    return paths, page.rect.width, page.rect.height


def circles_from_pdf(pdf_bytes: bytes) -> dict:
    """PDF → 轴号圈。无矢量数据时优雅降级为空结果。"""
    paths, page_w, page_h = paths_from_pdf(pdf_bytes)
    if not paths:
        return {"circles": [], "diameter_pt": 0.0, "dropped": 0,
                "standard": False, "diameter_mm": 0.0,
                "page_w": page_w, "page_h": page_h}
    return {**find_circles(paths), "page_w": page_w, "page_h": page_h}


def filter_circles_near_axes(
    circles: list[dict] | None, line_endpoints: list[tuple[float, float]] | None,
    *, max_ratio: float = AXIS_PROXIMITY_MAX_RATIO,
) -> list[dict]:
    """只留**贴着轴线**的圈（§8.0.2 圆心在定位轴线的延长线上）。

    判据是「圈心到最近线段端点的距离 ≤ 半径 × max_ratio」——
    按半径的**比例**而非绝对值，才能同时适配实测的 16.0pt 与 28.0pt 圈径。

    **取不到线段端点时原样返回**：判不出就不判，不能把整张图清空。
    """
    if not circles:
        return [] if circles is None else circles
    if not line_endpoints:
        return circles

    # 空间哈希：862 圈 × 96 万端点直接两两算不现实。
    cell = max(1.0, max(float(c.get("diameter_pt") or 0) for c in circles))
    grid: dict[tuple[int, int], list[tuple[float, float]]] = {}
    for x, y in line_endpoints:
        grid.setdefault((int(x // cell), int(y // cell)), []).append((x, y))

    kept: list[dict] = []
    for circle in circles:
        cx = float(circle.get("cx") or 0.0)
        cy = float(circle.get("cy") or 0.0)
        radius = float(circle.get("diameter_pt") or 0.0) / 2
        if radius <= 0:
            continue                       # 退化圈不参与
        # 加浮点容差:实测三张真值图**恰在 0.30r 处**跳到 100%，
        # 边界必须含在内，不能被 1e-15 的误差挤出去。
        limit = radius * max_ratio + 1e-9
        gx, gy = int(cx // cell), int(cy // cell)
        found = False
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for px, py in grid.get((gx + dx, gy + dy), ()):
                    if math.hypot(px - cx, py - cy) <= limit:
                        found = True
                        break
                if found:
                    break
            if found:
                break
        if found:
            kept.append(circle)
    return kept
