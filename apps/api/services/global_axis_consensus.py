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
