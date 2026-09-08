"""图面标注/非实体识别：把细笔画（引线、尺寸斜线、门扇、标高刻度）从构件候选里摘出来。

## 为什么需要这道判据

柱的定案复测（`data/model3d/gold/columns_final_v1.json`，26 格保留组独立判读）：
**保留下来的柱候选里 62% 是图面标注**，其中标高符号 7/26 = 27%。
全部 559 条误检归一后，22% 是图面标注或根本没有实体
（`docs/GOLD_STANDARD_REVIEW.md` §二.2）。

## 此前为什么失败 —— 两个被否掉的判据，各错在哪

**判据一「按三角形顶点数过滤」**（旧记录：「柱候选里三顶点多边形占 0%」）
量的是轮廓的**原始点数**。填充三角形由三条线段画成，`geometry_extractor`
把每条线段的两个端点都收进轮廓，点数恒为 6 —— 那个 0% 是编码方式的必然，
与图上有没有三角形无关。本轮复量（去掉重复点与共线点后数**真实角点**）：
原始多边形里 **12.6% 是三角形**，构件字典里 3.1%。「测不到三角形」是错的。

但顺着「过滤三角形」走仍然错，因为**三角形不是标注的签名**：本轮实测三角形
集中在结构与隔声隔振平面图（单图 410 / 332 / 244 个），那是构件的剖面填充；
三角形里只有 2.6% 是细条。

**判据二「墨迹填充率」**错得更彻底：它需要轮廓面积，而**构件字典里的轮廓
不是简单多边形**。`_downsample_ring(poly, 8)` 按索引顺序保留极值点，
产出的环会自交；实测 2183 个非细条候选里 **407 个（18.6%）鞋带面积恰为 0**
（且全部是 4 个角点的方块，尺寸 0.49×0.63m —— 正是真柱那一档）。
面积量不出来，填充率就是无意义的数 —— 这才是它当年被数据否掉的原因。

真正分得开的量是**真实范围**（最小面积外接矩形，旋转卡壳），不是轴对齐
包围盒 —— 后者量的是影子。实测样例：

===================  ====================  =======
轴对齐包围盒          真实范围               角点
===================  ====================  =======
0.601 × 0.529 m      0.792 × 0.012 m         5
0.559 × 0.495 m      0.738 × 0.012 m         5
0.879 × 0.861 m      1.197 × 0.067 m         4
===================  ====================  =======

三行的包围盒都落在柱窗口（0.2~1.5m）正中，尺寸判据分不开；真实短边
12~67 毫米，没有哪根柱是这个厚度。旁证：把同一批候选**按包围盒重建**成
轴对齐矩形后再跑本判据，命中 **0/2000** —— 信号确实只在真实范围里。

## 判据

**`thin_stroke`：真实短边 < 0.10m 且真实长宽比 ≥ 8。**

两个阈值都**不是新数**，各自回指识别器已有的断言：

- `0.10m` = `element_recognizer._COLUMN_ABSURD_MIN_M`（「小于 0.1m 的边不是柱」），
  只是那里量包围盒，这里量真实范围；
- `8` = `_COLUMN_LAYER_MAX_ASPECT`（识别器允许的最扁的柱就是 8:1）。

**长宽比这一条是抵御比例错误的唯一防线，不是可有可无的护栏。**
金标准实测全库比例只有 30% 站得住，而短边是米、会跟着错误比例一起缩放；
长宽比不会。任何被识别器自己认可的柱（长宽比 < 4 或图层路径 < 8），
无论比例错多少倍，都不可能被本判据删掉。

阈值敏感度（60 张结构/建筑平面图逐张 `recognize()`，2497 个柱候选）：

=========================  =======
短边阈值（长宽比 ≥3）        命中率
=========================  =======
< 0.08m                     12.3%
< 0.10m                     12.6%
< 0.12m                     13.5%
< 0.15m                     16.1%
< 0.20m                     16.7%   0.15→0.20 几乎不涨（平台）
=========================  =======

=========================  =======
长宽比阈值（短边 <0.10m）    命中率
=========================  =======
≥ 3                         12.6%
≥ 4                         12.3%
**≥ 8**                    **11.6%**   命中项 87% 的长宽比 ≥ 20
=========================  =======

从 3 收到 8 只少删 24 格（7.6% 相对），换来「比例错也不会误删柱」。

## 回测（同一批 120 张图，4376 个柱候选，两个阈值各跑一遍）

=====================  ==========  ===========================
长宽比阈值              命中          逐图
=====================  ==========  ===========================
≥ 3（判读用的那轮）      728 = 16.6%  88 张有候选的图里 52 张一格没删
**≥ 8（当前默认）**     **567 = 13.0%**  88 张里 60 张（68%）一格没删
=====================  ==========  ===========================

当前默认删的是判读那轮的**子集**，所以下面的误伤结论对当前默认同样成立
（少删 161 格，换零误删）。按专业：建筑 14.4% / 结构 11.4%。
命中高度集中：`08 1区、2-1区围护体平面图` 70/70、`07 第四阶段工况平面图` 54/54、
`建筑-竣工图--三层平面图(一)` 50/56 —— 与「误检按图纸聚集」那条结构信号一致。
- **接触表独立判读 50 格**（被删 24 · 保留 16 · 空白对照 10，混发洗牌）：
  - **空白对照 10/10 答对** —— 仪器有效。
  - **被删 24 格：误删 0 格。** 判读内容：细笔画 12 · 门扇 4 · 粗斜线 2 ·
    图签栏文字 2 · 标高刻度斜线 2 · 比例错到看不出所以然 2。
    其中「标高刻度斜线」那 2 格第一遍在 150dpi 下被我判成了实心方块，
    按候选尺度提分辨率重渲后是 0.28×0.07m 的**粗斜线**（长宽比 4）——
    这正是 `CLAUDE.md` 那条「渲染分辨率要匹配问题所在的尺度」；
    判读结论必须跟着改，不能留着好看的数字。
    （长宽比收到 8 之后这 2 格已不再被删 —— 少删一点，换零误删。）
  - 保留 16 格里只有 6 格是柱（38%），错的是标高符号 5 · 箭头 2 ·
    详图符号 2 —— **本判据一个都管不了**，见下。

## 管不了的（说清楚边界，别让它看起来管了）

**标高符号不是细条。** 实测两张图逐格核验：`∨` 是**整个符号作为一个多边形**
进来的，真实范围 **0.32 × 0.32 m**（近方形，长宽比 1.0），落在柱窗口正中。
以「一条斜笔画」形态进来的只是另一种画法。所以本判据对标高符号命中为零，
金标准复跑（`scripts/model3d/annotation_gold_replay.py`）也印证了这一点：
判读答「标高符号/尺寸/文字」的那 8 张图，命中率 **0.0%**。
—— 这是一次与预期相反的实测，按纪律照实写在这里，不改判据去迎合。

**第三个独立口径也是同一结论**：档案 `drawing_extracted_info` 里
`category='elevation'` 的条目数与本判据的命中量，斯皮尔曼 rho 只有 **+0.18**；
有标高条目的 73 张图命中率 16.7%，一条标高条目都没有的 15 张图 16.6% ——
**没有关系**。（只用计数不用坐标：那批 bbox 写在 `ocr/service.py` 的
`effective_dpi` 修好之前，位置不可信。见 `scripts/model3d/annotation_crosscheck.py`。）

**文字笔画在构件字典层面量不出来。** `_downsample_ring(poly, 8)` 把轮廓压到
8 点，实测 2497 个柱候选里 `corners > 8` 的是 **0 个**。角点判据
（`stroke_cluster`，真实角点 > 24）只在调用方**另外传入原始多边形**
（`raw_outlines`）时才生效；原始多边形上实测 `corners > 24` 占 2.0%。
不传就不判，不假装判过。

**墙/梁不在范围内**：它们是 `path` + `width`（平行线对），没有 `outline`，
本模块一律返回 None。

## 纪律与代价

判据只**标记**、由调用方决定删不删，并把量出来的数带在 `AnnotationFlag`
里（`MODELING_PIPELINE_BLUEPRINT.md` §7：降级必须可见）。
与 `dense_array_filter`（座椅闸）正交：实测密排阵列组 269 格里本判据命中 0 格。
耗时：2000 个候选（单图上限）**18ms**，占单图识别超时的 0.09%。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

#: 真实短边下限（米）。与 `element_recognizer._COLUMN_ABSURD_MIN_M` 同值 ——
#: 那里量包围盒，这里量真实范围。见模块文档的阈值实测表。
DEFAULT_MIN_SHORT_M = 0.10
#: 判为「细条」还要求真实长宽比不小于此值。与 `_COLUMN_LAYER_MAX_ASPECT` 同值 ——
#: 识别器允许的最扁的柱就是 8:1。**长宽比与比例无关**，是本判据抵御
#: 「全库比例只有 30% 站得住」的唯一防线。见模块文档的敏感度表。
DEFAULT_MIN_THIN_ASPECT = 8.0
#: 真实角点上限。真柱 4~6，图签栏笔画中位 96。只在传入原始多边形时生效。
DEFAULT_MAX_CORNERS = 24

_EPS = 1e-9
#: 判为同一个点的距离（米）。图纸坐标只保留到毫米（`_Ctx.to_m` 四舍五入 3 位）。
_SAME_POINT_M = 1e-4


@dataclass(frozen=True)
class AnnotationFlag:
    """一条判定。`reason` 是判据名，`detail` 是量出来的数（不静默）。"""

    reason: str
    long_m: float
    short_m: float
    corners: int
    detail: str


# ---------------------------------------------------------------------------
# 几何原语（普查脚本 `scripts/model3d/annotation_census.py` 用的也是这几个）
# ---------------------------------------------------------------------------

def _as_points(outline) -> list[tuple[float, float]]:
    pts: list[tuple[float, float]] = []
    for p in outline or []:
        try:
            pts.append((float(p[0]), float(p[1])))
        except (TypeError, ValueError, IndexError, KeyError):
            return []
    return pts


def convex_hull(points) -> list[tuple[float, float]]:
    """单调链凸包。少于 3 个不同点时原样返回（退化输入不抛错）。"""
    pts = sorted(set(_as_points(points)))
    if len(pts) < 3:
        return pts

    def _half(seq):
        out: list[tuple[float, float]] = []
        for p in seq:
            while len(out) >= 2:
                o, a = out[-2], out[-1]
                cross = (a[0] - o[0]) * (p[1] - o[1]) - (a[1] - o[1]) * (p[0] - o[0])
                if cross > _EPS:
                    break
                out.pop()
            out.append(p)
        return out

    lower = _half(pts)
    upper = _half(list(reversed(pts)))
    return lower[:-1] + upper[:-1]


def min_area_rect(points) -> tuple[float, float]:
    """旋转卡壳求最小面积外接矩形，返回 (长边, 短边)，单位同入参。

    **量物体不量影子**：轴对齐包围盒对斜放的细条会给出近方形的尺寸，
    这正是标高符号混进柱候选的方式（见模块文档）。
    """
    hull = convex_hull(points)
    if len(hull) < 3:
        if len(hull) == 2:
            return math.dist(hull[0], hull[1]), 0.0
        return 0.0, 0.0
    best: tuple[float, float] | None = None
    for i in range(len(hull)):
        a, b = hull[i], hull[(i + 1) % len(hull)]
        dx, dy = b[0] - a[0], b[1] - a[1]
        edge = math.hypot(dx, dy)
        if edge < _EPS:
            continue
        ux, uy = dx / edge, dy / edge
        us = [p[0] * ux + p[1] * uy for p in hull]
        vs = [-p[0] * uy + p[1] * ux for p in hull]
        w = max(us) - min(us)
        h = max(vs) - min(vs)
        if best is None or w * h < best[0] * best[1]:
            best = (w, h)
    if best is None:
        return 0.0, 0.0
    return max(best), min(best)


def corner_points(points, angle_tol: float = 0.05) -> list[tuple[float, float]]:
    """去掉重复点与共线点后的**真实角点**。

    填充三角形由三条线段画成，抽取器把每条线段的两个端点都收进轮廓，
    点数恒为 6 —— 直接数点会把三角形数成六边形（旧结论「三顶点多边形
    占 0%」就是这么来的，见模块文档）。
    """
    pts = _as_points(points)
    dedup: list[tuple[float, float]] = []
    for p in pts:
        if not dedup or math.dist(p, dedup[-1]) > _SAME_POINT_M:
            dedup.append(p)
    while len(dedup) > 1 and math.dist(dedup[0], dedup[-1]) <= _SAME_POINT_M:
        dedup.pop()
    n = len(dedup)
    if n < 3:
        return dedup
    keep: list[tuple[float, float]] = []
    for i in range(n):
        a, b, c = dedup[i - 1], dedup[i], dedup[(i + 1) % n]
        v1 = (b[0] - a[0], b[1] - a[1])
        v2 = (c[0] - b[0], c[1] - b[1])
        n1, n2 = math.hypot(*v1), math.hypot(*v2)
        if n1 < _EPS or n2 < _EPS:
            continue
        cross = abs(v1[0] * v2[1] - v1[1] * v2[0]) / (n1 * n2)
        if cross > angle_tol:
            keep.append(b)
    return keep if len(keep) >= 3 else dedup


def polygon_area(points) -> float:
    """鞋带公式，与绕向无关。"""
    pts = _as_points(points)
    if len(pts) < 3:
        return 0.0
    total = 0.0
    for i in range(len(pts)):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % len(pts)]
        total += x1 * y2 - x2 * y1
    return abs(total) / 2


# ---------------------------------------------------------------------------
# 判据
# ---------------------------------------------------------------------------

def find_annotation_flags(
    elements: list[dict] | None,
    *,
    raw_outlines: list | None = None,
    min_short_m: float = DEFAULT_MIN_SHORT_M,
    min_thin_aspect: float = DEFAULT_MIN_THIN_ASPECT,
    max_corners: int = DEFAULT_MAX_CORNERS,
) -> list[AnnotationFlag | None]:
    """逐候选判断是否是图面标注/非实体。返回与入参等长的表；入参不被修改。

    `raw_outlines` 是与 `elements` 等长的**未降点**多边形表（缺项给 None）。
    只有传了它，`stroke_cluster` 判据才可能触发 —— 构件字典里的轮廓已被
    `_downsample_ring` 压到 8 点，角点判据在那上面恒不成立（实测 0/2497）。

    只**标记**、由调用方决定删不删（降级必须可见）。
    """
    items = list(elements or [])
    raws = list(raw_outlines or [])
    flags: list[AnnotationFlag | None] = []
    for i, el in enumerate(items):
        raw = raws[i] if i < len(raws) else None
        flags.append(_flag_one(el, raw, min_short_m, min_thin_aspect, max_corners))
    return flags


def _flag_one(element, raw_outline, min_short_m, min_thin_aspect, max_corners):
    outline = element.get("outline") if isinstance(element, dict) else None
    pts = _as_points(outline)
    if len(pts) < 3:
        return None
    long_m, short_m = min_area_rect(pts)
    if long_m <= _EPS:
        return None
    corners = len(corner_points(pts))
    aspect = long_m / max(short_m, 1e-6)
    if short_m < min_short_m and aspect >= min_thin_aspect:
        return AnnotationFlag(
            reason="thin_stroke", long_m=long_m, short_m=short_m, corners=corners,
            detail=(f"真实范围 {long_m:.3f}×{short_m:.3f}m（长宽比 {aspect:.1f}）"
                    f"，短边小于 {min_short_m}m —— 细条不是构件截面"),
        )
    raw_pts = _as_points(raw_outline)
    if len(raw_pts) >= 3:
        raw_corners = len(corner_points(raw_pts))
        if raw_corners > max_corners:
            return AnnotationFlag(
                reason="stroke_cluster", long_m=long_m, short_m=short_m,
                corners=raw_corners,
                detail=(f"原始多边形真实角点 {raw_corners} 个，超过 {max_corners}"
                        f" —— 真柱 4~6 个，笔画/格线才这么多"),
            )
    return None
