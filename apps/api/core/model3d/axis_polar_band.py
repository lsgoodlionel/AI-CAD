"""极坐标轴网:放射轴线的定心与等角分组。

**为什么直线带模型不适用**:实测 A-01-03A 的 107 个轴号圈里,直线带只吃掉 66 个。
渲图后看清原因——这是**放射轴网**:放射线汇聚于一点,轴号圈绕中心**等角排布**
(外围 95 个,角间距中位 4.3°,开头连续 7 个都是 4.4°)。

**正确的模型**是把直线带的两个维度换掉:

    直线带    沿带位置(along) + 法向偏移(offset)
    极坐标带  **角度**         + **半径**

同一角度上不同半径的两个圈,是**同一条径向轴线**的两个标注点——正如直线轴线
可以两端各注一个圈(§8.0.2)。

**圆心怎么定**:用「同角度圈对」计分。正确圆心下大量圈对角度差接近 0
(实测角间距序列里出现连片的 0.0°);圆心一偏,这种一致性立刻散掉。
判据只用圈本身,不依赖能否把放射线从 8 万条碎段里挑出来。

**能力边界(实测,不许含糊)**:规律度判据是个好**分类器**,不是好**定位器**。

    A-01-03A(放射)规律度 0.76~0.88 → 通过;A-01-02A / A-01-04A(正交)0.47 / 0.44 → 排除
    但它的极大值**不在真圆心**:搜索返回处规律度 0.875,真圆心只有 0.756

用圆心的**工程坐标反算**(独立于任何几何判据)裁决:真页面位置 (1670.1, 1068.4)。

    渲图目测      差  6.8pt
    中垂线投票     差 16.2pt
    规律度搜索     差 67.2pt   ← 半径 900pt 上是 4.3°,与 4.4° 角间距同量级

**所以现在只能用它判「是不是放射轴网」并给粗中心,不能直接拿去派轴号**
——错 4.3° 会让整圈轴号偏一位。精修需要中垂线投票(那条路实测到 16pt)。

**教训留档**:此前两次尝试都在数字里打转——先按「顶点到中心等距」逐 path 判,
结果全是**离中心远的短线段**(半径 2000pt 时 2% 容差就是 40pt,天然满足);
加角覆盖判据后又变成 0 个。真正看清结构靠的是**把图渲出来看**。
"""
from __future__ import annotations

import math

#: 判为「同一条径向轴线」的角度容差(度)。实测角间距 4.4°,
#: 容差必须远小于它,否则相邻轴线会被并成一条
ANGULAR_TOLERANCE_DEG = 1.0

#: 参与定向的最小半径(pt)。贴着圆心的圈角度极不稳定
MIN_RADIUS_PT = 50.0

#: 定心时判为「同角度」的容差(度)。比分组容差略松,
#: 因为搜索过程中圆心还没到位
CENTER_SEARCH_TOLERANCE_DEG = 1.5

#: 定心的粗搜格数与精修轮数
_COARSE_STEPS = 40
_REFINE_ROUNDS = 6

#: 判为放射轴网所需的最低**角间距规律度**。
#: 实测 A-01-03A(真放射)0.76,A-01-02A / A-01-04A(正交)0.47 / 0.44。
MIN_REGULARITY = 0.65

#: 判为放射布局所需的最低**角覆盖**(度)。
#: **这条是必需的**:同角度圈对的数量在「圆心趋于无穷远」处有退化极大值
#: ——圆心越远,所有圈的角度越挤在一起,配对数越多。实测搜索会跑到页外
#: (3501, -95) 去。角覆盖是干净的鉴别器:**直线上的点从任何位置看,
#: 角覆盖都不超过 180°**,而放射轴网接近 360°。
MIN_COVERAGE_DEG = 200.0


def _angle_of(circle: dict, center: tuple[float, float]) -> float:
    return math.degrees(math.atan2(circle["cy"] - center[1],
                                   circle["cx"] - center[0])) % 360.0


def _radius_of(circle: dict, center: tuple[float, float]) -> float:
    return math.dist((circle["cx"], circle["cy"]), center)


def _angular_delta(a: float, b: float) -> float:
    """两角之差(度),自动处理跨 0 的绕回。"""
    diff = abs(a - b) % 360.0
    return min(diff, 360.0 - diff)


def angular_pair_score(circles: list[dict], center: tuple[float, float],
                       tol: float = CENTER_SEARCH_TOLERANCE_DEG) -> float:
    """**成对的射线条数** —— 定心的判据。

    每条径向轴线上通常有 2 个轴号圈(内外各一);正确圆心下它们角度相同,
    于是形成大量「2 个成员的角度组」。

    **不能用「同角度圈对的总数」计分**:从偏离的位置看,圆环上的点会
    在远侧**挤到一起**,配对数反而更多——实测错误圆心得 185 分,
    真值只有 80 分。那个判据奖励的正是「挤在一起」,与要找的结构相反。
    按**组数**计分则不然:挤到一起会让组变少变大,分数下降。
    """
    usable = [c for c in circles if _radius_of(c, center) >= MIN_RADIUS_PT]
    if len(usable) < 2:
        return 0
    angles = sorted(_angle_of(c, center) for c in usable)
    if _angular_coverage(angles) < MIN_COVERAGE_DEG:
        return 0                            # 挤成一束 = 圆心跑远了,不是放射布局
    return _gap_regularity(angles)


#: 判为「规律」的角间距相对容差
GAP_TOLERANCE_RATIO = 0.25

#: 忽略过小的角间距(同一射线上的多个圈)
MIN_GAP_DEG = 0.3


def _gap_regularity(sorted_angles: list[float]) -> float:
    """角间距落在中位数 ±25% 内的比例 —— **放射轴网的真正特征**。

    实测(A-01-03A 真圆心 76%,两张正交图 47% / 44%):放射轴网按等角
    「依次注写」(§8.0.3 在极坐标下的形态),间距高度规律;正交轴网的圈
    从任何一点看,角间距都是杂乱的。

    **此前用「成对射线条数」是错的**——那假设每条射线有 2 个轴号圈,
    而实测 A-01-03A 每条射线只有 1 个(75 组里 72 组是单圈);
    正交图在任意圆心下反而凑出 23 个成对组,判据方向完全反了。
    """
    gaps = [sorted_angles[i + 1] - sorted_angles[i]
            for i in range(len(sorted_angles) - 1)]
    gaps = [g for g in gaps if g > MIN_GAP_DEG]
    if len(gaps) < 5:
        return 0.0
    median = sorted(gaps)[len(gaps) // 2]
    if median <= 0:
        return 0.0
    hit = sum(1 for g in gaps if abs(g - median) <= GAP_TOLERANCE_RATIO * median)
    return hit / len(gaps)


def _group_sizes(sorted_angles: list[float], tol: float) -> list[int]:
    """按角度容差分组后各组的成员数(跨 0 绕回一并处理)。"""
    if not sorted_angles:
        return []
    groups = [[sorted_angles[0]]]
    for angle in sorted_angles[1:]:
        if angle - groups[-1][-1] <= tol:
            groups[-1].append(angle)
        else:
            groups.append([angle])
    if len(groups) > 1 and _angular_delta(groups[0][0], groups[-1][-1]) <= tol:
        groups[0] = groups[-1] + groups[0]
        groups.pop()
    return [len(g) for g in groups]


def _angular_coverage(sorted_angles: list[float]) -> float:
    """角覆盖 = 360° 减去最大角间隙。直线布局无论从哪看都 ≤180°。"""
    if len(sorted_angles) < 2:
        return 0.0
    gaps = [sorted_angles[i + 1] - sorted_angles[i]
            for i in range(len(sorted_angles) - 1)]
    gaps.append(360.0 - sorted_angles[-1] + sorted_angles[0])
    return 360.0 - max(gaps)


def estimate_polar_center(circles: list[dict], *, page_w: float,
                          page_h: float) -> tuple[float, float] | None:
    """仅由轴号圈估计放射中心;不像放射布局则返回 None(不硬给一个)。

    粗搜整页网格 → 逐轮缩小范围精修。确定性搜索,同输入必给同输出。
    """
    if len(circles) < 6 or page_w <= 0 or page_h <= 0:
        return None

    best = None
    lo_x, hi_x, lo_y, hi_y = 0.0, page_w, 0.0, page_h
    for _round in range(_REFINE_ROUNDS):
        step_x = (hi_x - lo_x) / _COARSE_STEPS
        step_y = (hi_y - lo_y) / _COARSE_STEPS
        if step_x <= 0 or step_y <= 0:
            break
        for i in range(_COARSE_STEPS + 1):
            for j in range(_COARSE_STEPS + 1):
                candidate = (lo_x + i * step_x, lo_y + j * step_y)
                score = angular_pair_score(circles, candidate)
                if best is None or score > best[0]:
                    best = (score, candidate)
        cx, cy = best[1]
        lo_x, hi_x = cx - step_x, cx + step_x
        lo_y, hi_y = cy - step_y, cy + step_y

    if best is None:
        return None
    # 角间距不够规律 = 不是放射布局(正交轴网就会落在这里)
    if best[0] < MIN_REGULARITY:
        return None
    refined = refine_center_by_ray_lines(circles, best[1])
    center = refined or best[1]
    return (round(center[0], 2), round(center[1], 2))


def refine_center_by_ray_lines(circles: list[dict],
                               rough: tuple[float, float],
                               tol: float = CENTER_SEARCH_TOLERANCE_DEG,
                               ) -> tuple[float, float] | None:
    """按「同一射线上的两个圈定义一条过圆心的直线」做最小二乘精修。

    网格搜索有**平台效应**——邻近的一片圆心给出相同的组数,实测粗搜后仍偏
    79.6pt(半径 900pt 上就是 5°,超过 4.4° 的角间距)。而每条射线上的
    内外两个圈连成的直线**必过圆心**,一组这样的直线求最近点即可精确定心。

    解法:最小化点到各直线距离平方和,对 x 求导得 2×2 线性方程组
    `Σ(I - d dᵀ) x = Σ(I - d dᵀ) p`。直线不足 2 条或退化则返回 None。
    """
    groups = detect_polar_bands(circles, rough, tol)
    lines = []
    for group in groups:
        pts = [(c["cx"], c["cy"]) for c in group["circles"]]
        if len(pts) < 2:
            continue
        (x0, y0), (x1, y1) = pts[0], pts[-1]
        length = math.hypot(x1 - x0, y1 - y0)
        if length < 1e-6:
            continue
        lines.append(((x0, y0), ((x1 - x0) / length, (y1 - y0) / length)))
    if len(lines) < 2:
        return None

    a11 = a12 = a22 = b1 = b2 = 0.0
    for (px, py), (dx, dy) in lines:
        # I - d dᵀ 的三个独立元素
        m11, m12, m22 = 1 - dx * dx, -dx * dy, 1 - dy * dy
        a11 += m11; a12 += m12; a22 += m22
        b1 += m11 * px + m12 * py
        b2 += m12 * px + m22 * py
    det = a11 * a22 - a12 * a12
    if abs(det) < 1e-9:
        return None                          # 直线近乎平行,定不出交点
    return ((a22 * b1 - a12 * b2) / det, (a11 * b2 - a12 * b1) / det)


def detect_polar_bands(circles: list[dict], center: tuple[float, float] | None,
                       tol: float = ANGULAR_TOLERANCE_DEG) -> list[dict]:
    """轴号圈 → 按**角度**聚成的径向轴线组。

    同角度不同半径的圈归为一条轴线(§8.0.2 允许一条轴线注多个编号圈)。
    """
    if not circles or center is None:
        return []
    usable = [c for c in circles if _radius_of(c, center) >= MIN_RADIUS_PT]
    if not usable:
        return []

    ordered = sorted(usable, key=lambda c: _angle_of(c, center))
    groups: list[list[dict]] = []
    for circle in ordered:
        angle = _angle_of(circle, center)
        if groups and _angular_delta(
                angle, _angle_of(groups[-1][-1], center)) <= tol:
            groups[-1].append(circle)
        else:
            groups.append([circle])

    # 跨 0 绕回:首尾两组若相邻则合并
    if len(groups) > 1 and _angular_delta(
            _angle_of(groups[0][0], center),
            _angle_of(groups[-1][-1], center)) <= tol:
        groups[0] = groups[-1] + groups[0]
        groups.pop()

    out = []
    for group in groups:
        angles = [_angle_of(c, center) for c in group]
        radii = [_radius_of(c, center) for c in group]
        out.append({
            "angle_deg": round(_mean_angle(angles), 3),
            "radius_min": round(min(radii), 2),
            "radius_max": round(max(radii), 2),
            "circles": [dict(c) for c in group],
        })
    return sorted(out, key=lambda g: g["angle_deg"])


def _mean_angle(angles: list[float]) -> float:
    """角度均值(按单位向量平均,跨 0 也对)。"""
    x = sum(math.cos(math.radians(a)) for a in angles)
    y = sum(math.sin(math.radians(a)) for a in angles)
    return math.degrees(math.atan2(y, x)) % 360.0


def polar_axes(groups: list[dict],
               center: tuple[float, float]) -> list[dict]:
    """等角组 → 径向轴线。身份是**角度**,下游靠它 + 圆心还原直线。

    §8.0.3「依次注写」在放射轴网里就是**按角度依次**,所以按角度排序输出。
    """
    return [{
        "kind": "radial",
        "angle_deg": g["angle_deg"],
        "center": center,
        "radius_min": g["radius_min"],
        "radius_max": g["radius_max"],
        "circle_count": len(g["circles"]),
        "source": "polar_band",
    } for g in sorted(groups, key=lambda g: g["angle_deg"])]


# ── 由同心弧定心(**首选方法**)────────────────────────────────────

#: 参与定心的最小弧半径(pt)。小弧多是文字笔画与标注符号
ARC_MIN_RADIUS_PT = 200.0

#: 一条链至少要有多少段才拿去拟合弧
ARC_MIN_CHAIN_SEGMENTS = 6


def polar_center_from_arcs(segments: list[tuple]) -> dict | None:
    """由**同心弧**定心 —— 目前精度最高的方法(实测距真值 1.2pt)。

    对比各法在 A-01-03A 上的实测误差:

        同心弧拟合      1.2pt   ← 本方法
        渲图目测        6.8pt   (人工,不可自动化)
        中垂线投票     16.2pt
        角间距规律搜索  67.2pt
        三点外接圆心投票 373pt

    关键是两步都要做对:**跨 path 平滑追链**(否则弧被炸成碎段,逐 path 拟合
    只找得到 5 条)+ **拟合前抽稀**(否则半径 900pt 上相邻 2pt 段的 0.13° 转角
    被 0.1pt 坐标噪声淹没,一条弧也拟合不出)。

    不成同心族返回 None——不硬给一个圆心。
    """
    from core.model3d.arc_detector import (
        chain_points, concentric_center, fit_arc, trace_smooth_chains,
    )

    if not segments:
        return None
    arcs = []
    for chain in trace_smooth_chains(segments):
        if len(chain) < ARC_MIN_CHAIN_SEGMENTS:
            continue
        arc = fit_arc(chain_points(chain))
        if arc and arc["radius"] >= ARC_MIN_RADIUS_PT:
            arcs.append(arc)
    return concentric_center(arcs)
