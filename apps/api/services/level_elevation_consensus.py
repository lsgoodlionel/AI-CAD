"""标高的跨图共识 —— **孤证不立，多证可立**。纯函数。

链式配对（`level_elevation_pairing` 的 `chain_only=True`）是单图内的强证据，
但实测只覆盖 65/1578 张图（4.1%）—— 平面图上的标高是散点不是链。

**跨图共识是另一种强证据**：自由配对若有 ≥2 张图给同一
（单体, 楼层, 标高），图例区数字乱配不可能在多张图上撞出同一个值。
实测它能把标高覆盖从约 10 层扩到 30+ 组合。

**假冲突的教训**：实测 9 个「冲突」大多是单体混淆 ——
楼层名自己带着单体（「大歌剧厅3F」），而外部单体分类器返回 None，
不同单体的同名层撞在一起。所以先从楼层名抽单体再聚合；
剩下的**真冲突只报不选**（用户口径：矛盾时出矛盾点交人判断）。
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

#: 采纳一个自由配对至少要几张**不同的图**背书。
#: 同一张图重复条目不算 —— 那只是同一处标注被抽了两次。
MIN_WITNESS_DRAWINGS = 2

#: 楼层名里内嵌的单体（大歌剧院实测词表；带「厅」的场馆名）。
#: 楼层名写在图上，比外部分类器（按图号/图名猜）可靠。
_UNIT_IN_LEVEL_RE = re.compile(r"^(大歌剧厅|中歌剧厅|小歌剧厅|歌剧厅)")


def split_unit_from_level(level_name: str) -> tuple[str | None, str]:
    """「大歌剧厅3F」→ ("大歌剧厅", "3F")；无内嵌单体 → (None, 原名)。"""
    name = str(level_name or "").strip()
    matched = _UNIT_IN_LEVEL_RE.match(name)
    if not matched:
        return None, name
    unit = matched.group(1)
    rest = name[len(unit):].strip()
    return unit, rest or name


def _keyed(pairs: list[dict] | None):
    """(单体, 楼层) → {标高值 → 背书图集合}。楼层名内嵌单体优先于外部分类。"""
    votes: dict[tuple, dict[float, set]] = defaultdict(lambda: defaultdict(set))
    for pair in pairs or []:
        unit_in_name, level = split_unit_from_level(pair.get("level_name"))
        unit = unit_in_name or pair.get("building_unit_key")
        value = pair.get("elevation_m")
        did = pair.get("drawing_id")
        if value is None or not did:
            continue
        votes[(unit, level)][round(float(value), 3)].add(str(did))
    return votes


def consensus_overrides(pairs: list[dict] | None) -> list[dict[str, Any]]:
    """≥2 图背书且**无竞争值**的（单体,楼层,标高）→ 覆盖列表。

    同一楼层若有两个值都拿到 ≥2 票，是真冲突 —— 不选（见
    `consensus_conflicts`），选了就是替人做主。
    """
    out: list[dict[str, Any]] = []
    for (unit, level), values in _keyed(pairs).items():
        backed = {v: dids for v, dids in values.items()
                  if len(dids) >= MIN_WITNESS_DRAWINGS}
        if len(backed) != 1:
            continue
        value, dids = next(iter(backed.items()))
        out.append({
            "building_unit_key": unit,
            "level_name": level,
            "elevation_m": value,
            "witnesses": len(dids),
            "drawing_ids": sorted(dids),
            "source": "cross_drawing_consensus",
        })
    return sorted(out, key=lambda o: (str(o["building_unit_key"]),
                                      str(o["level_name"])))


def consensus_conflicts(pairs: list[dict] | None) -> list[dict[str, Any]]:
    """同一（单体,楼层）有多个 ≥2 票的值 —— 出矛盾点，交人判断。"""
    out: list[dict[str, Any]] = []
    for (unit, level), values in _keyed(pairs).items():
        backed = [(v, len(dids)) for v, dids in values.items()
                  if len(dids) >= MIN_WITNESS_DRAWINGS]
        if len(backed) > 1:
            out.append({
                "building_unit_key": unit,
                "level_name": level,
                "values": sorted(backed, key=lambda x: -x[1]),
                "explanation": (
                    f"「{level}」有 {len(backed)} 个互斥标高，且各有 ≥2 张图背书"
                    " —— 可能是单体未分开（同名层属不同楼），或图纸版次不一致。"
                    "请人工核对；系统不替人选。"),
            })
    return out
