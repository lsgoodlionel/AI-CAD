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

#: 标准层 token:必须是**真楼层形态** —— 数字限 1~2 位且带 F/层,
#: 或 B 层、中文数字层、屋面/屋顶/夹层/机房层。
#: **裸数字不算**:实测「AHU3」(空调机组编号)被松正则拆成 AHU+3,
#: 把设备编号学成了子单体。这是国标层面的楼层表达(通用),非某工程命名。
#: 「第五层」的「第」、「上人屋面」的「上人」是楼层表达的一部分,
#: 不是前缀(实测它们被拆成了假子单体)。
# 「负3F」是地下三层的口语写法、「出屋面」是屋面以上构筑物层 ——
# 两者都是**中文建筑图纸的通用楼层表达**（非某工程特有）。
# 不并进 token 的话，`\d{1,2}F` 会从索引 1 匹配，把「负」「出」
# 剩成前缀，凭空造出子单体（第二工程实测 17 + 2 例）。
_FLOOR_TOKEN_RE = re.compile(
    r"(B\d{1,2}|负?\d{1,2}F|负?\d{1,2}层|第?[一二三四五六七八九十]+层"
    r"|负[一二三四五六七八九十]+层|地下[一二三四五六七八九十\d]+层"
    r"|(?:出|不?上人)?屋面|屋顶|夹层|机房层)")

#: 前缀合法性:须含中文、不含编号符号。仍是**结构约束**而非词表 ——
#: 设备编号(AHU/AP\/C/ANT-)是拉丁字母+符号,而中文图纸的单体名
#: (A栋/大歌剧厅/T2塔楼)必含中文字符。纯英文命名工程是已知边界。
#: 括号与句读也排除:实测「23.430（结构）第」这类 OCR 句子碎片
#: 会带着括号/小数点混进来。牺牲「北区（小歌剧厅）」组合形
#: —— 裸名「小歌剧厅」仍会被独立发现,信息不丢。
_PREFIX_OK_RE = re.compile(
    r"^[^/:：;；\-（）()，。.]*[\u4e00-\u9fff][^/:：;；\-（）()，。.]*$")

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
    if not prefix or not _PREFIX_OK_RE.match(prefix):
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
