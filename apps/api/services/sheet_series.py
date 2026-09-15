"""图纸系列：「总图」与它的「分图（一）（二）…」画的是同一批构件。

同一楼层里两者都进选图时，构件会被算几遍、各自按自己的原点摆成几片
（实测大歌剧院「1层」四张图摆成四片，全层 219×177 米）。本模块只做
**认系列、丢被替代的总图**；为什么留分图见 `tests/test_sheet_series.py`。

判据只用 GB/T 50001 通用的图名写法 —— 「总图」一词与图名末尾的
「（一）/(1)」序号 —— **不认图号**（图号体系因工程而异）。
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Callable

#: 图名末尾的分图序号：`（三）`、`(2)`，全角半角括号都有。
#: 数字限两位 —— `(2015)` 是年份/版次，不是分图序号。
_PART_SUFFIX_RE = re.compile(r"\s*[（(]\s*(?:[一二三四五六七八九十]+|\d{1,2})\s*[)）]\s*$")

#: 总图：**只认图名末尾**（`…平面总图`、`…配筋总图`）。GB/T 50001 里「总图」
#: 也是专业名，`总图-竣工图--道路平面图` 这类前缀不能当成某张平面的总图。
#: 「总平面图」（场地图）是「总」「平面图」，不含这个词，天然不命中。
_OVERVIEW_RE = re.compile(r"总图\s*$")


def is_part(title: str | None) -> bool:
    """图名末尾带分图序号。"""
    return bool(_PART_SUFFIX_RE.search(str(title or "")))


def is_overview(title: str | None) -> bool:
    """图名是某张平面的总图（以「总图」结尾）。"""
    return bool(_OVERVIEW_RE.search(str(title or "")))


def series_key(title: str | None) -> str:
    """同一系列的总图与分图得到同一个键：去掉分图序号，末尾「总图」读作「图」。"""
    text = _PART_SUFFIX_RE.sub("", str(title or ""))
    return re.sub(r"\s+", "", _OVERVIEW_RE.sub("图", text))


def drop_superseded_overviews(
    drawings: list[dict], *,
    is_protected: Callable[[dict], bool] | None = None,
    max_parts: int | None = None,
) -> tuple[list[dict], list[dict]]:
    """→ (保留, 被同系列分图替代的总图)。以下三种情况总图照留：

    - 本层没有它的分图 —— 它是唯一来源；
    - 分图张数超过 `max_parts`（选图配额）—— 装不下的那几张要总图兜着；
    - `is_protected(总图)` 为真 —— 例如它有工程坐标定位（位置绝对可信）。
    """
    parts = Counter(series_key(d.get("title")) for d in drawings if is_part(d.get("title")))

    def superseded(drawing: dict) -> bool:
        title = drawing.get("title")
        if not is_overview(title):
            return False
        count = parts.get(series_key(title), 0)
        if count == 0 or (max_parts is not None and count > max_parts):
            return False
        return not (is_protected is not None and is_protected(drawing))

    kept = [d for d in drawings if not superseded(d)]
    dropped = [d for d in drawings if superseded(d)]
    return kept, dropped
