"""楼层解析工具（3D 模型基座 — 楼层堆叠骨架）。

从图名 / 图号 / 审图定位 levels 文本中提取楼层信息，输出统一的
(key, label, order) 三元组供 model_builder 堆叠排序：

- B2 / 地下二层 / 负二层 → ('B2', '地下二层', -2)
- 3F / 三层 / 3层        → ('F3', '3层', 3)
- 屋面 / 屋顶            → ('RF', '屋面', 99)
- 基础 / 承台            → ('FD', '基础层', -98)
- 匹配不到               → None（图纸级兜底 ('UNZONED','未分层',0)）

蓝图：docs/MODEL_BASE_BLUEPRINT.md 第 5 节。
"""
import re
from collections import Counter

UNZONED_FLOOR: tuple[str, str, int] = ("UNZONED", "未分层", 0)
ROOF_FLOOR: tuple[str, str, int] = ("RF", "屋面", 99)
FOUNDATION_FLOOR: tuple[str, str, int] = ("FD", "基础层", -98)

_CN_DIGITS = {
    "零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}
_CN_UNITS = ["", "一", "二", "三", "四", "五", "六", "七", "八", "九"]

_RE_ROOF = re.compile(r"屋面|屋顶")
_RE_FOUNDATION = re.compile(r"基础|承台")
_RE_BASEMENT = re.compile(
    r"B(\d{1,2})(?!\d)|地下([0-9一二两三四五六七八九十]{1,3})层?|负([0-9一二两三四五六七八九十]{1,3})层",
    re.IGNORECASE,
)
_RE_FLOOR = re.compile(
    r"(\d{1,3})\s*F(?![0-9A-Za-z])|([0-9一二两三四五六七八九十]{1,3})层",
    re.IGNORECASE,
)


def _cn_to_int(text: str) -> int | None:
    """中文数字/阿拉伯数字 → 整数（支持 一~九十九）。"""
    if text.isdigit():
        return int(text)
    if "十" in text:
        tens_part, _, units_part = text.partition("十")
        tens = _CN_DIGITS.get(tens_part, 1) if tens_part else 1
        units = _CN_DIGITS.get(units_part, 0) if units_part else 0
        return tens * 10 + units
    return _CN_DIGITS.get(text)


def _int_to_cn(number: int) -> str:
    """整数 → 中文数字（1~99，用于地下层中文标签）。"""
    if number < 10:
        return _CN_UNITS[number]
    tens, units = divmod(number, 10)
    tens_text = "十" if tens == 1 else f"{_CN_UNITS[tens]}十"
    return f"{tens_text}{_CN_UNITS[units]}" if units else tens_text


# 楼层数值合理范围（超出视为轴号/桩号/编号伪匹配，非真实楼层）
_MAX_BASEMENT_FLOORS = 9
_MAX_ABOVE_GROUND_FLOORS = 120


def _match_basement(text: str) -> tuple[str, str, int] | None:
    """地下楼层匹配：B2 / 地下二层 / 负二层。"""
    match = _RE_BASEMENT.search(text)
    if match is None:
        return None
    raw = next((g for g in match.groups() if g), "")
    number = _cn_to_int(raw)
    if not number or number > _MAX_BASEMENT_FLOORS:
        return None
    return (f"B{number}", f"地下{_int_to_cn(number)}层", -number)


def _match_above_ground(text: str) -> tuple[str, str, int] | None:
    """地上楼层匹配：3F / 三层 / 3层。"""
    match = _RE_FLOOR.search(text)
    if match is None:
        return None
    raw = next((g for g in match.groups() if g), "")
    number = _cn_to_int(raw)
    if not number or number > _MAX_ABOVE_GROUND_FLOORS:
        return None
    return (f"F{number}", f"{number}层", number)


def parse_floor(text: str) -> tuple[str, str, int] | None:
    """从图名/图号/location.levels 文本提取楼层。

    返回 (key, label, order)：B2→('B2','地下二层',-2)；3F/三层/3层→('F3','3层',3)；
    屋面→('RF','屋面',99)；基础→('FD','基础层',-98)。匹配不到→None。
    """
    if not text:
        return None
    if _RE_ROOF.search(text):
        return ROOF_FLOOR
    if _RE_FOUNDATION.search(text):
        return FOUNDATION_FLOOR
    basement = _match_basement(text)
    if basement is not None:
        return basement
    # 命中地下语境但数值超范围（如"地下八十层"伪匹配）→ 不再降级按地上层解析
    if _RE_BASEMENT.search(text):
        return None
    return _match_above_ground(text)


def floor_of_drawing(drawing: dict, issue_levels: list[str]) -> tuple[str, str, int]:
    """优先图纸 title/drawing_no，再取该图 issues 的 location levels 众数；都无 → ('UNZONED','未分层',0)。"""
    for text in (drawing.get("title"), drawing.get("drawing_no")):
        parsed = parse_floor(str(text or ""))
        if parsed is not None:
            return parsed

    parsed_levels = [
        floor for floor in (parse_floor(str(level or "")) for level in issue_levels)
        if floor is not None
    ]
    if parsed_levels:
        return Counter(parsed_levels).most_common(1)[0][0]
    return UNZONED_FLOOR
