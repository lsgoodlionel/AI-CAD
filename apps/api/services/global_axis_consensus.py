"""全局轴网共识：把「不一致就丢弃」换成「联合求解」。

**为什么要这条路**（本轮实测）：当前每张图独立算变换，互不一致时
就丢弃 —— B1 层「轴网聚合采纳 4/12 张」、51 条轴线里 **32 条同名冲突**。
丢掉的不是噪声，是**没被调和的观测**。

轴网是建筑的**刚性骨架**：同一栋楼所有平面图共享同一套轴网。
这给出天然的约束网络（实测：共享轴号 165 个、图对约束 117 万条、
涉及 697 张图）。同一轴号在多图上的位置**必须一致**，
不一致就说明某些图的变换错了。

**陷阱**：轴号 `1` 出现在 **520 张图**上，它们**不一定是同一根轴线** ——
不同单体/分区各有自己的 1 号轴。⇒ 求解**必须按单体/分区分组**，
本模块只负责组内共识，分组由调用方保证。

本模块是第一步（共识求解），**不是完整 Bundle Adjustment**：
只解平移，不解旋转与比例。取中位数而非最小二乘 ——
中位数对外点天然稳健（实测 19% 粗差下最小二乘残差无分界，
见 Phase I 的世界锚点求解）。
"""
from __future__ import annotations

from statistics import median


def _median_of(values: list[float]) -> float:
    return float(median(values))


def solve_global_axes(
    observations: dict[str, dict[str, float]] | None,
) -> dict[str, float]:
    """多图观测 → 全局轴网位置。

    `observations`: `{drawing_id: {轴号: 位置(米)}}`，**同一单体/分区内**。
    每个轴号取所有观测的**中位数** —— 一张图变换算错不会带偏全局。
    只被一张图见过的轴号照收：**孤证也是证据**，只是没有共识可校。
    """
    pooled: dict[str, list[float]] = {}
    for axes in (observations or {}).values():
        for label, position in (axes or {}).items():
            try:
                pooled.setdefault(str(label), []).append(float(position))
            except (TypeError, ValueError):
                continue
    return {label: _median_of(values) for label, values in pooled.items()}


def align_offset(drawing_axes: dict[str, float] | None,
                 global_axes: dict[str, float] | None) -> float | None:
    """该图相对全局轴网的**平移量**（加到图坐标上即对齐）。

    取各共有轴号残差的中位数。无共有轴号 → None（**没有对照就不猜**）。
    """
    residuals = [
        float(global_axes[label]) - float(position)
        for label, position in (drawing_axes or {}).items()
        if label in (global_axes or {})
    ]
    return _median_of(residuals) if residuals else None


def alignment_residual(drawing_axes: dict[str, float] | None,
                       global_axes: dict[str, float] | None) -> float:
    """对齐后仍存在的残差（米）—— **这张图与全局差多少**。

    它是判断该图变换是否可信的依据：残差大说明这张图的比例或旋转
    与全局不符，不是简单平移能解决的。无共有轴号返回 `inf`。
    """
    offset = align_offset(drawing_axes, global_axes)
    if offset is None:
        return float("inf")
    errors = [
        abs(float(position) + offset - float(global_axes[label]))
        for label, position in (drawing_axes or {}).items()
        if label in (global_axes or {})
    ]
    return _median_of(errors) if errors else float("inf")


# ── 场景级共识:两遍求解 + 残差门限 ──────────────────────────────

from dataclasses import dataclass, field

#: 平移对齐后仍允许的残差上限(米)。实测 B1 层:一致的图残差 0.00,
#: 平移解释不了的图(比例/旋转错)残差 2.12 —— 取 1.0,离两边都远。
#: 轴距典型 8 米,1 米已是明显错位。
MAX_CONSENSUS_RESIDUAL_M = 1.0


@dataclass(frozen=True)
class SceneConsensus:
    """一层楼的轴网共识结果。"""
    axes: dict                                  # {"x": [[label, pos]…], "y": […]}
    shifts: dict = field(default_factory=dict)  # did → (dx, dy)，加到该图坐标即对齐
    residuals: dict = field(default_factory=dict)
    outliers: list = field(default_factory=list)
    adopted: int = 0


def _obs_of(scene_axes: dict, direction: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for label, pos in (scene_axes or {}).get(direction) or ():
        text = str(label or "").strip()
        if text:
            try:
                out[text] = float(pos)
            except (TypeError, ValueError):
                continue
    return out


def solve_scene_consensus(
    candidates: list[tuple[str, dict]] | None,
    max_residual_m: float = MAX_CONSENSUS_RESIDUAL_M,
) -> SceneConsensus:
    """多图轴网观测 → 全局共识 + 每图对齐平移 + 外点名单。

    **推导**（空间智能方法论落到轴网的最简完整形态）：每图变换含
    平移误差 → 同一轴号的多图观测构成一维约束网络 → 这是**仅平移参数
    的一维 Bundle Adjustment**。取中位数即 L1 最小化（对外点稳健，
    实测 19% 粗差下最小二乘残差无分界）；两遍求解（共识→对齐→重解）
    是 IRLS 的一次迭代；残差门限是截断估计。

    与旧的「最大一致组」的本质区别：**整体平移的图被对齐后收回**，
    而不是丢弃 —— 丢掉的从来不是噪声，是没被调和的观测。
    平移解释不了的（比例/旋转错）才是外点，排除并**列名可查**。

    x/y 独立求解：一图的 x 向可信不代表 y 向可信。
    调用方必须保证候选**同属一个单体/分区** —— 轴号 `1` 在 520 张图上
    出现，不同单体各有自己的 1 号轴，跨单体求解会把不同楼强行对齐。
    """
    items = [(str(did), scene) for did, scene in (candidates or [])]
    if not items:
        return SceneConsensus(axes={"x": [], "y": []})

    obs = {
        direction: {did: _obs_of(scene, direction) for did, scene in items}
        for direction in ("x", "y")
    }

    # 第一遍:直接共识
    global_1 = {d: solve_global_axes(obs[d]) for d in ("x", "y")}

    # 每图对齐量与残差(两向取最坏 —— 有一向对不上就整图存疑)
    shifts: dict[str, tuple[float, float]] = {}
    residuals: dict[str, float] = {}
    outliers: list[str] = []
    for did, _scene in items:
        per_dir_shift: dict[str, float] = {}
        worst = None
        for d in ("x", "y"):
            offset = align_offset(obs[d][did], global_1[d])
            if offset is None:
                per_dir_shift[d] = 0.0        # 无共有轴号:互补不是矛盾
                continue
            per_dir_shift[d] = offset
            res = alignment_residual(obs[d][did], global_1[d])
            worst = res if worst is None else max(worst, res)
        if worst is not None and worst > max_residual_m:
            outliers.append(did)
            residuals[did] = worst
            continue
        shifts[did] = (per_dir_shift["x"], per_dir_shift["y"])
        residuals[did] = worst if worst is not None else 0.0

    # 第二遍:外点剔除、内点对齐后重解 —— 消掉第一遍里外点/偏移图
    # 对中位数的污染(两观测时中位数取均值,会落在两者中间)
    aligned = {
        d: {
            did: {label: pos + shifts[did][0 if d == "x" else 1]
                  for label, pos in obs[d][did].items()}
            for did, _ in items if did in shifts
        }
        for d in ("x", "y")
    }
    global_2 = {d: solve_global_axes(aligned[d]) for d in ("x", "y")}

    axes = {
        d: sorted(([label, round(pos, 3)] for label, pos in global_2[d].items()),
                  key=lambda e: e[1])
        for d in ("x", "y")
    }
    return SceneConsensus(axes=axes, shifts=shifts, residuals=residuals,
                          outliers=sorted(outliers), adopted=len(shifts))
