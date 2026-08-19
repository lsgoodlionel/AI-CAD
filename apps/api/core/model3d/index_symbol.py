"""索引符号识别（GB/T 50001 §5 索引符号与详图符号）。

**它是平面图跳转详图的唯一线索** —— 会审 133 条检查项里
「节点号」出现 19 条；而详图里才有真实的构造尺寸。

**文字路已被证伪**：索引符号是**画成圆圈**的（水平直径把圆分成上下两半，
上半详图编号、下半图纸编号），文字提取时上下是两个独立文本，
永远拼不出 `1/A-15`。实测档案层里形似 `X/Y` 的 4929 条全是
标题栏字段（`BY/DATE`）、电流互感器变比（`300/5A`）或设备型号。

⇒ 判据是**图形**：圆 + 圆内一条横贯圆心的水平线。
这也正是它与**轴号圈**（§8.0.2，圆内只有一个字符、无分割线）的唯一区别。
"""
from __future__ import annotations

#: 分割线长度至少要有直径的这个比例 —— 字符里的短横（`工`/`二`）远达不到。
MIN_DIVIDER_LENGTH_RATIO = 0.7

#: 线到圆心的垂距上限（占直径的比例）—— 贴边的横线不是分割线。
MAX_CENTRE_OFFSET_RATIO = 0.15

#: 允许的倾角（用高差/长度近似 tan）——§5 规定是**水平**直径。
MAX_SLOPE = 0.12


def has_horizontal_divider(circle: dict, strokes: list) -> bool:
    """圆内是否有一条横贯圆心的水平分割线。"""
    try:
        cx = float(circle["cx"])
        cy = float(circle["cy"])
        diameter = float(circle["diameter_pt"])
    except (KeyError, TypeError, ValueError):
        return False
    if diameter <= 0:
        return False

    radius = diameter / 2.0
    min_length = diameter * MIN_DIVIDER_LENGTH_RATIO
    max_offset = diameter * MAX_CENTRE_OFFSET_RATIO

    for stroke in strokes or ():
        if len(stroke) < 4:
            continue
        x0, y0, x1, y1 = (float(v) for v in stroke[:4])
        width = abs(x1 - x0)
        if width < min_length:
            continue
        if width and abs(y1 - y0) / width > MAX_SLOPE:
            continue                       # 不够水平
        if abs((y0 + y1) / 2.0 - cy) > max_offset:
            continue                       # 不过圆心
        # 横向范围要压住圆心（贴边的长横线不算）
        if not (min(x0, x1) <= cx + radius and max(x0, x1) >= cx - radius):
            continue
        return True
    return False


def split_index_symbols(circles: list, strokes: list) -> tuple[list, list]:
    """一批圆 → `(索引符号, 轴号圈)`。

    **不删除任何圆**：分流而非过滤 —— 判错只影响归类，不丢数据。
    """
    index_symbols: list = []
    axis_circles: list = []
    for circle in circles or ():
        if has_horizontal_divider(circle, strokes):
            index_symbols.append(circle)
        else:
            axis_circles.append(circle)
    return index_symbols, axis_circles
