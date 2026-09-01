"""多边形的**真实尺寸**：最小面积外接矩形，而不是它在坐标轴上的影子。

## 为什么需要这个量

`_is_column_size` / `_is_plausible_column` 判的是宽、高与长宽比，取自
**轴对齐包围盒**（AABB）。对矩形图元这是精确的（`re` 本来就轴对齐），
对**多边形**却是它的影子 —— 一条 45° 的细条，影子是方的。

标高符号正是栽在这里。实测（`col_sheet/quad4.png`，按形状分层渲染后
逐格看图）：隔声隔振平面图上的 `∨` 形标高符号，**每一条笔画**单独成为
一个填充多边形进到识别器，包围盒 0.52×0.59m —— 长宽比 1.13、边长落在
柱窗口 (0.2~1.5m) 正中间。任何基于包围盒的尺寸判据都不可能把它挑出来。
换成真实尺寸后它是 0.7×0.12m 这一档，短边低于柱下限。

## 与两个已被否掉的判据的关系

**不是「三角形顶点数」**。2026-08-28 记录的「候选里三顶点多边形占 0%」
测的是 `outline` 的**原始点数**：填充三角形由 3 条线段画成，
`geometry_extractor` 把每条线段的两个端点都收进 `path_points`，
点数恒为 6，永远不等于 3 —— 那个 0% 说的是编码方式，不是形状。
按位置去重后重测，落库的 24664 根柱里 **13629 根（55.3%）只有 3 个
相异顶点**，三角形一直都在。但看图后发现三角形也不是标高符号的进入
形式（标高符号是斜细条，4 个角），所以顶点数这条路即使修正口径也没走通。

**不是「框内墨迹占比」**。那个判据要栅格化数暗像素，会把框内穿过的
别的线、柱内部的剖面填充图案一起数进去（实测真柱中位 0.54、误检 0.33，
分布重叠，任何阈值两头不讨好）。这里是多边形自身的解析几何量，
与邻居无关、与填充图案无关，也不需要渲染。

## 判据与阈值

**本模块不引入任何新阈值**。它只把量错的尺寸量对，量出来的
(长边, 短边) 交回调用方，套用调用方原有的窗口 —— 图层路径仍走
`_is_plausible_column`（0.1~3.0m / 8:1），猜测路径仍走
`_is_column_size`（0.2~1.5m / 4:1）。这一点是**必须**的：实测
（`str_sheet/gate_drop.png`）结构平面图上有 0.18×0.18m、正落在轴线
交点上的方块，按柱图层进来的真柱；若把猜测路径的窗口套到它头上，
这道闸就会删掉真柱。

## 实测的删除量与误伤

**删除 5.9%**（`probe_before_after.py`，32 张图 1942 个候选 → 1828）。
同一进程、同一张图，只把本模块的 `min_area_rect` 换成轴对齐口径即复现
改前行为，所以差值可完全归因于本判据。按图种：平面图 4.2%、其他 8.6%；
**32 张里 22 张一根没删** —— 打的是特定形态，不是普遍收紧。
另有 1 张图候选**增加**：旋转柱的包围盒会超出 1.5m 上限而被拒，
真实尺寸下反而通过。

**误伤：目视 30 格、0 格是柱**（`probe_delta_sheet.py` 渲染产品路径的差集）。
删掉的是吊顶/家具轮廓细条、墙角楔形填充、尺寸线与箭头、图签栏格线、
说明文字，以及斜板上厚 0.06~0.08m 的细条。

**不要用落库场景的数字汇报本判据**：对同一批图，2026-08-20 落库的 6359 根
里 **69.5% 早已被此后各道闸删掉**，在那个旧总体上本判据算出的是
31.3%~45.7%（`probe_scene_columns.py`）—— 高估五倍。那组数只用来看
形状分布，不用来看效果。

## 代价

单个多边形 17~256 µs（4~64 点，本机实测）。`MAX_PRIMITIVES` = 20000 封顶，
最坏情形（两万个 64 点多边形）5.1 秒，对 `_RECOGNIZE_TIMEOUT_SEC`=60s 是 8.5%；
典型（8 点）0.7 秒。

## 凸包是必须的

`path_points` 按绘制顺序堆点，可能自交。对自交点序直接求面积会算出
近 0（实测 33% 的候选面积比 <0.05，而肉眼看是实心的）。走凸包之后
结果与点序无关。凸包对本用途无损：外接矩形本来就只由外轮廓决定。

复杂度 O(n log n) 排序 + O(h²) 旋转卡壳。`outline` 经
`_downsample_ring` 限到 8 点，h ≤ 8，代价可忽略。
"""
from __future__ import annotations

import math

#: 判定两点重合 / 三点共线的容差（与坐标同量纲的相对量）。
_EPS = 1e-12


def convex_hull(points: list) -> list:
    """Andrew monotone chain → 逆时针凸包顶点（不含重复的首尾点）。

    点数不足 3 或全部共线时返回少于 3 个点，由调用方判为退化。
    """
    pts = sorted({(float(x), float(y)) for x, y in points})
    if len(pts) <= 2:
        return pts

    def cross(o, a, b) -> float:
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: list = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= _EPS:
            lower.pop()
        lower.append(p)
    upper: list = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= _EPS:
            upper.pop()
        upper.append(p)
    # 全部共线时 monotone chain 只剩两端点 —— **原样返回，不要兜底成输入点**。
    # 兜底会让共线退化伪装成一个「有 4 个点的多边形」，短边算出 0，
    # 退化就此静默通过。
    return lower[:-1] + upper[:-1]


def min_area_rect(points: list) -> tuple[float, float] | None:
    """→ (长边, 短边)，即最小面积外接矩形的两条边长；退化时 None。

    旋转卡壳：最小面积外接矩形必有一条边与凸包的某条边共线，
    所以沿凸包每条边定向投影一遍取最小即可。
    """
    try:
        hull = convex_hull(points)
    except (TypeError, ValueError, IndexError):
        return None
    if len(hull) < 3:
        return None
    best: tuple[float, float] | None = None
    for i, (ax, ay) in enumerate(hull):
        bx, by = hull[(i + 1) % len(hull)]
        ex, ey = bx - ax, by - ay
        edge = math.hypot(ex, ey)
        if edge <= 0:
            continue
        ux, uy = ex / edge, ey / edge
        along = [p[0] * ux + p[1] * uy for p in hull]
        across = [-p[0] * uy + p[1] * ux for p in hull]
        w = max(along) - min(along)
        h = max(across) - min(across)
        if best is None or w * h < best[0] * best[1]:
            best = (w, h)
    if best is None:
        return None
    return max(best), min(best)
