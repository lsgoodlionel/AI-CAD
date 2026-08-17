"""轴距序列匹配 —— 把局部图的轴网对到锚图上（Phase J 主线 J1）。

**目的**：全项目只有 11 张图（0.5%）有世界锚点。匹配成功可一次拿到
**轴号（含分区前缀）、分区归属、世界坐标** —— 正是 `docs/PHASE_J_BLUEPRINT.md`
§2.1 三条证伪路线各自卡住的那三样。

**与已证伪的路线①（精确指纹）的区别**：路线①要求序列完全相同，
而它的失败根因写得很清楚——「轴线检出有缺失，序列不会精确相同」。
本模块改为**带合并的子序列匹配**：局部图漏检一条轴线时，两段轴距合并成
相邻两个锚距之和，正好容忍那个失败根因。

**歧义一律判 None**。等距柱网（轴距全是 8.4 米）在任何起点都能匹配，
而规则柱网在工程上极其常见。猜一个世界坐标比没有更糟——
错的会带着满分置信度骗过所有下游（见 `drawing_transform` 的 1:335 万教训）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

#: 允许合并的最大连续锚距数（即局部图最多连续漏检 MAX_MERGE_SPAN-1 条轴线）。
#: 放大它能多匹配，但也会制造更多歧义路径 —— 3 是「漏一两条」的常见情形。
MAX_MERGE_SPAN = 3

#: 轴距比对的**相对**容差。误差主要来自比例尺，大轴距的绝对误差自然更大，
#: 用绝对容差会让大跨度轴网（30 米柱距）全部落空。
#: 2% 与 `drawing_transform` 判定「已是标准比例」的口径同源。
SCALE_TOLERANCE = 0.02

#: 参与匹配的最小轴距数。太短的序列碰巧对上的概率高，没有辨识度。
MIN_MATCH_GAPS = 5


@dataclass(frozen=True)
class GapMatch:
    """匹配结果。

    ``start_index``：局部序列的第一段对应锚序列的哪一段（0 基）。
    ``spans``：局部每一段吃掉了几个锚距（1 = 未合并，2 = 漏检一条轴线）。
    ``scale_ratio``：实测比例比，是独立于序列的一道合理性校验 ——
    明显偏离 1.0 说明两图的 `drawing_transform` 不一致，即便序列对上也该存疑。
    """

    start_index: int
    spans: list[int]
    scale_ratio: float

    @property
    def anchor_span(self) -> int:
        """本次匹配覆盖了锚序列的多少段。"""
        return sum(self.spans)


def _is_clean(gaps: Sequence[float] | None) -> bool:
    """序列可用于匹配吗。零或负轴距是数据错误（重复轴线 / 坐标乱序）。"""
    return bool(gaps) and all(float(g) > 0 for g in gaps)


def _close(measured: float, expected: float) -> bool:
    return abs(measured - expected) <= expected * SCALE_TOLERANCE


def count_matches(
    target: Sequence[float] | None, anchor: Sequence[float] | None,
    *, max_merge: int = MAX_MERGE_SPAN, min_gaps: int = MIN_MATCH_GAPS,
) -> tuple[int, GapMatch | None]:
    """``(路径条数, 代表解)``；路径数计到 2 即截断（判歧义只需知道「不止一条」）。

    独立于 :func:`match_gap_sequence` 暴露出来，是因为**歧义必须跨锚序列判定**：
    一个在自己组内歧义的序列，可能在别的组里唯一命中，从而被「抢走」
    （J1-A 留一法实测到这一例，分区归属会直接判错）。
    """
    return _search(target, anchor, max_merge=max_merge, min_gaps=min_gaps)


def match_against_anchors(
    target: Sequence[float] | None,
    anchors: dict[object, Sequence[float]] | None,
    *, max_merge: int = MAX_MERGE_SPAN, min_gaps: int = MIN_MATCH_GAPS,
) -> tuple[object, GapMatch] | None:
    """在**多个**锚序列中匹配；只有全体路径数恰为 1 才给结论。

    这是产线应当调用的入口。逐个调 :func:`match_gap_sequence` 是不安全的 ——
    那样只在单个锚序列内部判歧义，会漏掉「本组歧义、别组唯一」的误匹配。
    """
    if not _is_clean(target) or not anchors:
        return None
    total = 0
    best: tuple[object, GapMatch] | None = None
    for key, anchor in anchors.items():
        count, matched = count_matches(
            target, anchor, max_merge=max_merge, min_gaps=min_gaps)
        if not count:
            continue
        total += count
        if total > 1:
            return None                      # 全局歧义，不猜
        if matched is not None:
            best = (key, matched)
    return best if total == 1 else None


def match_gap_sequence(
    target: Sequence[float] | None, anchor: Sequence[float] | None,
    *, max_merge: int = MAX_MERGE_SPAN, min_gaps: int = MIN_MATCH_GAPS,
) -> GapMatch | None:
    """把 ``target`` 对到**单个** ``anchor`` 上；歧义或无解返回 None。

    多锚场景请用 :func:`match_against_anchors`。
    """
    count, matched = _search(target, anchor, max_merge=max_merge, min_gaps=min_gaps)
    return matched if count == 1 else None


def _search(
    target: Sequence[float] | None, anchor: Sequence[float] | None,
    *, max_merge: int, min_gaps: int,
) -> tuple[int, GapMatch | None]:
    """动态规划：``paths[i][j]`` = target 前 i 段停在 anchor 第 j 段之后的路径数。

    转移允许一段 target 吃掉 1..max_merge 个连续锚距（漏检合并）。
    起点不限（局部图可能画的是中间一段），故所有 j 都是合法起点。
    复杂度 ``O(k·n·max_merge)``，k、n 均为几十量级。
    """
    if not _is_clean(target) or not _is_clean(anchor):
        return 0, None
    tgt = [float(g) for g in target]        # type: ignore[union-attr]
    anc = [float(g) for g in anchor]        # type: ignore[union-attr]
    if len(tgt) < min_gaps or len(tgt) > len(anc):
        return 0, None

    # 前缀和让「连续 w 个锚距之和」变成 O(1)。
    prefix = [0.0]
    for gap in anc:
        prefix.append(prefix[-1] + gap)

    n = len(anc)
    # paths[i][j]:target 前 i 段停在 anchor 第 j 段之后的**路径条数**。
    # 计数而不是布尔 —— 歧义判定要的就是「不止一条」。计到 2 即可截断。
    paths = [[0] * (n + 1) for _ in range(len(tgt) + 1)]
    back: list[list[int]] = [[0] * (n + 1) for _ in range(len(tgt) + 1)]
    for j in range(n + 1):
        paths[0][j] = 1                     # 任意起点都合法

    for i in range(1, len(tgt) + 1):
        for j in range(1, n + 1):
            total = 0
            chosen = 0
            for w in range(1, min(max_merge, j) + 1):
                if paths[i - 1][j - w] and _close(tgt[i - 1], prefix[j] - prefix[j - w]):
                    total += paths[i - 1][j - w]
                    if not chosen:
                        chosen = w
                    if total > 1:
                        break
            paths[i][j] = min(total, 2)
            back[i][j] = chosen

    ends = [j for j in range(1, n + 1) if paths[len(tgt)][j]]
    if not ends:
        return 0, None
    # 路径总数 = 各终点路径数之和。多终点、或单终点多路径，都是歧义。
    total_paths = sum(paths[len(tgt)][j] for j in ends)
    if total_paths > 1:
        return min(total_paths, 2), None

    spans: list[int] = []
    j = ends[0]
    for i in range(len(tgt), 0, -1):
        w = back[i][j]
        if not w:                            # 理论不可达；防御性返回而非抛错
            return 0, None
        spans.append(w)
        j -= w
    spans.reverse()

    consumed = prefix[ends[0]] - prefix[j]
    return 1, GapMatch(start_index=j, spans=spans,
                       scale_ratio=(sum(tgt) / consumed) if consumed else 1.0)
