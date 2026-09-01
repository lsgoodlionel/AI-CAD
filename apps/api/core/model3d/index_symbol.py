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

#: **索引符号的圆直径**（GB/T 50001 §5）：标准值 8~10mm，登记在
#: `drawing_conventions.INDEX_SYMBOL_DIAMETER_MM`（单一来源）。
#: 这里用的是**放宽后的工程容差**，不是标准值本身——两者不同，故不直接引用：
#: 实测逼出的判据 —— 栈桥详图上 13 个「索引符号」直径仅 **4.74mm**，
#: 裁开一看圈内暗像素 0%（本来就没字），那是钢筋断面之类的小圆。
#: 上限放到 12mm 容差；再大是**详图符号**（§5 直径 14mm 粗实线圆），
#: 它标的是「我就是那张详图」而非「去看那张详图」，语义相反。
INDEX_SYMBOL_DIAMETER_MM = (7.0, 12.0)

_MM_PER_PT = 25.4 / 72.0

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
    # **直径必须落在 §5 规定的区间** —— 小圆是钢筋断面、大圆是详图符号
    lo, hi = INDEX_SYMBOL_DIAMETER_MM
    if not lo <= diameter * _MM_PER_PT <= hi:
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


# ── 读出圈内编号：建立平面图 ↔ 详图的跳转 ──────────────────────

import re
from dataclasses import dataclass

#: 半区相对圆的**内缩比例** —— 弧线本身会被 OCR 当成字符笔画。
HALF_INSET_RATIO = 0.18

#: 下半画一横 = 详图就在本张图上（§5）。
#: 含制表横线 U+2500 —— OCR 把长横读成它是常见形态（实测）。
_SAME_SHEET_RE = re.compile(r"^[-—－–_\u2500\u2501\uff0d]{1,4}$")

#: 详图编号：一到两位数字，或单个大写字母（`A/15`）。
_DETAIL_NO_RE = re.compile(r"^(?:\d{1,2}|[A-Z])$")

#: 图纸编号：数字或「字母-数字」（`15` / `A-15` / `结施12`）。
_SHEET_NO_RE = re.compile(r"^[A-Za-z\u4e00-\u9fff]{0,4}[-]?\d{1,3}[A-Za-z]?$")


@dataclass(frozen=True)
class IndexReference:
    """一条「看某图某详图」的跳转引用。"""
    detail_no: str          # 详图编号（上半）
    sheet_no: str | None    # 图纸编号（下半）；本图时为 None
    same_sheet: bool        # 下半是一横 → 详图在本张图上


def index_symbol_halves(circle: dict) -> tuple[tuple, tuple]:
    """索引符号 → (上半区, 下半区) 的裁剪矩形（PDF 点坐标）。

    上半是详图编号、下半是图纸编号（§5）。两半各自**内缩**，
    避开圆弧 —— 弧线会被 OCR 读成字符笔画。
    """
    cx = float(circle["cx"])
    cy = float(circle["cy"])
    radius = float(circle["diameter_pt"]) / 2.0
    inset = radius * HALF_INSET_RATIO
    half_w = radius - inset
    return (
        (cx - half_w, cy - radius + inset, cx + half_w, cy),
        (cx - half_w, cy, cx + half_w, cy + radius - inset),
    )


def _first_clean(tokens: list) -> str:
    for token in tokens or ():
        text = str(token or "").strip()
        if text:
            return text
    return ""


def parse_index_reference(top_tokens: list,
                          bottom_tokens: list) -> IndexReference | None:
    """上下两半的 OCR 结果 → 一条跳转引用（**读不出就不猜**）。"""
    detail = _first_clean(top_tokens).upper()
    bottom = _first_clean(bottom_tokens)
    if not detail or not _DETAIL_NO_RE.match(detail):
        return None
    if not bottom:
        return None
    if _SAME_SHEET_RE.match(bottom):
        return IndexReference(detail_no=detail, sheet_no=None, same_sheet=True)
    if not _SHEET_NO_RE.match(bottom):
        return None
    return IndexReference(detail_no=detail, sheet_no=bottom, same_sheet=False)
