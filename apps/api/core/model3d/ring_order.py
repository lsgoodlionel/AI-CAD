"""多边形环序修复：把「点集顺序不是环序」的轮廓恢复成不自交的简单多边形。

**为什么需要**：`geometry_extractor._collect_pdf_drawings` 收 PDF 填充路径时
按**线段绘制顺序**累加两端点（`path_points.extend([a, b])`）。路径由若干
子段拼成、且子段不是首尾相接一路画下来时，这个顺序**不是多边形的环序**。
最坏情况一个矩形柱被画成「底边 + 顶边」两条线段，点序 `[A, B, D, C]`
构成 8 字形，鞋带面积**恰好为 0**。

**为什么要紧**：`services/model_qto.py:_column_quantity` 用鞋带面积 × 层高
算混凝土体积、用周长算模板面积；`_slab_quantity` 同理。面积为 0 ⇒ 该构件
混凝土量为 0、模板量失真，而算量经 `/model/quantities/to-proposal`
喂进创效提案，是要走三审的经济数字。

**坏环有两种形态**，判据各自精确、**都不含阈值**：
1. **穿越**（8 字形）—— 两条不相邻边在各自内部相交；
2. **回描**（`A→B→A→C`）—— 不穿越，但原路折回让鞋带正负相消得 0；
   判据是「面积为 0 却在 x、y 两向都有真实跨度」，因为简单多边形不可能这样。

**为什么不用「面积/包围盒是否够小」**：细长斜置的真实构件（斜撑、斜墙段）
面积本就远小于其轴对齐包围盒，比例阈值必然误伤。上面两条是坏环的精确
特征，无阈值、无误伤。凹多边形（L 形角柱）两条都不命中，因此原样保留 ——
换成凸包会把面积抬高（`dedupe.py` 实测外接矩形抬高约 27%）。
"""
from __future__ import annotations

import math

#: 叉积判正负的容差。图纸坐标以米计，1e-12 远小于任何真实构件尺度，
#: 只用来吸收浮点噪声，不构成几何判据。
_EPS = 1e-12

#: **穿越**检测是 O(n²)，超过这个点数就不查 —— 建模要跑几千张图，
#: 长环把整机拖垮的代价大于漏修的代价。柱/设备轮廓经 `_downsample_ring`
#: 后只有 8/12 个点，板轮廓实测也远低于此值，该上限基本不触发。
#: **回描**判据是 O(n) 的，不设上限，长环也照查。
MAX_CHECKED_POINTS = 256


def polygon_area(ring: list) -> float:
    """鞋带公式面积（取绝对值，与 `model_qto._polygon_area` 同口径）。"""
    if not ring or len(ring) < 3:
        return 0.0
    total = 0.0
    n = len(ring)
    for i in range(n):
        x0, y0 = float(ring[i][0]), float(ring[i][1])
        x1, y1 = float(ring[(i + 1) % n][0]), float(ring[(i + 1) % n][1])
        total += x0 * y1 - x1 * y0
    return abs(total) / 2.0


def _cross(ox: float, oy: float, ax: float, ay: float, bx: float, by: float) -> float:
    return (ax - ox) * (by - oy) - (ay - oy) * (bx - ox)


def _sign(value: float) -> int:
    if value > _EPS:
        return 1
    if value < -_EPS:
        return -1
    return 0


def _crosses(p0: tuple, p1: tuple, q0: tuple, q1: tuple) -> bool:
    """两线段是否**真正穿越**（各自内部相交）。

    端点相接、共线相叠一律不算 —— `path_points` 逐线段两端点会产生
    重复顶点（`[A, B, B, C, C, D, D, A]`），那是正常环的正常表达，
    把「相接」判成自交会让每一个正常轮廓都被重排。
    """
    d0 = _sign(_cross(p0[0], p0[1], p1[0], p1[1], q0[0], q0[1]))
    d1 = _sign(_cross(p0[0], p0[1], p1[0], p1[1], q1[0], q1[1]))
    d2 = _sign(_cross(q0[0], q0[1], q1[0], q1[1], p0[0], p0[1]))
    d3 = _sign(_cross(q0[0], q0[1], q1[0], q1[1], p1[0], p1[1]))
    return d0 * d1 < 0 and d2 * d3 < 0


def is_self_intersecting(ring: list) -> bool:
    """环是否自交（存在两条不相邻边真正穿越）。点数 < 4 或超限一律 False。"""
    if not ring or len(ring) < 4 or len(ring) > MAX_CHECKED_POINTS:
        return False
    n = len(ring)
    for i in range(n):
        p0, p1 = ring[i], ring[(i + 1) % n]
        # j 从 i+2 起跳过相邻边；末边(n-1)与首边(0)也相邻，故 i == 0 时止于 n-2
        stop = n - 1 if i == 0 else n
        for j in range(i + 2, stop):
            if _crosses(p0, p1, ring[j], ring[(j + 1) % n]):
                return True
    return False


def _has_zero_area_with_2d_extent(ring: list) -> bool:
    """面积为 0，却在 x、y 两个方向都有真实跨度。

    这是**坏环的第二种形态**：环没有穿越自己，而是原路折回（`A→B→A→C`
    这类回描），鞋带正负相消得 0。严格穿越判据碰不到它 —— 共线相叠被
    刻意排除掉了，否则 `path_points` 的重复顶点会让每个正常环都误判。

    判据同样**无阈值**：简单多边形只要在两个方向都有跨度，面积必然 > 0；
    面积为 0 就只能是环序坏了。所有点共线（跨度只在一个方向上，或沿对角
    线共线）时本条不成立也无妨 —— 那是真退化，重排也变不出面积。
    """
    if not ring or len(ring) < 3:
        return False
    xs = [float(p[0]) for p in ring]
    ys = [float(p[1]) for p in ring]
    if max(xs) - min(xs) <= 0.0 or max(ys) - min(ys) <= 0.0:
        return False
    return polygon_area(ring) == 0.0


def _angular_order(ring: list) -> list:
    """按绕形心的极角排序 —— 结果对该形心是星形多边形，因而不自交。

    **保留全部点**（不取凸包），所以轴对齐包围盒与点数都不变；
    这条是历史教训：轮廓重排/降点都不得缩小构件的真实范围。
    """
    cx = sum(float(p[0]) for p in ring) / len(ring)
    cy = sum(float(p[1]) for p in ring) / len(ring)

    def key(point) -> tuple[float, float]:
        dx, dy = float(point[0]) - cx, float(point[1]) - cy
        # 同极角时按半径排，避免近端点与远端点交错产生新的穿越
        return math.atan2(dy, dx), dx * dx + dy * dy

    return sorted(ring, key=key)


def repair_ring(ring: list) -> list:
    """环序坏了就修，没坏就**原样返回同一个列表内容**。

    契约：
    - 环序正常 → 原样返回，一个点不动；
    - 坏环（自交穿越，或面积为 0 却有二维跨度的回描环）→ 按极角重排，
      点数与轴对齐包围盒不变；
    - 所有点共线这类**真退化**图形修不出面积，也不假造 —— 那不是环序问题。

    **能力边界（不要当成"恢复原形"）**：极角序恢复的是**对形心的星形
    多边形**。源轮廓本就是凸的（矩形柱、圆柱八边形近似，占绝大多数）时
    这就是原形；源轮廓是凹的（L 形 / T 形角柱）时只能得到一个外扩的近似，
    面积偏大 —— 但仍**远好于原来的 0**，且包围盒不变。点序信息在
    `geometry_extractor` 那一步就已经丢了，这里无从还原真实的凹口。
    """
    if not ring:
        return ring
    if not is_self_intersecting(ring) and not _has_zero_area_with_2d_extent(ring):
        return ring
    return _angular_order(ring)
