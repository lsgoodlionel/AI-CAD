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


#: 轴号里的分区前缀（§8.0.5「分区号-轴线号」）。
#: 不含 `/` —— 那是附加轴线的分数式（§8.0.6 `2-1/k`），整体是一个标签。
_ZONE_PREFIX_RE = __import__("re").compile(r"^(\d+)-(?![^/]*/)")


def zone_of_scene(scene_axes: dict | None) -> str | None:
    """从轴号标签取该图的**分区身份**（取多数）。

    **为什么用它**：共识此前要求「调用方保证同分区」，但没人保证。
    实测大歌剧院 822 张多分区图里 **616 张已人工确认分区号**，
    轴号带 `1-` 前缀 —— **前缀即分区身份**，是现成的硬先验。
    """
    counts: dict[str, int] = {}
    for direction in ("x", "y"):
        for label, _pos in (scene_axes or {}).get(direction) or ():
            matched = _ZONE_PREFIX_RE.match(str(label or "").strip())
            if matched:
                counts[matched.group(1)] = counts.get(matched.group(1), 0) + 1
    if not counts:
        return None
    return max(sorted(counts), key=lambda k: counts[k])


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


#: 修复量上限(米)。修复语义针对**变换误差**(实测 4.8 米量级);
#: 单体间距是几十米量级 —— 两个恰好平移等价的单体若不设上限会被强行
#: 合并成一套轴网。上限取 15:变换误差之上、单体间距之下。
MAX_REPAIR_SHIFT_M = 15.0


def _pair_relation(obs_a: dict, obs_b: dict,
                   max_residual_m: float) -> tuple[bool, bool]:
    """两图在一个方向上的关系 → (有共有轴号, 平移等价)。

    平移等价 = 对齐后残差 ≤ 门限 **且** 修复量 ≤ 上限。
    共有轴号不足 2 个时残差无意义,只报「有关系」不连边。
    """
    shared = [(obs_a[k], obs_b[k]) for k in obs_a.keys() & obs_b.keys()]
    if not shared:
        return False, False
    if len(shared) < 2:
        # 单共有轴号:证据不足以做平移修复(等价性不可证伪),
        # 但**同位置即等价**(旧合并语义)—— 链式覆盖靠它连通。
        return True, abs(shared[0][0] - shared[0][1]) <= max_residual_m
    diffs = [a - b for a, b in shared]
    offset = _median_of(diffs)
    if abs(offset) > MAX_REPAIR_SHIFT_M:
        return True, False
    residual = _median_of([abs(d - offset) for d in diffs])
    return True, residual <= max_residual_m


def _solve_direction(dids: list[str], obs_d: dict[str, dict[str, float]],
                     max_residual_m: float) -> tuple[dict, dict, set]:
    """单方向:聚类 → 最大簇 → 簇内两遍共识。

    返回 (全局位置, {did: 平移量}, 该方向的簇成员集合)。
    """
    with_labels = [d for d in dids if obs_d[d]]
    if not with_labels:
        return {}, {}, set()

    parent = {d: d for d in with_labels}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    related = {d: False for d in with_labels}
    for i, a in enumerate(with_labels):
        for b in with_labels[i + 1:]:
            shared, equivalent = _pair_relation(obs_d[a], obs_d[b],
                                                max_residual_m)
            if shared:
                related[a] = related[b] = True
                if equivalent:
                    parent[find(a)] = find(b)

    groups: dict[str, list[str]] = {}
    for d in with_labels:
        groups.setdefault(find(d), []).append(d)
    main = max(groups.values(),
               key=lambda ms: (len(ms), sum(len(obs_d[m]) for m in ms),
                               min(ms)))
    members = set(main) | {d for d in with_labels
                           if not related[d] and d not in main}

    # 簇内两遍共识
    global_1 = solve_global_axes({m: obs_d[m] for m in members})
    shifts: dict[str, float] = {}
    for d in members:
        offset = align_offset(obs_d[d], global_1)
        shifts[d] = offset if offset is not None else 0.0
    aligned = {d: {label: pos + shifts[d]
                   for label, pos in obs_d[d].items()} for d in members}
    return solve_global_axes(aligned), shifts, members


def solve_scene_consensus(
    candidates: list[tuple[str, dict]] | None,
    max_residual_m: float = MAX_CONSENSUS_RESIDUAL_M,
    group_of: dict[str, str] | None = None,
) -> SceneConsensus:
    """多图轴网观测 → **逐方向**聚类与簇内共识。

    **推导**(空间智能方法论落到轴网的完整形态):同一轴号的多图观测
    构成一维约束网络 → 仅平移参数的一维 Bundle Adjustment。
    中位数 = L1 最小化;两遍求解 = IRLS 一次迭代;
    聚类边 = 平移等价(残差 ≤ 门限 且 修复量 ≤ MAX_REPAIR_SHIFT_M)。

    **为什么必须先聚类**(B1 第一手重建反馈):楼层横跨多单体时,
    单一中位数框架被混合污染,连同单体的图也被挤成外点(实测 3/12)。

    **为什么逐方向**(B1 第二手调试反馈):分图间 x 向一致、
    y 向差 53~64 米且非常数(y 向变换真不一致);而我此前的验证把
    x/y 混进一个字典 —— x 向多数一致把 y 向不一致**在中位数里掩盖了**。
    整图门禁会把好的 x 向陪葬;逐方向采纳:收 x、弃 y。

    比例差(总图 vs 分图,位置差线性增长)平移救不了 —— 正确落为外点,
    那要靠上游变换修复(主线 J1),本模块不越权硬修。
    """
    items = [(str(did), scene) for did, scene in (candidates or [])]
    if not items:
        return SceneConsensus(axes={"x": [], "y": []})

    # **分区硬分组优先于几何自聚类**：同分区才求共识 —— 不同分区各有
    # 自己的 1 号轴，混算会互相污染（B1 层实测采纳 3/12 就是这么来的）。
    # 未提供分组时按轴号前缀自取；都没有则退回几何自聚类（旧能力不丢）。
    resolved_groups = dict(group_of or {})
    if not resolved_groups:
        for did, scene in items:
            zone = zone_of_scene(scene)
            if zone:
                resolved_groups[did] = zone
    if resolved_groups and len(set(resolved_groups.values())) > 1:
        merged_axes: dict[str, list] = {"x": [], "y": []}
        merged_shifts: dict = {}
        merged_res: dict = {}
        merged_outliers: list[str] = []
        by_group: dict[str, list] = {}
        for did, scene in items:
            by_group.setdefault(resolved_groups.get(did, ""), []).append(
                (did, scene))
        for key in sorted(by_group):
            part = solve_scene_consensus(by_group[key], max_residual_m)
            for d in ("x", "y"):
                merged_axes[d].extend(part.axes.get(d) or [])
            merged_shifts.update(part.shifts)
            merged_res.update(part.residuals)
            merged_outliers.extend(part.outliers)
        for d in ("x", "y"):
            merged_axes[d].sort(key=lambda e: e[1])
        return SceneConsensus(axes=merged_axes, shifts=merged_shifts,
                              residuals=merged_res,
                              outliers=sorted(merged_outliers),
                              adopted=len(merged_shifts))

    dids = [did for did, _ in items]
    obs = {
        direction: {did: _obs_of(scene, direction) for did, scene in items}
        for direction in ("x", "y")
    }

    per_dir = {d: _solve_direction(dids, obs[d], max_residual_m)
               for d in ("x", "y")}

    axes = {
        d: sorted(([label, round(pos, 3)]
                   for label, pos in per_dir[d][0].items()),
                  key=lambda e: e[1])
        for d in ("x", "y")
    }
    shifts: dict[str, tuple[float, float]] = {}
    residuals: dict[str, float] = {}
    outliers: list[str] = []
    for did in dids:
        member_x = did in per_dir["x"][2]
        member_y = did in per_dir["y"][2]
        has_x = bool(obs["x"][did])
        has_y = bool(obs["y"][did])
        if not member_x and not member_y and (has_x or has_y):
            outliers.append(did)          # 每个有标签的方向都进不了簇
            continue
        shifts[did] = (per_dir["x"][1].get(did, 0.0),
                       per_dir["y"][1].get(did, 0.0))
        residuals[did] = 0.0
    return SceneConsensus(axes=axes, shifts=shifts, residuals=residuals,
                          outliers=sorted(outliers), adopted=len(shifts))
