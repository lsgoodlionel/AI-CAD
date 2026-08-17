"""交叉点定位:用轴线交叉点把整张图纸摆到工程坐标系里。

**要解决的问题**:多张图纸出现相同轴号时,如何把它们对齐、并把整套图放进模型的
真实坐标系。既有 `drawing_transform` 只有比例 + 平移,拿不到旋转,也没有世界原点。

**两条独立的定位路径**(都只需图上 ≥2 个交叉点):

1. **图↔图对齐**(需求 4):两张图有 ≥2 个同名交叉点(如 `1-A`、`5-C`),
   由这两对点解出**相似变换**(缩放 + 旋转 + 平移),即可把 B 图整体摆到 A 图的坐标系。
2. **图↔世界**(需求 5):某张图上给出交叉点的**实际工程坐标 XYZ**,
   同样 ≥2 点即可解出该图到工程坐标系的变换。一套图里先在某张图定义
   `(0,0,0)` 原点,**每个专业各自定义一次**——各专业的图往往用不同的局部原点。

**为什么是相似变换而不是仿射**:图纸是等比绘制的,允许缩放+旋转+平移,
但**不允许拉伸/错切**——放开会把识别误差吸收成假的形变,反而错得更离谱。
故用 Umeyama 闭式解(带尺度的 Kabsch),对 2 点即可精确求解,>2 点为最小二乘。
"""
from __future__ import annotations

import math

#: 解算所需的最少交叉点对数
MIN_PAIRS = 2

#: 残差告警阈值(米):超过说明点配错了或轴号重名
RESIDUAL_WARN_M = 0.5


def similarity_from_pairs(
    src: list[tuple[float, float]], dst: list[tuple[float, float]],
) -> dict | None:
    """由 ≥2 对点解相似变换 src → dst(缩放 s、旋转 θ、平移 tx/ty)。

    返回 {scale, rotation_deg, tx, ty, rmse, pairs};点数不足/退化 → None。
    """
    n = min(len(src), len(dst))
    if n < MIN_PAIRS:
        return None

    sx = sum(p[0] for p in src[:n]) / n
    sy = sum(p[1] for p in src[:n]) / n
    dx = sum(p[0] for p in dst[:n]) / n
    dy = sum(p[1] for p in dst[:n]) / n

    # 旋转+缩放拟合不出**反射**,而工程坐标常常需要它:
    # 中国测量坐标惯例 X=北 / Y=东,相对数学系 (东,北) 是**左手系**,
    # 图纸米坐标(y 向上)→ 工程坐标因此要先镜像一次。
    # 实测不支持反射时残差 105m,图被判 suspect 直接跳过,永远摆不上。
    # 做法:两种朝向各拟合一次,取残差小的那个。
    best = None
    for reflect in (False, True):
        candidate = _fit_one(src[:n], dst[:n], (sx, sy), (dx, dy), n, reflect)
        if candidate is None:
            continue
        if best is None or candidate["rmse"] < best["rmse"]:
            best = candidate
    return best


def _fit_one(src, dst, src_center, dst_center, n: int,
             reflect: bool) -> dict | None:
    """给定朝向下的最小二乘相似变换。reflect 时先把源点的 y 取反。"""
    sx, sy = src_center
    dx, dy = dst_center
    sign = -1.0 if reflect else 1.0

    num_cos = num_sin = den = 0.0
    for (ax, ay), (bx, by) in zip(src, dst):
        ux, uy = ax - sx, sign * (ay - sy)
        vx, vy = bx - dx, by - dy
        num_cos += ux * vx + uy * vy
        num_sin += ux * vy - uy * vx
        den += ux * ux + uy * uy
    if den < 1e-15:
        return None                     # 源点重合,定不出方向

    scale = math.hypot(num_cos, num_sin) / den
    if scale <= 0:
        return None
    theta = math.atan2(num_sin, num_cos)
    cos_t, sin_t = math.cos(theta), math.sin(theta)
    # 反射时源点先绕自身中心镜像,故平移量按镜像后的中心算
    mx, my = sx, sign * sy
    tx = dx - scale * (cos_t * mx - sin_t * my)
    ty = dy - scale * (sin_t * mx + cos_t * my)

    transform = {
        "scale": scale, "rotation_deg": math.degrees(theta) % 360.0,
        "tx": tx, "ty": ty, "pairs": n, "reflect": reflect,
    }
    residuals = [math.dist(apply_similarity(p, transform), q)
                 for p, q in zip(src, dst)]
    transform["rmse"] = math.sqrt(sum(r * r for r in residuals) / n)
    return transform


def apply_similarity(
    point: tuple[float, float], transform: dict,
) -> tuple[float, float]:
    """把点按相似变换映射过去。"""
    s = float(transform["scale"])
    theta = math.radians(float(transform["rotation_deg"]))
    cos_t, sin_t = math.cos(theta), math.sin(theta)
    x, y = float(point[0]), float(point[1])
    # 缺 reflect 字段的老变换按不反射处理(向后兼容)
    if transform.get("reflect"):
        y = -y
    return (s * (cos_t * x - sin_t * y) + float(transform["tx"]),
            s * (sin_t * x + cos_t * y) + float(transform["ty"]))


def match_intersections(
    a: list[dict], b: list[dict],
) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    """两张图的交叉点按**轴号对**配对,返回可解算的点序列。

    交叉点的身份是 `(label_x, label_y)`(如 `1`×`A`),这正是「多张图纸出现相同
    轴线名称」时的天然锚点。
    """
    index = {(str(p["label_x"]), str(p["label_y"])): p for p in b}
    src: list[tuple[float, float]] = []
    dst: list[tuple[float, float]] = []
    for p in a:
        key = (str(p["label_x"]), str(p["label_y"]))
        other = index.get(key)
        if other is None:
            continue
        src.append((float(p["x_norm"]), float(p["y_norm"])))
        dst.append((float(other["x_norm"]), float(other["y_norm"])))
    return src, dst


def align_drawings(a: list[dict], b: list[dict]) -> dict | None:
    """按同名交叉点把 a 图对齐到 b 图的坐标系(需求 4)。"""
    src, dst = match_intersections(a, b)
    return similarity_from_pairs(src, dst)


def solve_world_transform(points: list[dict]) -> dict | None:
    """由带世界坐标的交叉点解「图纸 → 工程坐标系」的变换(需求 5)。

    points 每项需含 `x_norm/y_norm`(图上归一化)与 `world_x/world_y`(米)。
    只取填了世界坐标的点;不足 2 个 → None。
    返回的变换额外带 `z`(取各点 world_z 均值)与 `rmse_m`(米残差)。
    """
    usable = [
        p for p in points
        if p.get("world_x") is not None and p.get("world_y") is not None
    ]
    if len(usable) < MIN_PAIRS:
        return None
    src = [(float(p["x_norm"]), float(p["y_norm"])) for p in usable]
    dst = [(float(p["world_x"]), float(p["world_y"])) for p in usable]
    transform = similarity_from_pairs(src, dst)
    if transform is None:
        return None

    zs = [float(p["world_z"]) for p in usable if p.get("world_z") is not None]
    transform["z"] = sum(zs) / len(zs) if zs else None
    transform["rmse_m"] = transform["rmse"]
    # 残差大说明点配错了或轴号重名,如实标出而不是当成好结果用
    transform["suspect"] = transform["rmse_m"] > RESIDUAL_WARN_M
    return transform
