"""选锚图与荐锚图 —— 判据同源的两件事（J1 收尾 + J1-3）。

**选锚**：交点传播从哪张图出发。**不得硬编码图号**
（`docs/MODELING_PIPELINE_BLUEPRINT.md` §7 约束 1），判据只能来自内容：
有没有真世界锚点、变换残差多大。

**荐锚**：告诉人「该确认哪几张图最划算」。实测未匹配原因中
「对不上任何锚」占 **91%**、歧义仅 1% ⇒ 瓶颈是锚覆盖不足；
而人工确认一次的成本固定，所以该**优先确认覆盖最广的图**。

两者共用「覆盖力」度量：序列越长、方向越全、分区越多，
能作为其子序列匹配上的局部图就越多。
"""
from __future__ import annotations

from typing import Any, Sequence

#: 解相似变换至少要 2 个点。
MIN_ANCHOR_POINTS = 2

#: 覆盖力权重。**方向数权重最高** —— 交点要 x、y 两个轴号才能构成，
#: 实测 143 张匹配成功的图里 131 张是单向的，一个交点也产不出。
_WEIGHT_DIRECTIONS = 1000

#: 分区数超过它即判为**符号场误检**，整张图不推荐。
#:
#: 第一版把分区数当加分项，实测推荐前 5 名全是给排水/喷淋抗震支架图，
#: 报「11 个分区」「15 个分区」—— 而大歌剧院真值只有 **3 个**。
#: 分区多是设备符号被当成轴号圈的特征，不是覆盖广。
#: GB/T 50001 §8.0.5 未规定分区数上限，工程实际极少超过个位数。
MAX_PLAUSIBLE_ZONES = 6

#: 最长组段数的**计分上限**。超过它不再加分。
#:
#: 为什么要封顶：排序里「越大越好」的量会被过检刷榜 —— 实测基坑支撑图
#: 报「最长一组 194 段」占据榜首，而真正的轴网定位图 A-01-02A 只有 23 段。
#: 查下来那 434 条轴线全部 `source=label_circle`，是图上的**圆形构件**
#: （立柱桩、钢立柱）被当成了轴号圈。
#:
#: 我先试过用「带轴号占比」区分，**无效**：轴号是 §8.0.3 推导出来的，
#: 系统给每条检出轴线都编号，占比恒为 1 —— 它衡量的是系统自己的产出，
#: 不是图纸事实。
#:
#: 40 段（41 条轴线）已是相当完整的轴网（实测真值 23 段），
#: 再多不提升作锚价值。**这是经验值**，不是国标规定。
MAX_SCORED_GAPS = 40


def pick_anchor_drawing(candidates: Sequence[dict] | None) -> str | None:
    """选传播锚图：**残差最小者优先**。

    锚图的变换会传给所有下游，错了全错，所以先看准不准、再看多不多。
    算不出残差（变换没解出来）的**不当锚** —— 判不出就说判不出。
    并列时按 ``drawing_id`` 定序，避免重建结果漂移。
    """
    usable = [
        c for c in (candidates or [])
        if int(c.get("anchor_points") or 0) >= MIN_ANCHOR_POINTS
        and c.get("rmse_m") is not None
    ]
    if not usable:
        return None
    best = min(usable, key=lambda c: (float(c["rmse_m"]), str(c["drawing_id"])))
    return str(best["drawing_id"])


def coverage_score(candidate: dict) -> int:
    """覆盖力 —— 这张图当锚能让多少局部图匹配上。

    **方向数 > 最长组段数**：
    - 双向是构成交点的**前提**，单向图确认了也拿不到世界坐标；
    - 匹配是**按组**(分区×方向×角度)做的，所以看**最长的那组**，
      不是各组总和 —— 11 个分区共 91 段、每组平均 4 段，什么也匹配不上。

    分区数**不加分**：它多不代表覆盖广，反而是符号场误检的特征。
    """
    longest = int(candidate.get("max_gaps") or 0)
    if not longest:                       # 老调用方没给 max_gaps 时退回总长
        longest = int(candidate.get("total_gaps") or 0)
    return (int(candidate.get("directions") or 0) * _WEIGHT_DIRECTIONS
            + min(longest, MAX_SCORED_GAPS))


def rank_anchor_candidates(
    candidates: Sequence[dict] | None, *, limit: int = 10,
) -> list[dict]:
    """按覆盖力排出「最值得人工确认的图」；已确认的不再推荐。

    每项带 ``reason``，说明为什么值得 —— 让人能判断值不值得花这一次确认，
    而不是照着一个不透明的分数点下去。
    """
    ranked = []
    for cand in candidates or []:
        if cand.get("zone_confirmed"):
            continue                       # 推荐列表是待办，不是排行榜
        if int(cand.get("zones") or 0) > MAX_PLAUSIBLE_ZONES:
            continue                       # 分区数远超工程常识 ⇒ 符号场误检
        score = coverage_score(cand)
        if score <= 0 or not int(cand.get("total_gaps") or 0):
            continue                       # 轴线太少，当不了锚
        directions = int(cand.get("directions") or 0)
        ranked.append({
            **cand,
            "coverage_score": score,
            "reason": (
                f"{directions} 个方向、{int(cand.get('zones') or 0)} 个分区、"
                f"最长一组 {int(cand.get('max_gaps') or cand.get('total_gaps') or 0)} 段"
                f"（共 {int(cand.get('total_gaps') or 0)} 段）"
                + ("" if directions >= 2 else "（**单向，构不成交点**）")
                + ("" if int(cand.get("max_gaps") or 0) <= MAX_SCORED_GAPS
                   else "（轴线数远超常见轴网，**可能是圆形构件被当成轴号圈**，需核对）")
            ),
        })
    ranked.sort(key=lambda c: (-c["coverage_score"], str(c["drawing_id"])))
    return ranked[:limit]
