"""OCR 文本 → 轻量语义分类（确定性规则，纯函数，可离线测）。

把 OCR 原始文本行归到 TokenKind，并对标高/尺寸解析出数值。这是「结构化」的核心：
下游楼层/标高、轴号拼接、语义各取所需的 kind，而不必各自重复正则。
"""
from __future__ import annotations

import re

from .types import TokenKind

# 标高：±0.000 / +3.600 / -1.500 / 3.600（CAD 标高惯例为三位小数，可带正负号或 ± 前缀）
_RE_ELEVATION = re.compile(r"^[±+\-]?\d{1,3}\.\d{3}$")
# 带"标高"字样的标高标注
_RE_ELEV_WORD = re.compile(r"标高")
# 轴号：单/双字母、1~2 位数字、或 "1/A"、"A/1" 形式
_RE_AXIS = re.compile(r"^([0-9]{1,2}|[A-Za-z]{1,2}|[0-9]{1,2}/[A-Za-z]{1,2}|[A-Za-z]{1,2}/[0-9]{1,2})$")
# 纯尺寸数字（mm，2~5 位整数，且不像标高）
_RE_DIMENSION = re.compile(r"^\d{2,5}$")
# 楼层名
_RE_LEVEL = re.compile(
    r"(地下[一二三四五六七八九十\d]+层|[一二三四五六七八九十\d]+层|首层|屋面|夹层|设备层|避难层|标准层|机房层)"
)
# CJK 判定
_RE_CJK = re.compile(r"[一-鿿]")
# 图名/标题关键词（图种 + 布置/说明），命中即判为 title
_RE_TITLE = re.compile(r"(平面图|立面图|剖面图|详图|大样图|布置图|系统图|总图|说明|图例)")


def _parse_elevation(text: str) -> float | None:
    """解析标高为米。±0.000→0.0，+3.600→3.6，-1.500→-1.5。"""
    cleaned = text.replace("±", "").replace("＋", "+").replace("－", "-")
    try:
        return float(cleaned)
    except ValueError:
        return None


def classify_text(text: str) -> tuple[TokenKind, float | None]:
    """返回 (kind, value)。value 仅对 elevation(米)/dimension(mm) 有意义，否则 None。

    判定优先级：标高 > 楼层名 > 轴号 > 尺寸 > 房间/说明/标题。
    """
    raw = text.strip()
    if not raw:
        return "other", None

    # 标高（数值形态，或"标高"字样后接数值）
    if _RE_ELEVATION.match(raw):
        return "elevation", _parse_elevation(raw)
    if _RE_ELEV_WORD.search(raw):
        m = re.search(r"[±+\-]?\d{1,3}\.\d{3}", raw)
        return "elevation", (_parse_elevation(m.group()) if m else None)

    # 楼层名
    if _RE_LEVEL.search(raw):
        return "level_name", None

    # 轴号（短 alnum）
    if _RE_AXIS.match(raw):
        return "axis", None

    # 纯尺寸数字（排除已被标高吃掉的三位小数）
    if _RE_DIMENSION.match(raw):
        return "dimension", float(raw)

    # 含中文：图名关键词→标题;否则按长度分 房间名(短)/说明(中长)
    if _RE_CJK.search(raw):
        if _RE_TITLE.search(raw):
            return "title", None
        length = len(raw)
        if length <= 6:
            return "room_name", None
        return "note", None

    return "other", None
