"""多栏阅读顺序恢复 —— 读懂设计说明的前置。

**实测失败驱动**：直接按提取顺序重组说明文字，3352 行只归出 7 个条目、
平均 6493 字，内容是多个不相关条文的拼接。根因是
**PDF 文字提取顺序不是阅读顺序** —— 工程图的说明是多栏排版
（通常 3~5 栏），按提取顺序读会在栏间反复跳跃。

人能读懂说明，前提是**知道怎么按栏读**。做法：
x 坐标聚类分栏 → 栏内自上而下 → 栏间自左而右。

**为什么用间隙聚类而不是固定栏数**：栏数随图纸而变（实测 3~5 栏），
硬编码栏数会在别的图上崩掉；而**栏间空白远大于栏内行距**，
这个特征跨图纸稳定。
"""
from __future__ import annotations

from statistics import median

#: 栏间间隙相对**栏内间隙**的倍数。
#: **不能拿「全部间隙的中位数」当基准**：实测两栏用例 gaps 为
#: [0, 700, 0] —— 栏间隙本身就是唯一的非零间隙，拿它当典型值再放大，
#: 阈值高到永远分不出栏。正确的判据是间隙分布的**双峰性**：
#: 栏内间隙密集在小值区，栏间是孤立的大值。
COLUMN_GAP_FACTOR = 6.0

#: 分栏所需的最小绝对间隙（点）。防止在字号极小、整体紧凑的图上
#: 把正常字距当成栏边界。工程图栏间空白实测在百点量级。
MIN_COLUMN_GAP_PT = 40.0

#: 同一行的 y 容差（点）。工程图说明字号小、行距紧，取 3 点。
SAME_LINE_TOLERANCE_PT = 3.0


def _xy_of(token) -> tuple[float, float] | None:
    """取 token 的 (x, y)。

    **档案层有两种位置结构**（实测）：OCR 存 `{"bbox": [x0,y0,x1,y1]}`、
    矢量文字存 `{"x":…, "y":…}`。只认后者会让 **2469 条 OCR 记录
    全部被判「无位置」**，版面分析直接失效（检出 0 栏）。
    """
    if not isinstance(token, dict):
        return None
    try:
        return float(token["x"]), float(token["y"])
    except (KeyError, TypeError, ValueError):
        pass
    bbox = token.get("bbox")
    if isinstance(bbox, (list, tuple)) and len(bbox) >= 2:
        try:
            return float(bbox[0]), float(bbox[1])
        except (TypeError, ValueError):
            return None
    return None


def _positioned(tokens: list) -> tuple[list, list]:
    """拆出有位置与无位置的 token（无位置的不丢，末尾追加）。"""
    with_pos, without = [], []
    for token in tokens or []:
        (with_pos if _xy_of(token) is not None else without).append(token)
    return with_pos, without


def detect_columns(tokens: list | None) -> list[list]:
    """按 x 坐标把 token 分栏（间隙聚类）。

    返回按栏左边界排序的分组；无位置 token 不参与分栏。
    """
    with_pos, _ = _positioned(tokens)
    if not with_pos:
        return []
    ordered = sorted(with_pos, key=lambda t: _xy_of(t)[0])
    xs = [_xy_of(t)[0] for t in ordered]
    gaps = [b - a for a, b in zip(xs, xs[1:])]
    if not gaps:
        return [ordered]

    # **双峰判据**：栏内间隙密集在小值区，栏间是孤立大值。
    # 取「小值区」的代表（下半部分的中位数）作栏内基准，
    # 超过它 COLUMN_GAP_FACTOR 倍且绝对值够大的，才算栏边界。
    if not any(g > 0 for g in gaps):
        return [ordered]                      # x 全同 —— 只有一栏
    ordered_gaps = sorted(gaps)
    lower_half = ordered_gaps[:max(1, len(ordered_gaps) // 2)]
    intra = median(lower_half)
    threshold = max(intra * COLUMN_GAP_FACTOR, MIN_COLUMN_GAP_PT)

    columns: list[list] = [[ordered[0]]]
    for gap, token in zip(gaps, ordered[1:]):
        if gap > threshold:
            columns.append([token])
        else:
            columns[-1].append(token)
    return columns


def sort_by_reading_order(tokens: list | None) -> list:
    """token → **阅读顺序**（栏内自上而下、栏间自左而右）。

    同一行内（y 差 ≤ `SAME_LINE_TOLERANCE_PT`）按 x 从左到右 ——
    一行被切成多个 token 时要拼对。
    """
    with_pos, without = _positioned(tokens)
    if not with_pos:
        return list(without)

    out: list = []
    for column in detect_columns(with_pos):
        # 先按 y 归行，行内再按 x —— 直接 (y, x) 排序会因 y 的微小抖动错行
        rows: list[tuple[float, list]] = []
        for token in sorted(column, key=lambda t: _xy_of(t)[1]):
            y = _xy_of(token)[1]
            if rows and abs(y - rows[-1][0]) <= SAME_LINE_TOLERANCE_PT:
                rows[-1][1].append(token)
            else:
                rows.append((y, [token]))
        for _y, row in rows:
            out.extend(sorted(row, key=lambda t: _xy_of(t)[0]))
    return out + without


def merge_into_lines(tokens: list | None) -> list[str]:
    """token → **成行的文本**（阅读顺序）。

    **为什么必须有这一步**（实测）：档案层存的是**单字符 token**
    （`A`、`筑`），不是行 —— `1.2` 这样的条文号永远不会出现在
    单个 token 里，条文重组的输入假设从根上就错了。

    同时**去同位置重复**：实测同一点存了 8 条 `A`、4 条 `筑`
    （抽取重复入库），不去重会把一行写成 `AAAAAAAA`。
    """
    with_pos, without = _positioned(tokens)
    lines: list[str] = []
    for column in detect_columns(with_pos):
        rows: list[tuple[float, list]] = []
        for token in sorted(column, key=lambda t: _xy_of(t)[1]):
            y = _xy_of(token)[1]
            if rows and abs(y - rows[-1][0]) <= SAME_LINE_TOLERANCE_PT:
                rows[-1][1].append(token)
            else:
                rows.append((y, [token]))
        for _y, row in rows:
            seen: set = set()
            parts: list[str] = []
            for token in sorted(row, key=lambda t: _xy_of(t)[0]):
                tx, ty = _xy_of(token)
                key = (round(tx, 1), round(ty, 1),
                       str(token.get("text") or ""))
                if key in seen:
                    continue          # 同位置同字 —— 重复入库
                seen.add(key)
                parts.append(str(token.get("text") or ""))
            text = "".join(parts).strip()
            if text:
                lines.append(text)
    lines.extend(str(t.get("text") or "").strip() for t in without
                 if str(t.get("text") or "").strip())
    return lines
