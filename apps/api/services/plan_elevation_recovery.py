"""方向1:从**平面图标注**恢复楼层真实标高(补剖面覆盖不足)。纯函数。

**问题(大歌剧院实测)**:全项目 2309 图仅 **24 张剖面**(main 22 / south 2 / **north 0**),
north 单体永远拿不到剖面标高 → 层高全部默认套(4.2/4.5),竖向真实率 0%。
但**平面图本身标注楼面标高**:989 张平面图带 28265 条标高档案条目。

**难点**:平面图标高混杂——楼面标高(-2.700/±0.000)与局部标高(窗顶、板变、防爆墙)
混在一起,直接取均值/最值会被噪声带偏。

**方案**:每层**众数投票**(多张图共同认可的标高才可信)+ **楼层单调性约束**
(标高须随楼层序单调递增,违反者剔除)+ 支持度门槛(≥min_support 张图)。
产出带 provenance 的层标高,z_source="plan_annotation"(源自图纸标注,属真实)。
"""
from __future__ import annotations

from collections import Counter

#: 标高取整精度(米):图纸标高以 mm 计,聚合时按 10mm 归并抵消 OCR 抖动
ROUND_M = 0.01
#: 某标高值需被多少张图共同标注才可信(防单图噪声)
MIN_SUPPORT = 2
#: 合理楼层标高范围(米):超出视为噪声(如构件局部标高/尺寸误识)
MIN_ELEVATION_M = -60.0
MAX_ELEVATION_M = 300.0


def _candidates_per_drawing(values: list[float]) -> set[float]:
    """一张图的标高候选去重(同图重复标注只算一票,防单图刷票)。"""
    return {round(round(v / ROUND_M) * ROUND_M, 2) for v in values
            if v is not None and MIN_ELEVATION_M <= v <= MAX_ELEVATION_M}


def vote_floor_elevation(
    elevations_by_drawing: dict[str, list[float]], min_support: int = MIN_SUPPORT,
) -> tuple[float | None, int]:
    """一层的多张图标高候选 → (众数标高, 支持图纸数)。

    每张图一票(同图重复不加权),取得票最多者;票数 < min_support → (None, 0)。
    平票取更小值(楼面标高通常低于局部构件标高如窗顶/女儿墙)。
    """
    tally: Counter = Counter()
    for values in elevations_by_drawing.values():
        for cand in _candidates_per_drawing(values or []):
            tally[cand] += 1
    if not tally:
        return None, 0
    best_votes = max(tally.values())
    if best_votes < min_support:
        return None, 0
    winners = [v for v, n in tally.items() if n == best_votes]
    return min(winners), best_votes


def enforce_monotonic(
    levels: list[tuple[str, int, float | None, int]],
) -> dict[str, tuple[float, int]]:
    """楼层单调性约束:标高须随楼层序(order)递增,违反者剔除。

    输入 [(floor_key, order, elevation|None, support)];按 order 升序贪心保留
    单调递增序列(冲突时保留**支持度更高**者),返回 {floor_key: (elevation, support)}。
    这是工程守恒律:上层标高必然高于下层,能剔掉投票选出的局部标高噪声。
    """
    ordered = sorted(
        [lv for lv in levels if lv[2] is not None], key=lambda lv: lv[1]
    )
    kept: list[tuple[str, int, float, int]] = []
    for key, order, elev, support in ordered:
        while kept and elev <= kept[-1][2]:
            # 与已保留的上一层冲突:支持度低者出局
            if support > kept[-1][3]:
                kept.pop()
            else:
                break
        if not kept or elev > kept[-1][2]:
            kept.append((key, order, elev, support))
    return {k: (e, s) for k, _, e, s in kept}


def vote_with_discriminative_weight(
    per_floor: dict[str, dict], min_support: int = MIN_SUPPORT,
) -> dict[str, tuple[float | None, int]]:
    """**层间区分度加权投票**(TF-IDF 思想):某标高只在该层高频才是该层特征标高。

    **为什么必须加权**(实测):`±0.000` 作为基准标高几乎每张图都标 → 纯众数下**每层
    众数都是 0.0**,单调性约束把所有层全剔除,恢复率为 0。加权后跨层普遍出现的基准值
    被压制,只在本层高频的值(如 B1 的 -4.200)胜出。

    得分 = 该层票数 / 出现该值的层数(IDF)。返回 {floor_key: (elevation|None, support)}。
    """
    # 每层的候选票数
    tally_by_floor: dict[str, Counter] = {}
    for fkey, info in (per_floor or {}).items():
        tally: Counter = Counter()
        for values in (info.get("drawings") or {}).values():
            for cand in _candidates_per_drawing(values or []):
                tally[cand] += 1
        tally_by_floor[fkey] = tally
    # 每个标高值出现在多少层(文档频次)
    floors_per_value: Counter = Counter()
    for tally in tally_by_floor.values():
        for cand in tally:
            floors_per_value[cand] += 1

    out: dict[str, tuple[float | None, int]] = {}
    for fkey, tally in tally_by_floor.items():
        best_value: float | None = None
        best_score = 0.0
        best_support = 0
        for cand, votes in tally.items():
            if votes < min_support:
                continue
            score = votes / floors_per_value[cand]      # IDF 加权
            if score > best_score or (score == best_score and
                                      best_value is not None and cand < best_value):
                best_score, best_value, best_support = score, cand, votes
        out[fkey] = (best_value, best_support)
    return out


#: 合理层高范围(米):相邻层标高差超出此范围 → 恢复值可疑
MIN_STORY_HEIGHT_M = 2.5
MAX_STORY_HEIGHT_M = 9.0


def grade_candidates(
    candidates: dict[str, dict], baseline: dict[str, float] | None = None,
    orders: dict[str, int] | None = None,
) -> dict[str, dict]:
    """给恢复候选打质量分,标注是否可直接采信(**默认不可**,须人审)。

    **诚实的必要性(实测)**:区分度加权虽把覆盖从 1/12 提到 9/12,但 IDF 会**误压制
    恰好是正确答案的普遍值**——实测 F1 恢复出 0.92,而首层楼面标高应为 ±0.000。
    故恢复值一律作**候选**,由以下信号定级,交人审裁决:

    - `deviation_m`:与现有基线(默认套)的差;
    - `story_height_ok`:与相邻层构成的层高是否在 2.5~9.0m 合理区间;
    - `confidence`:support 与偏差综合(仅供排序,不作自动采信依据)。
    """
    orders = orders or {}
    ordered_keys = sorted(candidates, key=lambda k: orders.get(k, 0))
    graded: dict[str, dict] = {}
    for idx, fkey in enumerate(ordered_keys):
        cand = dict(candidates[fkey])
        elev = cand["elevation_m"]
        base = (baseline or {}).get(fkey)
        cand["deviation_m"] = round(elev - base, 3) if base is not None else None
        height_ok = None
        if idx + 1 < len(ordered_keys):
            nxt = candidates[ordered_keys[idx + 1]]["elevation_m"]
            height = nxt - elev
            height_ok = MIN_STORY_HEIGHT_M <= height <= MAX_STORY_HEIGHT_M
        cand["story_height_ok"] = height_ok
        support = int(cand.get("support") or 0)
        score = min(support / 20.0, 1.0)
        if height_ok is False:
            score *= 0.4
        if cand["deviation_m"] is not None and abs(cand["deviation_m"]) > 2.0:
            score *= 0.5      # 与基线差异过大 → 降级(可能是局部标高误判)
        cand["confidence"] = round(score, 3)
        cand["needs_review"] = True     # **一律须人审**:精度不足以自动覆盖
        graded[fkey] = cand
    return graded


def recover_plan_elevations(
    per_floor: dict[str, dict],
    min_support: int = MIN_SUPPORT,
) -> dict[str, dict]:
    """楼层 → 平面图标注恢复的真实标高(区分度加权投票 + 单调性约束)。

    per_floor: {floor_key: {"order": int, "drawings": {drawing_id: [elevation_m,...]}}}
    返回 {floor_key: {"elevation_m", "support", "z_source": "plan_annotation"}}
    (仅含通过投票 + 单调性约束的层)。
    """
    weighted = vote_with_discriminative_weight(per_floor, min_support=min_support)
    voted = [
        (fkey, int((per_floor.get(fkey) or {}).get("order") or 0), elev, support)
        for fkey, (elev, support) in weighted.items()
    ]
    kept = enforce_monotonic(voted)
    return {
        fkey: {"elevation_m": elev, "support": support, "z_source": "plan_annotation"}
        for fkey, (elev, support) in kept.items()
    }
