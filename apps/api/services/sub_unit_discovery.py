"""子单体发现 —— 从楼层名前缀跨图共识,**零后缀词表**。纯函数。

**通用性要求**(用户复核口径):「××厅」是剧院的命名,「A栋」是住宅的,
「T2 塔楼」是综合体的 —— 任何后缀词表都只覆盖一类工程。
通用的是**结构**:楼层名 = [子单体前缀] + 标准层 token。
前缀在标准层 token 之前、且**跨多个楼层名一致出现**,即为子单体。

这也是架构待办「厅级单体粒度」的通用解法入口:大歌剧厅 F4=16.1 与
中歌剧厅 F4=14.5 装不进 main/south/north 的楼层表,先把厅**发现**出来,
粒度问题才有承载对象。
"""
from __future__ import annotations

import re

#: 标准层 token:数字层(3F/4层/三层)、地下层(B1/地下一层)、屋面/夹层等。
#: 这是**国标层面的楼层表达**(通用),不是某工程的命名。
_FLOOR_TOKEN_RE = re.compile(
    r"(B?\d+F?|[一二三四五六七八九十]+层|地下[一二三四五六七八九十\d]+层"
    r"|\d+层|屋面|屋顶|夹层|机房层)")

#: 一个前缀至少要在几个**不同**楼层名里出现才算子单体。
#: 孤证不立 —— 只出现一次的前缀可能是房间名或笔误。
MIN_PREFIX_OCCURRENCES = 2


def split_level_prefix(level_name: str) -> tuple[str | None, str]:
    """「A栋3F」→ ("A栋", "3F");无前缀 → (None, 原名)。

    前缀 = 标准层 token 之前的非空片段。token 打头(如「3F」「地下一层」)
    即无前缀。
    """
    name = str(level_name or "").strip()
    matched = _FLOOR_TOKEN_RE.search(name)
    if not matched or matched.start() == 0:
        return None, name
    prefix = name[:matched.start()].strip("（(、-· ")
    if not prefix:
        return None, name
    return prefix, name[matched.start():]


def discover_sub_units(level_names: list[str] | None) -> set[str]:
    """跨楼层名一致出现的前缀 → 子单体集合。

    「一致出现」按**不同楼层名**计(同名重复只算一次)——
    同一张图把「A栋3F」写八遍不构成更强的证据。
    """
    seen: dict[str, set[str]] = {}
    for name in set(level_names or ()):
        prefix, rest = split_level_prefix(name)
        if prefix:
            seen.setdefault(prefix, set()).add(rest)
    return {prefix for prefix, floors in seen.items()
            if len(floors) >= MIN_PREFIX_OCCURRENCES}
