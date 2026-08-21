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


def _median_spacing(positions: dict) -> float | None:
    """该图相邻轴线间距的中位数——**轴网的指纹**。"""
    values = sorted(float(v) for v in (positions or {}).values())
    gaps = [b - a for a, b in zip(values, values[1:]) if b - a > 1e-6]
    if not gaps:
        return None
    gaps.sort()
    return gaps[len(gaps) // 2]


def _pick_seed(remaining: dict) -> str:
    """选种子：**轴距最接近多数派的那张**。

    **不能按字母序或标签数**：实测三张图标签数相同时按字母序
    选中了轴距 30 米的错图，帧围绕错图形成、把两张正确的图判成离群。
    正确的图总是多数，多数派轴距是可测的事实；字母序不携带任何信息。
    """
    spacings = {d: _median_spacing(p) for d, p in remaining.items()}
    known = sorted(v for v in spacings.values() if v is not None)
    if not known:
        return min(remaining, key=lambda d: (-len(remaining[d]), d))
    typical = known[len(known) // 2]
    return min(remaining, key=lambda d: (
        abs((spacings[d] if spacings[d] is not None else typical * 10) - typical),
        -len(remaining[d]), d))


def _grow_joint(usable: dict) -> tuple[dict, dict]:
    """联合生长：一个池子，**两个方向同时校验**。

    **帧是二维的，不是两个独立的一维系统。** 实测按方向分别聚类时，
    x 与 y 各选各的种子、各自成团，再要求一张图两方向都符合才进帧——
    最大的分组（399 张）因此**一帧都建不出来**，
    连种子自己在 y 方向的残差都有 2.99 米（它符合 x 的那一团，
    却属于 y 的另一团）。
    """
    remaining = dict(usable)
    if not remaining:
        return {"x": {}, "y": {}}, {}
    seed = _pick_seed({d: o["x"] or o["y"] for d, o in remaining.items()})
    pool = {seed: remaining.pop(seed)}
    offsets = {seed: {"x": 0.0, "y": 0.0}}
    consensus = {d: solve_global_axes({k: v[d] for k, v in pool.items()})
                 for d in ("x", "y")}

    progressed = True
    while remaining and progressed:
        progressed = False
        for did in list(remaining):
            obs = remaining[did]
            offs, ok = {}, True
            for d in ("x", "y"):
                if not obs[d]:
                    offs[d] = 0.0
                    continue
                off = align_offset(obs[d], consensus[d])
                if off is None:          # 无共有轴号 —— 没有对照就不猜
                    ok = False
                    break
                shifted = {k: v + off for k, v in obs[d].items()}
                if alignment_residual(shifted, consensus[d]) > MAX_FRAME_RESIDUAL_M:
                    ok = False           # 一个方向不合就整张不收
                    break
                offs[d] = off
            if not ok:
                continue
            remaining.pop(did)
            pool[did] = {d: {k: v + offs[d] for k, v in obs[d].items()}
                         for d in ("x", "y")}
            offsets[did] = offs
            consensus = {d: solve_global_axes({k: v[d] for k, v in pool.items()})
                         for d in ("x", "y")}
            progressed = True
    return consensus, offsets


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
    seed = _pick_seed(remaining)
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
            shifted = {k: v + offset for k, v in remaining[did].items()}
            # **生长时就要校验，不能来者不拒**：只看「有没有共有轴号」
            # 会把轴距不同的图也并进来，污染共识后全军覆没
            # （实测混着两套轴网时返回 0 帧）。对齐后残差不达标的留给下一帧。
            if alignment_residual(shifted, consensus) > MAX_FRAME_RESIDUAL_M:
                continue
            remaining.pop(did)
            pool[did] = shifted
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

    consensus, offsets = _grow_joint(usable)
    consensus = _shift_to_origin(consensus)

    for did, obs in usable.items():
        off = offsets.get(did, {"x": 0.0, "y": 0.0})
        shifted = {d: {k: v + off.get(d, 0.0)
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
        # **总平移量 = 生长时的对齐平移 + 归零后的残余修正**。
        # 只存后者会丢掉大的那一半——实测轨道交通换到帧内后
        # 包络/核心比从 3.05 涨到 15.74，因为每张图只挪了一点点、
        # 仍散在各自的原位。契约是「原坐标 + offset = 帧内坐标」。
        frame.offsets[did] = {}
        for d in ("x", "y"):
            correction = align_offset(shifted[d], consensus[d])
            frame.offsets[did][d] = (
                off.get(d, 0.0) + correction if correction is not None else None)

    if frame.rejected and frame.members:
        # 剔除离群图后重算 —— 否则离群图仍留在共识里带偏结果
        kept = {k: usable[k] for k in frame.members}
        consensus, _ = _grow_joint(kept)
        consensus = _shift_to_origin(consensus)
    frame.axes = consensus
    frame.members.sort()
    return frame


def build_axis_frames(observations: dict | None) -> list[AxisFrame]:
    """多图轴号观测 → **若干**互相自洽的轴网帧。

    **为什么是复数**：一个分组（哪怕同层同单体）里可能本就有多套轴网。
    实测按楼层+单体分组后，残差 **P25 = 0.007 米（7 毫米）**而中位 2.8 米——
    四分之一的图对齐到毫米级，说明解算没问题；排除法否掉了图种、专业、
    重复轴号、比例、方向五个假设，剩下的解释就是**分组里混着互不相容的
    轴网**（分区工程一图三套，§8.33 已记）。

    强行合一的代价是多数图被判「残差过大」整批丢弃，
    而它们各自内部其实是自洽的。所以改为**聚类**：
    反复建帧，把进帧的取走，对剩下的再建，直到没有新帧能成。

    返回按成员数降序——主轴网排第一，下游默认取它。
    """
    remaining = dict(observations or {})
    frames: list[AxisFrame] = []
    while len(remaining) >= 1:
        frame = build_axis_frame(remaining)
        if not frame.members:
            break
        frames.append(frame)
        for did in frame.members:
            remaining.pop(did, None)
        # 只剩单向图之类无法定帧的，收工
        if not any(_usable(o) for o in remaining.values()):
            break
    frames.sort(key=lambda f: (-len(f.members), f.members[:1]))
    return frames


def register_frames(frames: list | None) -> list:
    """帧间配准：每个帧相对**锚帧**的整体平移量。

    **帧内部干净不等于帧之间对齐。** 每个帧以「本帧最小轴号 = 0」
    为原点，321 个帧就是 321 个互不相干的原点。实测把构件换到帧内后
    包络/核心比不降反升（大歌剧院 3.99→4.85、轨道交通 3.05→8.42）。

    帧之间同样靠**共有轴号**配准——把每个帧的轴网当作一次观测，
    用同一套共识算法再上一层。

    **成员最多的帧当锚**：它的证据最多，让它去迁就小帧没有道理。
    与任何已配准帧都没有共有轴号的返回 `None`——
    偏移 0 会被下游当成「已配准到原点」，而它其实是「没配准」。
    """
    items = list(frames or [])
    if not items:
        return []
    order = sorted(range(len(items)), key=lambda i: -len(items[i].members))
    anchor = order[0]
    result: list = [None] * len(items)
    result[anchor] = {"x": 0.0, "y": 0.0}
    pool = {d: dict(items[anchor].axes.get(d) or {}) for d in ("x", "y")}

    progressed = True
    pending = [i for i in order[1:]]
    while pending and progressed:
        progressed = False
        for index in list(pending):
            axes = items[index].axes or {}
            offs, ok = {}, False
            for d in ("x", "y"):
                own = axes.get(d) or {}
                off = align_offset(own, pool[d]) if own else None
                if off is None:
                    offs[d] = 0.0
                    continue
                shifted = {k: v + off for k, v in own.items()}
                # **配准也要校验残差**：只看「有没有共有轴号」就收，
                # 错配的帧会污染池子、后续帧跟着错
                # （实测大歌剧院包络 833→1079 米）。
                # 这个坑第三次出现了——前两次在 `_grow_consensus`
                # 与分方向聚类。
                if alignment_residual(shifted, pool[d]) > MAX_FRAME_RESIDUAL_M:
                    ok = False
                    break
                offs[d] = off
                ok = True           # 至少一个方向对上才算配准
            if not ok:
                continue
            pending.remove(index)
            result[index] = offs
            for d in ("x", "y"):
                for label, value in (axes.get(d) or {}).items():
                    pool[d].setdefault(label, value + offs[d])
            progressed = True
    return result


def register_frames_by_structure(keyed_frames: list | None) -> list:
    """按**结构关系**决定谁能与谁配准。

    `keyed_frames`: `[((story_key, unit, frame_index), AxisFrame), …]`

    两条规则来自建筑本身，不是调参：
    - **同一单体的不同楼层共用一套轴网**（建筑垂直对齐）→ 可配准
    - **同一楼层的不同分区是不同轴网**（GB/T 50001 §8.0.5 一图三套）
      → 不可配准，它们共用轴号名却不共用轴网
    - 跨单体不可配准（南区北区各有各的原点，配准会把两栋楼摞在一起）

    实测过强行让所有帧互相配准的代价：宽松则污染
    （大歌剧院包络 833→1079 米），严格则归零（落库摆放 1394→157）。
    所以**按结构关系先分好谁跟谁有资格比**，再谈残差。

    只有每层的**主帧**（frame_index=0，成员最多）参与跨层配准；
    同层的次帧是分区帧，各自独立、不给偏移。
    """
    items = list(keyed_frames or [])
    result: list = [None] * len(items)
    lanes: dict = {}
    for index, (key, frame) in enumerate(items):
        story, unit, frame_index = key
        if frame_index != 0:
            continue                    # 分区帧：不参与跨层配准
        lanes.setdefault(unit, []).append((index, frame))

    for _unit, members in lanes.items():
        offsets = register_frames([f for _i, f in members])
        for (index, _f), offset in zip(members, offsets):
            result[index] = offset
    return result
