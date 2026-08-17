"""楼层名↔标高配对 → 建模 `z_overrides`(纯函数,离线可测)。

**P2 接线**(见 `docs/MODELING_PIPELINE_BLUEPRINT.md`):
`level_elevation_pairing` 已经能从立面/剖面图读出**带名字**的楼层标高
(`6F（设备层） 36.800`),本模块把它翻成 `model_story.normalize_story_table`
能吃的 `z_overrides` 形状。

**为什么按名字匹配而不是按位置**:现有 `section_z_recovery` 用序列窗口对齐
(第 n 个标高配第 n 层),一旦某层漏读,整条序列错位。
立面图上写的是楼层名,名字直接给出归属,不用猜。

**为什么这件事重要**:模型 v31 的 13 层里 10 层标高是
`DEFAULT_STORY_HEIGHT_M = 4.5` 硬推的,与图纸实测最大差 **11.9 米**
(F6 设备层:模型 24.9,图纸 36.800)。
"""
from __future__ import annotations

import re
from typing import Any

#: 同一层被多张图读出多个标高时，差值在此以内视为同一个值（取均值）；
#: 超过则判为**冲突**，两个都不要。
#:
#: 实测依据：一个项目有多套标高体系——大歌剧厅 3F=10.300，
#: 而 7-7 剖面 3F=10.800，差 0.5m。混着取会得到一个既不是这个
#: 也不是那个的值，**宁可留默认值等人工**。
#: 取 0.05m：容得下读数抖动，容不下真实的体系差异。
CONFLICT_TOLERANCE_M = 0.05

#: 来源标记。落到 `StoryLevel.source` 上，用于区分
#: 「从图纸读的」与「默认值推的」——这是阶段门禁的依据。
OVERRIDE_SOURCE = "level_elevation_pairing"

#: 首层 `F1` 是 ±0.000 基准层，允许的偏差（米）。
#: 实测配对里有把 `1.000` 配给 `1F` 的，那是配错了。
GROUND_FLOOR_TOLERANCE_M = 0.5

#: 采用一个标高所需的**最少佐证张数**。孤证不立。
#:
#: 实测：north 的 F2/F3/F5/RF 各有 **12 张图**给出完全一致的值，
#: 而 main 的 `F3=2.944`、`RF=23.400` 只有 **1 张**佐证——
#: 2.944 明显不像楼层标高，更像某个尺寸被配错了。
#: 一张图配出来的值没有交叉印证，风险高于留默认值。
#:
#: 小项目每层可能只有一张立面图，故可调。
MIN_SAMPLES = 2

#: `4F` / `F4` / `B1` / `RF`。允许前后有部位名与功能后缀：
#: 实测 `大歌剧厅4F`、`6F（设备层）`、`6F（设备层`（OCR 丢右括号）、
#: `5F （设备层）`（中间有空格）。
_RE_MARK = re.compile(
    r"^[一-鿿]{0,8}\s*(?:(?P<b>B)(?P<bn>\d{1,2})"
    r"|(?P<fn>\d{1,2})\s*F|F\s*(?P<fn2>\d{1,2})|(?P<rf>RF))\s*"
    r"(?:[（(].*)?$",
    re.IGNORECASE)

#: 中文楼层名。**必须整串匹配**——`不上人屋面区域做法3` 含「屋面」
#: 但不是楼层名，用 search 会把它误判成 RF。
_CN_DIGITS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
_RE_CN_BASEMENT = re.compile(r"^[一-鿿]{0,6}地下([一二三四五六七八九十\d]+)层$")
_RE_CN_FLOOR = re.compile(r"^[一-鿿]{0,6}?([一二三四五六七八九十\d]+)层$")
_RE_CN_GROUND = re.compile(r"^[一-鿿]{0,6}首层$")
_RE_CN_ROOF = re.compile(r"^[一-鿿]{0,6}(?:屋面|屋顶层|屋面层)$")


def _cn_number(text: str) -> int | None:
    if text.isdigit():
        return int(text)
    if len(text) == 1:
        return _CN_DIGITS.get(text)
    if text.startswith("十"):                      # 十一 → 11
        rest = text[1:]
        return 10 + (_CN_DIGITS.get(rest, 0) if rest else 0)
    if text.endswith("十"):                        # 二十 → 20
        return _CN_DIGITS.get(text[0], 0) * 10
    if "十" in text:                                # 二十一 → 21
        tens, ones = text.split("十", 1)
        return _CN_DIGITS.get(tens, 0) * 10 + _CN_DIGITS.get(ones, 0)
    return None


def story_key_for_level_name(name: str) -> str | None:
    """楼层名 → `story_key`（`F4` / `B1` / `RF`）。认不出返回 ``None``。

    **认不出就返回 None，绝不硬凑**：`屋面做法了`（OCR 错字）、
    `大歌剧厅顶板`（构件名）都不是楼层，给它们配标高会让整层构件放错高度。
    """
    raw = str(name or "").strip()
    if not raw:
        return None

    match = _RE_MARK.match(raw)
    if match:
        if match.group("rf"):
            return "RF"
        if match.group("b"):
            return f"B{int(match.group('bn'))}"
        number = match.group("fn") or match.group("fn2")
        if number:
            return f"F{int(number)}"

    if _RE_CN_ROOF.match(raw):
        return "RF"
    if _RE_CN_GROUND.match(raw):
        return "F1"
    basement = _RE_CN_BASEMENT.match(raw)
    if basement:
        value = _cn_number(basement.group(1))
        return f"B{value}" if value else None
    floor = _RE_CN_FLOOR.match(raw)
    if floor:
        value = _cn_number(floor.group(1))
        return f"F{value}" if value else None
    return None


def is_elevation_consistent(story_key: str, elevation_m: float) -> bool:
    """楼层号与标高的**国标一致性**校验。

    地下层标高按定义 ≤ 0（相对 ±0.000）；`F1` 是基准层应接近 0；
    `F2` 及以上应 > 0。国标 §11.8.5 规定负数标高注「−」——
    **正负号本身就是信息**，不能忽略。

    **实测必要性**：north 单体的 `B1` 同时读出 **−5.500 和 +5.500**、
    `B2` 读出 **−9.300 和 +9.300**。正值显然是把别的东西
    （轴号 `B1`、构件编号）当成了楼层标记；不滤掉就会把两个值判成
    「冲突」而**双双丢弃**，白白损失一层真标高。
    """
    key = str(story_key or "").upper()
    if key.startswith("B"):
        return elevation_m <= 0.0
    if key == "F1":
        return abs(elevation_m) <= GROUND_FLOOR_TOLERANCE_M
    if key.startswith("F") or key == "RF":
        return elevation_m > 0.0
    return True


def build_z_overrides(
    pairs: list[dict], stories: list[dict], *,
    conflict_tolerance_m: float = CONFLICT_TOLERANCE_M,
    min_samples: int = MIN_SAMPLES,
) -> dict[tuple[str, str], dict[str, Any]]:
    """配对结果 + 楼层表 → `z_overrides`。

    `pairs` 形如 ``[{"level_name": str, "elevation_m": float,
    "building_unit_key": str | None}]``。

    **按 (单体, 楼层) 分组**——同一层号在不同单体是不同标高，**不是冲突**。
    实测 north（小歌剧厅）F3=9.350（12 张图一致），south（大歌剧厅）F3=10.300；
    不分单体就会被判成冲突而两个都丢掉。配对未带单体时退回「所有同名层」，
    保持向后兼容。

    只为**楼层表里已有**的层产出覆盖——不凭空造层。
    值先过 `is_elevation_consistent` 国标校验，再按容差判冲突。
    """
    known: dict[str, set[str]] = {}
    for story in stories or ():
        key = str(story.get("story_key") or "")
        unit = str(story.get("building_unit_key") or "")
        if key:
            known.setdefault(key, set()).add(unit)

    # 按 (单体, 楼层) 收集。单体缺失时用 None 占位，落库时展开到所有同名层。
    collected: dict[tuple[str | None, str], list[float]] = {}
    for pair in pairs or ():
        story_key = story_key_for_level_name(pair.get("level_name", ""))
        if story_key is None or story_key not in known:
            continue
        try:
            value = float(pair["elevation_m"])
        except (KeyError, TypeError, ValueError):
            continue
        # 国标一致性：地下层为负、首层近零、地上层为正
        if not is_elevation_consistent(story_key, value):
            continue
        unit = pair.get("building_unit_key") or None
        if unit is not None and unit not in known[story_key]:
            continue          # 该单体没有这一层，不凭空造
        collected.setdefault((unit, story_key), []).append(value)

    overrides: dict[tuple[str, str], dict[str, Any]] = {}
    for (unit, story_key), values in collected.items():
        if len(values) < min_samples:
            continue          # 孤证不立——见 MIN_SAMPLES
        if max(values) - min(values) > conflict_tolerance_m:
            # 同一单体同一层仍有分歧——宁可留默认值等人工
            continue
        elevation = round(sum(values) / len(values), 3)
        targets = [unit] if unit is not None else sorted(known[story_key])
        for target in targets:
            overrides[(target, story_key)] = {
                "elevation_bottom_m": elevation,
                "source": OVERRIDE_SOURCE,
                "sample_count": len(values),
            }
    return overrides
