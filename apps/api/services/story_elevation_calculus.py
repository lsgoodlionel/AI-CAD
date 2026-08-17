"""层高 ↔ 标高的计算关系(工程约束求解)。纯函数。

## 领域知识(决定数据从哪来、怎么算)

- **标高**:一般标注在**剖面图/立面图**内,常以**表格**形式给出各层楼面绝对标高;
- **层高**:标注在对应结构/建筑的**平面、剖面图纸**上,以**标高标注符号**给出;
- 二者是**差分/累加关系**——根据上下层结构楼板的标高,即可算出该层层高;
  反之给定基准标高 + 各层层高,可推出全部楼面标高。

    height[i] = elevation[i+1] - elevation[i]
    elevation[i] = elevation[base] + Σ height[base..i-1]

## 为什么要显式建模这个关系

1. **互补恢复**:某一侧缺失可由另一侧推出(剖面缺表格时用平面层高累加,反之亦然);
2. **交叉校验**:两侧都有时必须自洽,不自洽处即数据错误所在(定位问题比猜测更可靠);
3. **合理性约束**:层高必在工程区间(2.5–9m),越界说明标高识别有误。
"""
from __future__ import annotations

#: 合理层高区间(米)——越界即判定标高/层高识别有误
MIN_STORY_HEIGHT_M = 2.5
MAX_STORY_HEIGHT_M = 9.0
#: 自洽判定容差(米):OCR/换算抖动允许的偏差
CONSISTENCY_TOLERANCE_M = 0.05


def heights_from_elevations(levels: list[dict]) -> list[dict]:
    """楼面标高序列 → 层高序列(差分)。

    levels: [{"story_key", "order", "elevation_m"}](按 order 升序处理)。
    返回 [{"story_key", "height_m", "reasonable"}];顶层无上层 → height_m=None。
    """
    ordered = sorted(
        [lv for lv in levels or [] if lv.get("elevation_m") is not None],
        key=lambda lv: lv.get("order") or 0,
    )
    out: list[dict] = []
    for i, level in enumerate(ordered):
        height = None
        if i + 1 < len(ordered):
            height = round(
                float(ordered[i + 1]["elevation_m"]) - float(level["elevation_m"]), 3)
        out.append({
            "story_key": level.get("story_key"),
            "height_m": height,
            "reasonable": (None if height is None
                           else MIN_STORY_HEIGHT_M <= height <= MAX_STORY_HEIGHT_M),
        })
    return out


def elevations_from_heights(
    base_story_key: str, base_elevation_m: float, heights: list[dict],
) -> list[dict]:
    """基准标高 + 层高序列 → 全部楼面标高(累加)。

    heights: [{"story_key", "order", "height_m"}](按 order 升序)。
    自基准层向上累加、向下回减;缺层高的层之后无法继续推(截断)。
    """
    ordered = sorted(heights or [], key=lambda h: h.get("order") or 0)
    keys = [h.get("story_key") for h in ordered]
    if base_story_key not in keys:
        return []
    base_idx = keys.index(base_story_key)
    out: list[dict] = [None] * len(ordered)  # type: ignore[list-item]
    out[base_idx] = {"story_key": base_story_key,
                     "elevation_m": round(float(base_elevation_m), 3)}
    # 向上累加
    current = float(base_elevation_m)
    for i in range(base_idx, len(ordered) - 1):
        h = ordered[i].get("height_m")
        if h is None:
            break
        current = round(current + float(h), 3)
        out[i + 1] = {"story_key": keys[i + 1], "elevation_m": current}
    # 向下回减
    current = float(base_elevation_m)
    for i in range(base_idx - 1, -1, -1):
        h = ordered[i].get("height_m")
        if h is None:
            break
        current = round(current - float(h), 3)
        out[i] = {"story_key": keys[i], "elevation_m": current}
    return [o for o in out if o is not None]


def cross_validate(
    elevations: list[dict], heights: list[dict],
    tolerance_m: float = CONSISTENCY_TOLERANCE_M,
) -> dict:
    """标高侧与层高侧交叉校验 → {consistent, conflicts, checked}。

    对每层比较「标高差分算出的层高」与「平面标注的层高」,超容差即冲突。
    冲突定位到具体楼层——**这正是数据错误所在**,比笼统怀疑更有价值。
    """
    derived = {h["story_key"]: h["height_m"] for h in heights_from_elevations(elevations)}
    given = {h.get("story_key"): h.get("height_m") for h in heights or []}
    conflicts: list[dict] = []
    checked = 0
    for key, d_height in derived.items():
        g_height = given.get(key)
        if d_height is None or g_height is None:
            continue
        checked += 1
        diff = abs(float(d_height) - float(g_height))
        if diff > tolerance_m:
            conflicts.append({
                "story_key": key,
                "from_elevations": d_height,
                "from_heights": float(g_height),
                "diff_m": round(diff, 3),
            })
    return {"consistent": not conflicts, "conflicts": conflicts, "checked": checked}


def unreasonable_heights(levels: list[dict]) -> list[dict]:
    """标高序列中层高越出工程区间的楼层(标高识别有误的强信号)。"""
    return [
        {"story_key": h["story_key"], "height_m": h["height_m"]}
        for h in heights_from_elevations(levels)
        if h["reasonable"] is False
    ]
