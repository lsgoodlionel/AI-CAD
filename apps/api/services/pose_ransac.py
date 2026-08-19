"""RANSAC 鲁棒位姿求解：**标签给候选，几何来裁决**。

**为什么必须做**（实测）：标签归一化让分区体系（`1-1`）与裸体系（`1`）
能配对，交点从 0 → 135；但**跨分区错配**混在其中 —— 分图（四）的
`1-1…1-15` 归一成 `1…15`，全局的 `1…36` 可能来自其他分区，
于是解出 scale=0.77（两图实际都是 1:150，本该 1.0）、rmse 8.58 米。

**空间智能的做法**：不信标签，信几何。同一结构的对应点必然服从同一个
相似变换，跨分区的伪对应服从不了。⇒ 枚举最小样本（2 对点）拟合，
统计内点，取内点最多的模型，再用全部内点重解。

**确定性**：用**全枚举**而非随机采样。本轮反复吃过「结果依赖输入顺序、
不可复现」的亏（轴网聚合曾因 stable sort 同档保序而随调用方变化）。
候选点先按键排序，枚举顺序固定，同样的数据必得同样的解。
"""
from __future__ import annotations

from itertools import combinations

from services.drawing_anchor import similarity_from_pairs

#: 内点判定阈值（米）。轴距典型 8 米，0.5 米已是明显错位；
#: 而正确对应在实测里残差是 0.00 量级。
DEFAULT_INLIER_TOLERANCE_M = 0.5

#: 成模最少内点数。2 对点必然完美拟合（自由度相等），
#: 所以至少要 3 对才谈得上「一致」。
DEFAULT_MIN_INLIERS = 3

#: 枚举上限。C(n,2) 在 n=200 时是 19900 次拟合，尚可；
#: 再多就先按键截断 —— 轴网交点本就是笛卡尔积，冗余度高。
MAX_ENUMERATION_POINTS = 200

#: 可信内点率下限。实测外点图得 rmse 0.297m（比标签配对的 8.58m 好 29 倍）
#: 却只有 **14/135 = 10%** 内点，且 scale=0.824 而两图比例尺都记为 1:150 ——
#: 低内点率下伪解风险高，而**无法从数据区分**「比例尺记录错」与「对应仍错」。
#: ⇒ 低于此值只报不采，让人判断（降级必须可见）。
CONFIDENT_INLIER_RATIO = 0.5

#: **scale 是比内点率更强的信号**。图纸等比绘制，同一工程的图之间
#: scale 应接近 1，1~2% 是测量噪声级别；而伪解的指纹是明显偏离
#: （实测跨分区伪解 **0.824**，真匹配 **0.988~1.000**）。
#: 内点率低往往只说明**覆盖不全**（一张图只覆盖分区的一部分），不等于错配。
NEAR_UNIT_SCALE_TOLERANCE = 0.05

#: 靠 scale 判可信时，内点的**绝对数**下限 —— 3 个点凑出的 scale≈1 不足为凭。
MIN_INLIERS_FOR_SCALE_TRUST = 8


def _residuals(keys, local, glob, pose) -> list[float]:
    scale = pose["scale"]
    import math

    theta = math.radians(pose["rotation_deg"])
    cos_t, sin_t = math.cos(theta), math.sin(theta)
    reflect = pose.get("reflect", False)
    out = []
    for key in keys:
        x, y = local[key]
        if reflect:
            y = -y
        px = scale * (x * cos_t - y * sin_t) + pose["tx"]
        py = scale * (x * sin_t + y * cos_t) + pose["ty"]
        gx, gy = glob[key]
        out.append(((px - gx) ** 2 + (py - gy) ** 2) ** 0.5)
    return out


def solve_pose_ransac(
    local_points: dict | None,
    global_points: dict | None,
    tolerance_m: float = DEFAULT_INLIER_TOLERANCE_M,
    min_inliers: int = DEFAULT_MIN_INLIERS,
) -> dict | None:
    """从含错配的对应中解出**几何一致子集**上的相似变换。

    返回 `{scale, rotation_deg, tx, ty, rmse, inliers, total}`；
    找不到足够大的一致子集时返回 None（**不硬凑解**）。
    """
    local_points = local_points or {}
    global_points = global_points or {}
    keys = sorted(set(local_points) & set(global_points))
    if len(keys) < min_inliers:
        return None
    if len(keys) > MAX_ENUMERATION_POINTS:
        keys = keys[:MAX_ENUMERATION_POINTS]

    best_inliers: list = []
    best_pose = None
    for pair in combinations(keys, 2):
        pose = similarity_from_pairs(
            [local_points[k] for k in pair], [global_points[k] for k in pair])
        if pose is None:
            continue
        residuals = _residuals(keys, local_points, global_points, pose)
        inliers = [k for k, r in zip(keys, residuals) if r <= tolerance_m]
        # 平局时取残差和更小的 —— 保证确定性
        if len(inliers) > len(best_inliers) or (
                len(inliers) == len(best_inliers) and best_pose is not None
                and sum(residuals) < best_pose.get("_sum", float("inf"))):
            best_inliers = inliers
            best_pose = {**pose, "_sum": sum(residuals)}

    if best_pose is None or len(best_inliers) < min_inliers:
        return None

    # 用全部内点重解（最小样本拟合只是假设，内点集才是证据）
    refined = similarity_from_pairs(
        [local_points[k] for k in best_inliers],
        [global_points[k] for k in best_inliers])
    if refined is None:
        return None
    ratio = len(best_inliers) / len(keys) if keys else 0.0
    return {
        "scale": refined["scale"],
        "rotation_deg": refined["rotation_deg"],
        "tx": refined["tx"],
        "ty": refined["ty"],
        "rmse": refined["rmse"],
        "inliers": len(best_inliers),
        "total": len(keys),
        "inlier_ratio": round(ratio, 4),
        # **组合判据**：内点率够 **或**（scale 接近 1 且内点数够）。
        # 单看内点率会把「覆盖不全的真匹配」误杀 —— 实测裸标签图
        # 归入分区时内点仅 33% 而 scale=0.988，那是真匹配。
        "confident": bool(
            ratio >= CONFIDENT_INLIER_RATIO
            or (abs(refined["scale"] - 1.0) <= NEAR_UNIT_SCALE_TOLERANCE
                and len(best_inliers) >= MIN_INLIERS_FOR_SCALE_TRUST)),
    }
