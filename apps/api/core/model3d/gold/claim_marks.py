"""把「系统读数」画成一格上的红标记 —— 标高框、轴线、坐标锚点。

与构件类批次的区别：构件候选来自 `recognize()`，这三类来自**库里已存的读数**
（`drawing_extracted_info.category='elevation'`、`axis_recognition.axes`
与 `.anchors`）。判读者要回答的不是「这是不是构件」，而是
**「系统在这里读出的东西，图上真有吗、是不是这个数」**。

## 档案 bbox 不是页面点

2026-08-31（`ce05751`）之前写入的档案 OCR 位置，是在「最长边缩到 2160」的
画布上量的。全库的档案 OCR 都写于此前（实测最新一批 2026-08-19），所以
**每一条都要换算**：`k = max(page_w, page_h) / 2160`，bbox 乘 k 即页面点。
八格随机抽检逐格套准（12.600 / 14.500 / 6.500 / 2.160 / −1.644 / 3.750）。

`scale_evidence.printed_scale_from_archive` 用的是另一种归一化（按本图 OCR
词条范围），因为它只需要判断「在不在图框带」这种粗位置；裁图要的是**准位置**，
按词条范围归一化会随「图上文字铺到多远」漂移，所以这里按画布换算。
"""
from __future__ import annotations

import math

from core.model3d.axis_normal import normal_vector
from core.model3d.gold.batch_design import Mark

#: 档案 OCR 的旧画布长边（像素）。
LEGACY_LONG_SIDE = 2160.0

Box = tuple[float, float, float, float]


def archive_bbox_to_page(bbox: Box, *, page_w: float, page_h: float) -> Box:
    """档案 bbox（旧画布）→ 页面点。页面尺寸不可用时原样返回。"""
    longest = max(page_w, page_h)
    if longest <= 0:
        return bbox
    k = longest / LEGACY_LONG_SIDE
    return (bbox[0] * k, bbox[1] * k, bbox[2] * k, bbox[3] * k)


def elevation_mark(bbox: Box, *, page_w: float, page_h: float) -> Mark:
    """标高读数 → 框住那段文字的红框。"""
    return Mark("box", archive_bbox_to_page(bbox, page_w=page_w, page_h=page_h))


def axis_segment(angle_deg: float, offset_pt: float, *, page_w: float, page_h: float,
                 at: float = 0.5, span_pt: float = 350.0) -> Mark | None:
    """轴线（方向角 + 法向偏移）→ 页面内一段红线。

    `at` 是取哪一段：0=线进入页面处，1=离开处，0.5=正中。整条轴线常横跨
    三千多点，一格里画全就成了一条贴边的直线，什么也判不出 —— 所以只画
    `span_pt` 长的一段，裁框跟着这一段走。

    段长要够：判轴线的依据是「贯穿、两端伸出、构件沿它排列」，200pt 的一截
    只看得见线型（而图上的轴线往往很淡），看不出它贯不贯穿 —— 实测按 200pt
    出的探针，十二格里我自己只能确判两三格。

    线完全在页面外返回 None（不假装画了）。
    """
    rad = math.radians(angle_deg)
    dx, dy = math.cos(rad), math.sin(rad)
    nx, ny = normal_vector(angle_deg)
    px, py = offset_pt * nx, offset_pt * ny          # 线上离原点最近的点
    lo_t, hi_t = -math.inf, math.inf
    for lo, hi, p, d in ((0.0, page_w, px, dx), (0.0, page_h, py, dy)):
        if abs(d) < 1e-9:                             # 与这一维平行
            if not lo <= p <= hi:
                return None                           # 且落在页面带之外
            continue
        a, b = (lo - p) / d, (hi - p) / d
        lo_t, hi_t = max(lo_t, min(a, b)), min(hi_t, max(a, b))
    if not math.isfinite(lo_t) or not math.isfinite(hi_t) or hi_t <= lo_t:
        return None
    t0, t1 = lo_t, hi_t
    mid = t0 + (t1 - t0) * at
    a = mid - span_pt / 2, mid + span_pt / 2
    lo, hi = max(t0, a[0]), min(t1, a[1])
    if hi <= lo:
        lo, hi = t0, t1
    p0 = (px + dx * lo, py + dy * lo)
    p1 = (px + dx * hi, py + dy * hi)
    # **虚线而不是实线**：轴线的判据是「底下那条是不是点划线」，
    # 实心红线会把它完全盖住 —— 判读者只能看见我画的线，判不了图上的线。
    return Mark("dashed_line", (min(p0[0], p1[0]), min(p0[1], p1[1]),
                                max(p0[0], p1[0]), max(p0[1], p1[1])), line=(p0, p1))


def anchor_mark(x_norm: float, y_norm: float, *, page_h: float,
                arm_pt: float = 28.0) -> Mark:
    """世界坐标锚点（归一化位置）→ 红十字。

    **两个坐标都同除页高**（`services/axis_world_anchors.py:78`），不是各除
    各的边长 —— 所以横向页面上 `x_norm` 大于 1 是正常的（实测 35 个锚点里
    16 个如此）。按页宽还原会把十字甩到页面外，整格看不见标记而判读者
    浑然不觉：**把系统的坐标口径当成常识，量出来的就是自己的 bug**。

    锚点断言的是「**这一点**是某轴线交点、工程坐标是 X/Y」，画框会让判读者
    去看框里有什么；画十字才问得出「十字打在哪」。
    """
    cx, cy = x_norm * page_h, y_norm * page_h
    return Mark("cross", (cx - arm_pt, cy - arm_pt, cx + arm_pt, cy + arm_pt),
                line=((cx, cy), (cx, cy)))
