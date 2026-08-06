"""OCR 文本 → 轻量语义分类（确定性规则，纯函数，可离线测）。

把 OCR 原始文本行归到 TokenKind，并对标高/尺寸解析出数值。这是「结构化」的核心：
下游楼层/标高、轴号拼接、语义各取所需的 kind，而不必各自重复正则。
"""
from __future__ import annotations

import re

from .types import TokenKind

# 标高：±0.000 / +3.600 / -1.500 / 3.600（CAD 标高惯例为三位小数，可带正负号或 ± 前缀）
_RE_ELEVATION = re.compile(r"^[±+\-]?\d{1,3}\.\d{3}$")
# 带"标高"字样的**短标注**（`标高10.800` / `建筑标高±0.000`）。
#
# **不能只判「含标高二字」**：实测全项目 38810 条 elevation 里
# **18127 条（47%）** 是含「标高」的说明文字，例如
# `33.330标高钢桁架轮廓`、`窗顶标高43.100，窗底标高42.350`、
# `水箱架空安装，架空乘项标高42.20m`、`窗底标高：34250`（34250 是**毫米**）。
# 把它们当标高会：取到错的数值、一句话两个值只取第一个、污染楼层标高配对。
#
# 国标 §11.8.4：标高是**米制三位小数的数字**，标注在标高符号旁，不是一段话。
# 判据：整串去掉「标高」「建筑/结构」等限定词与数值后，**不应再有实质内容**。
_RE_ELEV_WORD = re.compile(r"标高")
#: 允许出现在标高短标注里的限定词（去掉它们后应当只剩数值与标点）
_RE_ELEV_QUALIFIER = re.compile(r"标高|建筑|结构|完成面|面层|设计|相对|绝对")
#: 去掉限定词、数值、标点后仍剩下的字符数上限。超过就是说明文字。
MAX_ELEV_RESIDUE = 0
# 轴号：单/双字母、1~2 位数字、或 "1/A"、"A/1" 形式
_RE_AXIS = re.compile(r"^([0-9]{1,2}|[A-Za-z]{1,2}|[0-9]{1,2}/[A-Za-z]{1,2}|[A-Za-z]{1,2}/[0-9]{1,2})$")
# 纯尺寸数字（mm，2~5 位整数，且不像标高）
_RE_DIMENSION = re.compile(r"^\d{2,5}$")
# 楼层名（中文写法）
_RE_LEVEL = re.compile(
    r"(地下[一二三四五六七八九十\d]+层|[一二三四五六七八九十\d]+层|首层|屋面|夹层|设备层|避难层|标准层|机房层)"
)
# 楼层名（工程通用短标记）：4F / F1 / B1 / B2 / RF / LF。
# **实测缺口**：上海大歌剧院 A-30-07A 剖面图右侧标高链写的正是
# `16.200 4F` / `10.800 3F` / `5.400 2F` / `±0.000 1F`，
# 而这些标记此前全部落进 `other`，楼层名整条丢失 —— 这恰恰是建模最需要的
# 楼层标高表。放在轴号判据**之前**，否则 `F1` 会被 `_RE_AXIS` 当成轴号吃掉。
_RE_LEVEL_MARK = re.compile(r"^(?:B\d{1,2}|\d{1,2}F|F\d{1,2}|RF|LF)$", re.IGNORECASE)
# 带部位前缀的楼层名：`大歌剧厅4F` / `大歌剧厅屋顶层` / `小歌剧厅B1`。
# **实测缺口**：A-20-02A 南立面图的标高链用的正是这种写法
# （`大歌剧厅3F 10.300` / `大歌剧厅4F 16.100` / `大歌剧厅屋顶层 45.500`），
# 此前因长度超阈值被判为 note/room_name，楼层名整条丢失。
# 前缀限定为中文部位名，避免把普通房间名吃进来。
_RE_PREFIXED_LEVEL = re.compile(
    r"^[一-鿿]{2,8}(?:B\d{1,2}|\d{1,2}F|F\d{1,2}|RF|屋顶层|屋面层)"
    r"[（(].*[)）]?$|^[一-鿿]{2,8}(?:B\d{1,2}|\d{1,2}F|F\d{1,2}|RF|屋顶层|屋面层)$",
    re.IGNORECASE)
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


def _is_short_elevation_note(raw: str) -> bool:
    """`标高10.800` 是标高标注；`窗顶标高43.100，窗底标高42.350` 是说明文字。

    判据：去掉限定词、**米制三位小数**、标点与空白后，不应再有实质字符。
    这同时挡掉 `窗底标高：34250`——34250 不是三位小数形式（§9 它是毫米）。
    """
    residue = _RE_ELEV_QUALIFIER.sub("", raw)
    residue = re.sub(r"[±+\-]?\d{1,3}\.\d{3}", "", residue)
    residue = re.sub(r"[\s:：,，。、()（）\-—~～]", "", residue)
    return len(residue) <= MAX_ELEV_RESIDUE


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
    if _RE_ELEV_WORD.search(raw) and _is_short_elevation_note(raw):
        m = re.search(r"[±+\-]?\d{1,3}\.\d{3}", raw)
        return "elevation", (_parse_elevation(m.group()) if m else None)

    # 楼层名（中文写法 / 工程短标记 / 带部位前缀）
    if (_RE_LEVEL.search(raw) or _RE_LEVEL_MARK.match(raw)
            or _RE_PREFIXED_LEVEL.match(raw)):
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
        # **单字不是房间名**。图框会签栏在矢量层面是被逐字拆开的
        # （`校/合/作/设/计/单/位/审/定/期/总/负/责`…），实测 A-20-01A
        # 一张立面图就产出 127 条这种单字 room_name，全是噪声。
        # 真实房间名最少两字（男卫/女卫/前厅/机房）。
        if length == 1:
            return "other", None
        if length <= 6:
            return "room_name", None
        return "note", None

    return "other", None
