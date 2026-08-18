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

def split_unit_from_level(level_name: str) -> tuple[str | None, str]:
    """「大歌剧厅3F」→ ("大歌剧厅", "3F")；无内嵌单体 → (None, 原名)。

    委托给通用的 `split_level_prefix`（**零后缀词表**，通用性审计修复）：
    「××厅」是剧院命名、「A栋」是住宅的 —— 任何后缀词表只覆盖一类工程。
    通用的是结构：楼层名 = [子单体前缀] + 标准层 token。
    """
    from services.sub_unit_discovery import split_level_prefix

    return split_level_prefix(level_name)


def _keyed(pairs: list[dict] | None):
    """(单体, 楼层) → {标高值 → 背书图集合}。楼层名内嵌单体优先于外部分类。

    同时记录每个键下见证图的**外部分类单体票**（`classifier_votes`）——
    厅名单体（如「小歌剧厅」）在楼层表里不存在时，
    见证图的分类一致票就是「厅 → 区」的映射，从数据学出，不硬编码词表。
    """
    votes: dict[tuple, dict[float, set]] = defaultdict(lambda: defaultdict(set))
    classifier_votes: dict[tuple, list] = defaultdict(list)
    for pair in pairs or []:
        unit_in_name, level = split_unit_from_level(pair.get("level_name"))
        unit = unit_in_name or pair.get("building_unit_key")
        value = pair.get("elevation_m")
        did = pair.get("drawing_id")
        if value is None or not did:
            continue
        votes[(unit, level)][round(float(value), 3)].add(str(did))
        external = pair.get("building_unit_key")
        if external is not None:
            classifier_votes[(unit, level)].append(external)
    return votes, classifier_votes


def _unanimous(items: list) -> Any | None:
    """全体一致才采纳 —— 分歧时**判不出就说判不出**，不按票数赌。"""
    unique = set(items)
    return next(iter(unique)) if len(unique) == 1 and items else None


def consensus_overrides(pairs: list[dict] | None) -> list[dict[str, Any]]:
    """≥2 图背书且**无竞争值**的（单体,楼层,标高）→ 覆盖列表。

    同一楼层若有两个值都拿到 ≥2 票，是真冲突 —— 不选（见
    `consensus_conflicts`），选了就是替人做主。
    """
    out: list[dict[str, Any]] = []
    votes, classifier_votes = _keyed(pairs)
    for (unit, level), values in votes.items():
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
            # 厅名单体在楼层表里不存在时的降级目标：见证图分类的一致票
            "fallback_unit": _unanimous(classifier_votes.get((unit, level), [])),
            "source": "cross_drawing_consensus",
        })
    return sorted(out, key=lambda o: (str(o["building_unit_key"]),
                                      str(o["level_name"])))


def consensus_conflicts(pairs: list[dict] | None) -> list[dict[str, Any]]:
    """同一（单体,楼层）有多个 ≥2 票的值 —— 出矛盾点，交人判断。"""
    out: list[dict[str, Any]] = []
    votes, _cls = _keyed(pairs)
    for (unit, level), values in votes.items():
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


def consensus_to_pairs(items: list[dict] | None) -> list[dict[str, Any]]:
    """共识项 → 配对列表，**每张见证图一条**。

    实测断点：共识补 19 层、`build_z_overrides` 产出仍 10 层 ——
    它的 `MIN_SAMPLES=2` 把每条共识项当 1 个样本杀掉，
    「孤证不立」被重复计了两次。N 张见证图就是 N 个独立样本，
    按图展开是**如实表示**，不是权重技巧。
    """
    out: list[dict[str, Any]] = []
    for item in items or []:
        for _did in item.get("drawing_ids") or []:
            out.append({
                "level_name": item["level_name"],
                "elevation_m": item["elevation_m"],
                "building_unit_key": item.get("building_unit_key"),
            })
    return out


def learn_unit_aliases(
    aliases: set[str], titled_units: list[tuple[str, str | None]],
    ignore_units: set[str] | frozenset = frozenset(),
) -> dict[str, str]:
    """从图名共现学「别名 → 楼层表单体」映射。零硬编码词表。

    **项目图纸自己写着答案**：图名「南区（大、中歌剧厅）…」把厅与区
    写在一起（实测共现零歧义：大/中→南区、小→北区）。
    对每个别名，收集**图名含该别名**的图的分类器单体；
    一致才学，出现分歧就不学（判不出就说判不出）。
    """
    votes: dict[str, set[str]] = {a: set() for a in aliases or ()}
    for title, unit in titled_units or []:
        # **默认兜底值没有否决权**：`DEFAULT_UNIT_KEY` 是「没匹配上」时
        # 给的默认，不是真实判定 —— 实测「中歌剧厅」的票是
        # south 125 / main 5 / None 14，5 张默认噪声否掉了 125 张共识。
        if not unit or unit in ignore_units:
            continue
        text = str(title or "")
        for alias in votes:
            if alias in text:
                votes[alias].add(str(unit))
    unanimous = {alias: next(iter(units))
                 for alias, units in votes.items() if len(units) == 1}
    # **目标被多个别名共享 ⇒ 歧义,一个都不映**。实测大、中歌剧厅都映到
    # south,而两厅同名层标高不同(F4: 16.1 vs 14.5),挤进同一单体互相
    # 打架,把链式原本能出的键也炸掉(合并后 10 层反而变 8 层)。
    # 楼层表的粒度装不下两个厅,硬塞就是赌 —— 独占目标才安全。
    target_count: dict[str, int] = {}
    for target in unanimous.values():
        target_count[target] = target_count.get(target, 0) + 1
    return {alias: target for alias, target in unanimous.items()
            if target_count[target] == 1}
