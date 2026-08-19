"""XY-cut 块级版面分析（文档版面分析的经典算法）。

**为什么需要它**（上一轮实测）：单纯按 x 聚类分栏，对结构统一说明
只检出 3 栏，条文仍是拼接。根因是工程图的说明**不是规则多栏**，
而是若干说明块散布在图幅各处，每块内部才是多栏或单栏。

**XY-cut**：在投影直方图里找**空白带**，在最宽处切分，递归下去。
它的好处正是本轮反复需要的：**确定性、无需调参、可解释** ——
切在哪、为什么切，都能说清（本轮吃够了「结果不可复现」的亏）。

递归顺序 y→x→y…：先横切成带，再纵切成块，块内再横切成行组。
"""
from __future__ import annotations

#: 递归深度上限。图纸版面再复杂也到不了这个深度；
#: 设上限是防止病态数据把栈打穿。
MAX_DEPTH = 12

#: 一块里少于这么多 token 就不再切。
#: **门槛必须低**：token 在本系统里是**行**（OCR 输出行级，平均 14.6 字），
#: 不是字 —— 两行完全可能分属左右两栏。设成 4 会让「上下两块、
#: 每块左右两栏」只切出 2 块（实测），因为切完 y 后每块只剩 2 行就停手了。
MIN_TOKENS_TO_SPLIT = 2


def _span_of(token, axis: int) -> tuple[float, float] | None:
    """token 在某轴上的跨度（0=x, 1=y）。"""
    if not isinstance(token, dict):
        return None
    bbox = token.get("bbox")
    if isinstance(bbox, (list, tuple)) and len(bbox) >= 4:
        try:
            lo, hi = float(bbox[axis]), float(bbox[axis + 2])
            return (lo, hi) if hi >= lo else (hi, lo)
        except (TypeError, ValueError):
            return None
    key = "x" if axis == 0 else "y"
    try:
        value = float(token[key])
    except (KeyError, TypeError, ValueError):
        return None
    return (value, value)


def find_gaps(spans: list[tuple[float, float]] | None,
              min_gap: float) -> list[tuple[float, float]]:
    """跨度列表 → 其间的**空白带**（宽度 ≥ `min_gap`）。"""
    ordered = sorted(spans or [])
    if len(ordered) < 2:
        return []
    gaps: list[tuple[float, float]] = []
    reach = ordered[0][1]
    for lo, hi in ordered[1:]:
        if lo - reach >= min_gap:
            gaps.append((reach, lo))
        reach = max(reach, hi)
    return gaps


def _split(tokens: list, axis: int, min_gap: float) -> list[list] | None:
    """沿某轴按最宽空白切分；切不动返回 None。"""
    spans = [(t, _span_of(t, axis)) for t in tokens]
    if any(span is None for _t, span in spans):
        return None
    gaps = find_gaps([span for _t, span in spans], min_gap)
    if not gaps:
        return None
    # 在**最宽**的空白处切 —— 那是版面里最强的分隔信号
    cut_lo, cut_hi = max(gaps, key=lambda g: g[1] - g[0])
    boundary = (cut_lo + cut_hi) / 2.0
    first = [t for t, span in spans if span[0] < boundary]
    second = [t for t, span in spans if span[0] >= boundary]
    if not first or not second:
        return None
    return [first, second]


def xy_cut(tokens: list | None, min_gap: float,
           depth: int = 0, axis: int = 1) -> list[list]:
    """递归切分 → **按阅读顺序排列的块**（先上后下、先左后右）。

    `axis` 从 1（y）起：先横切成带，再纵切成块。
    无位置的 token 不丢，单独成块附在末尾。
    """
    items = list(tokens or [])
    if not items:
        return []
    positioned = [t for t in items if _span_of(t, 0) and _span_of(t, 1)]
    orphans = [t for t in items if t not in positioned]

    def _finish(blocks: list[list]) -> list[list]:
        return blocks + ([orphans] if orphans else [])

    if len(positioned) < MIN_TOKENS_TO_SPLIT or depth >= MAX_DEPTH:
        return _finish([positioned] if positioned else [])

    for try_axis in (axis, 1 - axis):
        parts = _split(positioned, try_axis, min_gap)
        if parts is None:
            continue
        # 切完后各部分按该轴起点排序 —— y 轴即先上后下、x 轴即先左后右
        parts.sort(key=lambda part: min(_span_of(t, try_axis)[0] for t in part))
        out: list[list] = []
        for part in parts:
            out.extend(xy_cut(part, min_gap, depth + 1, 1 - try_axis))
        return _finish(out)

    return _finish([positioned])
