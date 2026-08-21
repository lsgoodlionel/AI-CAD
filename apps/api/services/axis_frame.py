"""Phase K-1：工程自有坐标系（轴网帧）。

**为什么要它**（`docs/PHASE_K_BLUEPRINT.md`）：

| | 大歌剧院 | 轨道交通 |
|---|---|---|
| 有轴号且双向各 ≥2 条 | 2183 / 2309（**94.5%**） | 747 / 1707（**43.8%**） |
| 能定出坐标变换 | 727（31%） | 80（**4.5%**） |
| 有世界坐标 | 11（**0.5%**） | 0 |

**原料远比产出多。** 世界坐标近乎没有而轴号几乎每张都有——
不是数据质量问题，是施工图的固有属性：国标不要求每张图标测量坐标，
而定位轴线是每张平面图的必备要素（GB/T 50001 §8）。

轴网帧把「轴号 → 帧内米坐标」定下来，让每张图有共同参照物，
**全程不需要一个测量坐标**。

复用 `global_axis_consensus` 的中位共识与对齐残差——
那套机制此前只在楼层尺度用，这里抬到工程尺度并持久化。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from services.global_axis_consensus import (align_offset, alignment_residual,
                                            solve_global_axes)

#: 对齐残差超过它就不进帧（米）。**同名轴号对不上的图要剔除，
#: 不能平均进去**——平均会把错误摊到所有轴上，让整帧都偏一点，
#: 而那种偏差事后查不出来源。
MAX_FRAME_RESIDUAL_M = 0.5

#: 定帧所需的最少轴号数（每方向）。少于两条无法判断间距是否一致，
#: 只能对齐不能校验。
MIN_AXES_PER_DIRECTION = 2


@dataclass
class AxisFrame:
    """工程自有坐标系：轴号 → 帧内米坐标。"""

    axes: dict = field(default_factory=lambda: {"x": {}, "y": {}})
    #: 参与定帧的图纸（对齐残差达标）
    members: list = field(default_factory=list)
    #: 未参与的图纸 → 原因。**判不出就说判不出**，不给假坐标。
    rejected: dict = field(default_factory=dict)
    #: 每图对齐残差（米）——**「这张图能不能信」的唯一依据**，
    #: 下游摆放构件时按它决定信到什么程度。
    residuals: dict = field(default_factory=dict)
    #: 每图相对帧的平移量（加到图坐标上即对齐）
    offsets: dict = field(default_factory=dict)


def _label_order(label: str) -> tuple:
    """轴号排序键：数字轴按数值、字母轴按字母，混排时数字在前。

    **原点必须可复现**——取编号最小的轴（1 / A），
    而不是「第一张图的第一条轴」：后者随输入顺序变，
    同一个工程两次建帧会得到两套坐标。
    """
    text = str(label).strip()
    if text.isdigit():
        return (0, int(text), "")
    return (1, 0, text.upper())


def _shift_to_origin(axes: dict) -> dict:
    """把每个方向平移到「编号最小的轴 = 0」。"""
    out = {}
    for direction, labels in axes.items():
        if not labels:
            out[direction] = {}
            continue
        origin_label = min(labels, key=_label_order)
        origin = labels[origin_label]
        out[direction] = {k: round(v - origin, 4) for k, v in labels.items()}
    return out


def _usable(observation: dict) -> bool:
    """双向都有足够轴号才能定帧。

    只有单向轴号的图（剖面、立面）**不能定帧**——
    没有两个方向就没有平面定位。
    """
    return all(len((observation or {}).get(d) or {}) >= 1 for d in ("x", "y"))


def _grow_consensus(normalized: dict, direction: str) -> tuple[dict, dict]:
    """增量对齐：从轴号最多的图起，靠**共有轴号**把其余图并进来。

    **不能各自按自己最小的轴号归零**：d1 有轴号 1/2/3、d2 只有 2/3 时，
    两张图的零点不同，标签就不可比了——实测会把 `3` 解成 12 米
    （真值 16 米），因为 `2` 的观测里混了 0 和 8。

    对齐必须靠**两张图共有的轴号**：先算平移量把 d2 挪到 d1 的坐标系里，
    再合并。孤立（与已有共识无共有轴号）的图留到最后处理。
    """
    remaining = {did: dict(obs[direction]) for did, obs in normalized.items()
                 if obs.get(direction)}
    if not remaining:
        return {}, {}
    seed = min(remaining, key=lambda d: (-len(remaining[d]), d))
    # **保留全部已对齐的观测**再取中位：只用「当前共识 + 新图」两者
    # 取中位会丢掉累积的观测数，后进的图能把共识整体拉走
    # （实测三张图里那张轴距 30/60 的错图没被剔除，因为它把共识拉过去了）。
    pool: dict = {seed: dict(remaining.pop(seed))}
    aligned: dict = {seed: 0.0}
    consensus = solve_global_axes(pool)

    progressed = True
    while remaining and progressed:
        progressed = False
        for did in list(remaining):
            offset = align_offset(remaining[did], consensus)
            if offset is None:          # 无共有轴号 —— 没有对照就不猜
                continue
            pool[did] = {k: v + offset for k, v in remaining.pop(did).items()}
            aligned[did] = offset
            consensus = solve_global_axes(pool)
            progressed = True
    return consensus, aligned


def build_axis_frame(observations: dict | None) -> AxisFrame:
    """多图轴号观测 → 工程自有坐标系。

    `observations`: `{drawing_id: {"x": {轴号: 米}, "y": {轴号: 米}}}`，
    **同一单体/分区内**（分区工程一图三套轴网，混着建帧会让
    `1×A` 撞身份——§8.33 已踩过）。

    每张图各有自己的页面原点，所以位置不能直接比；
    比的是**轴号之间的相对距离**，由中位共识吸收个别图的误差。
    """
    frame = AxisFrame()
    usable: dict[str, dict] = {}
    for did, obs in (observations or {}).items():
        if _usable(obs):
            usable[str(did)] = obs
        else:
            frame.rejected[str(did)] = "single_direction"
    if not usable:
        return frame

    consensus, offsets = {}, {}
    for direction in ("x", "y"):
        consensus[direction], offsets[direction] = _grow_consensus(usable, direction)
    consensus = _shift_to_origin(consensus)

    for did, obs in usable.items():
        shifted = {d: {k: v + offsets[d].get(did, 0.0)
                       for k, v in (obs.get(d) or {}).items()}
                   for d in ("x", "y")}
        residual = max(
            (alignment_residual(shifted[d], consensus[d])
             for d in ("x", "y") if shifted[d]), default=float("inf"))
        frame.residuals[did] = (round(residual, 4)
                                if residual != float("inf") else None)
        if residual > MAX_FRAME_RESIDUAL_M:
            frame.rejected[did] = "residual_too_large"
            continue
        frame.members.append(did)
        frame.offsets[did] = {
            d: align_offset(shifted[d], consensus[d]) for d in ("x", "y")}

    if frame.rejected and frame.members:
        # 剔除离群图后重算 —— 否则离群图仍留在共识里带偏结果
        kept = {k: usable[k] for k in frame.members}
        consensus = {}
        for direction in ("x", "y"):
            consensus[direction], _ = _grow_consensus(kept, direction)
        consensus = _shift_to_origin(consensus)
    frame.axes = consensus
    frame.members.sort()
    return frame
