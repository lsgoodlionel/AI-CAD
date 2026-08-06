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


def find_circles(paths: list[dict],
                 tol: float = DIAMETER_CLUSTER_TOLERANCE_PT) -> dict:
    """一步到位:path 列表 → {circles, diameter_pt, dropped, standard}。

    只保留众数直径的圈,离群计入 `dropped`。
    """
    cands = circle_candidates(paths)
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
        paths.append({"rect": (r.x0, r.y0, r.x1, r.y1),
                      "kinds": [it[0] for it in p["items"]]})
    return paths, page.rect.width, page.rect.height


def circles_from_pdf(pdf_bytes: bytes) -> dict:
    """PDF → 轴号圈。无矢量数据时优雅降级为空结果。"""
    paths, page_w, page_h = paths_from_pdf(pdf_bytes)
    if not paths:
        return {"circles": [], "diameter_pt": 0.0, "dropped": 0,
                "standard": False, "diameter_mm": 0.0,
                "page_w": page_w, "page_h": page_h}
    return {**find_circles(paths), "page_w": page_w, "page_h": page_h}
