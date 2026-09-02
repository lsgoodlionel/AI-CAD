"""密排阵列识别：把座椅/吸声板/铺装单元这类「小方块阵列」从柱候选里摘出来。

## 为什么需要这道判据

柱的尺寸窗口是 0.2~1.5m（`element_recognizer._COLUMN_SIZE`），而剧院座椅
约 0.36~0.47m —— **落在窗口正中间**。任何纯尺寸判据都不可能把两者分开。
实测（`data/model3d/gold/rule_vs_model_v1.json`，60 格独立判读）：60 个柱
候选里 28 格判为座椅，其中规则引擎侧 17 格。

## 判据：等间距成排 **且** 间距≈自身尺寸

柱网也是等间距成排，所以「成排」单独不成立判据。分开两者的是间距与
构件自身尺寸的比值 —— 这个量纲无关，不随图纸比例或构件绝对尺寸漂移：

===================================  ========  ==========
实测对象                              比值中位   比值<1.5
===================================  ========  ==========
建筑-竣工图--三层平面图(三)（座椅）          0.94       87%
建筑-竣工图--二层平面图(五)（座椅）          1.00       80%
结构--一层结构平面图（四）（真柱网）           6.39        1%
结构--地下一层结构平面图（四）（真柱网）         6.78        0%
===================================  ========  ==========

座椅是紧挨着排的（中心距 = 自身宽），柱之间隔着一个跨度。两条判据同时
成立才删；只用「密集」会误杀结构图上的构件，只用「成排」会误杀柱网。

## 阈值的实测来源与已知敏感点

`DEFAULT_RUN_MIN = 5` 来自实测排布：二层平面图(五) 的座椅是
`0.36×5 → 1.15(走道) → 0.36×5`，**5 个一组**被走道打断。放到 6 时该图
的删除率从 80% 掉到 23% —— 阈值卡在这张图的形态边界上，换一个座椅
布局（每组 4 座）就会漏。这是本判据已知的脆弱面，不是安全余量。

判据只**标记**、由调用方决定删不删；不在这里静默丢弃
（`MODELING_PIPELINE_BLUEPRINT.md` §7：降级必须可见）。

复杂度 O(n²)：每个候选只被并进一条链（`taken`），但每步要扫一遍找最近邻。
实测单图候选上限 `_CAPS["columns"]=2000` 时 **583ms**，占单图识别超时
（`_RECOGNIZE_TIMEOUT_SEC`=20s）的 3%。
"""
from __future__ import annotations

#: 间距上限，以构件自身较大边长为单位。实测阳性 0.94~1.00、真柱网 10 分位 3.83。
DEFAULT_GAP_RATIO_MAX = 1.5
#: 构成「一排」的最少个数。见模块文档：来自实测的 5 座一组形态。
DEFAULT_RUN_MIN = 5
#: 同一排内间距的最大不齐程度（max/min）。超过则不是规则阵列。
DEFAULT_EVEN_TOL = 1.3
#: 归为同一排的横向偏移上限，以自身边长为单位。
_ROW_OFFSET_RATIO = 0.5
#: 小于此边长（米）的元素不参与阵列判定 —— 尺寸不可信，比值会失真。
_MIN_SIDE_M = 1e-6


def _center_size(element: dict) -> tuple[float, float, float] | None:
    """元素 → (cx, cy, 较大边长)；轮廓退化时返回 None。"""
    outline = element.get("outline") or []
    if len(outline) < 3:
        return None
    try:
        xs = [float(p[0]) for p in outline]
        ys = [float(p[1]) for p in outline]
    except (TypeError, ValueError, IndexError):
        return None
    side = max(max(xs) - min(xs), max(ys) - min(ys))
    if side <= _MIN_SIDE_M:
        return None
    return (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, side


def _runs_along(
    nodes: list[tuple[int, float, float, float]], gap_ratio_max: float,
) -> list[list[tuple[int, float, float, float]]]:
    """沿主轴把节点串成链。nodes 元素为 (index, 主轴坐标, 副轴坐标, 边长)。

    每个节点只向右接**最近**的一个同排邻居，因此一条链就是一排。
    """
    ordered = sorted(nodes, key=lambda n: (n[1], n[2]))
    taken: set[int] = set()
    runs: list[list[int]] = []
    for start in ordered:
        if start[0] in taken:
            continue
        chain = [start]
        taken.add(start[0])
        cur = start
        while True:
            _, cx, cy, side = cur
            limit = gap_ratio_max * side
            best = None
            for cand in ordered:
                if cand[0] in taken:
                    continue
                step = cand[1] - cx
                if step <= 0 or step > limit:
                    continue
                if abs(cand[2] - cy) > _ROW_OFFSET_RATIO * side:
                    continue
                if best is None or step < best[1] - cx:
                    best = cand
            if best is None:
                break
            chain.append(best)
            taken.add(best[0])
            cur = best
        if len(chain) > 1:
            runs.append(chain)
    return runs


def _is_even(chain: list[tuple[int, float, float, float]], even_tol: float) -> bool:
    gaps = [chain[i + 1][1] - chain[i][1] for i in range(len(chain) - 1)]
    if not gaps:
        return False
    lo = min(gaps)
    return lo > 0 and max(gaps) / lo <= even_tol


def find_dense_array_flags(
    elements: list[dict] | None,
    *,
    gap_ratio_max: float = DEFAULT_GAP_RATIO_MAX,
    run_min: int = DEFAULT_RUN_MIN,
    even_tol: float = DEFAULT_EVEN_TOL,
) -> list[bool]:
    """逐元素判断是否落在一条密排等间距阵列上。

    **逐候选**而非逐图：观众厅平面图上座椅与柱共存，按整张图删会把柱
    一起删掉。返回与入参等长的布尔表；入参不被修改。
    """
    items = list(elements or [])
    flags = [False] * len(items)
    measured = []
    for i, el in enumerate(items):
        cs = _center_size(el)
        if cs is not None:
            measured.append((i, *cs))
    if len(measured) < run_min:
        return flags
    for axis in (0, 1):
        nodes = [(i, cx, cy, s) if axis == 0 else (i, cy, cx, s)
                 for i, cx, cy, s in measured]
        chains = _runs_along(nodes, gap_ratio_max)
        for chain in chains:
            if len(chain) < run_min or not _is_even(chain, even_tol):
                continue
            for node in chain:
                flags[node[0]] = True
    return flags
