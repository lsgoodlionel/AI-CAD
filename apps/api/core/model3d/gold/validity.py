"""一批判读结果的有效性与汇总 —— 阈值事先声明，不可调。

阈值一旦可调，就会被调到让当前这批通过（`judge_sanity` 模块文档的同一条
纪律）。所以这里的数都是写死的常量，改它们要改代码、留提交记录。
"""
from __future__ import annotations

#: 空白对照至少这么多格才能证明任何事 —— 2 格全对也可能是运气。
MIN_BLANK_CELLS = 6

#: 空白对照允许失手的格数。此前三批分别是 12/12、12/12、10/10 全对，
#: 允许一格是给「红线刚好压到一点墨迹」留的余地，不是给判读者放水。
MAX_BLANK_MISSES = 1


def blank_control_ok(*, n_blank: int, n_blank_judged_negative: int) -> tuple[bool, str]:
    """空白对照是否通过：判读者对「红线压在空白处」是否答了「不是」。

    不通过就整批作废 —— 判读者连空白都答成构件，其余格子的答案没有意义。
    """
    if n_blank < MIN_BLANK_CELLS:
        return False, f"空白对照只有 {n_blank} 格，少于 {MIN_BLANK_CELLS}，证明不了仪器有效"
    misses = n_blank - n_blank_judged_negative
    if misses > MAX_BLANK_MISSES:
        return False, (f"空白对照 {n_blank_judged_negative}/{n_blank}，失手 {misses} 格"
                       f"（上限 {MAX_BLANK_MISSES}）—— 仪器无效，整批作废")
    return True, f"空白对照 {n_blank_judged_negative}/{n_blank} 通过"


def pair_agreement(answers: dict[str, bool], pairs: list[tuple[str, str]]) -> float | None:
    """批内重测对的一致率。只算两份都答了的对 —— 漏答不等于不一致。

    没有一对是完整的就返回 None：没有数据时不报数。
    """
    complete = [(a, b) for a, b in pairs if a in answers and b in answers]
    if not complete:
        return None
    agree = sum(1 for a, b in complete if answers[a] == answers[b])
    return agree / len(complete)


def weighted_precision(
    per_stratum: dict[str, tuple[int, int]],
    weights: dict[str, float],
) -> tuple[float | None, float]:
    """按语料分量加权的精确率，以及它覆盖了多少语料。

    ``per_stratum``：层 → (判对格数, 判读格数)；``weights``：层 → 该层在语料里的分量。

    分层抽样把稀有层放大了（教训②），直接数「对的格子 / 全部格子」会让
    稀有层的精确率被高估成全局的。加权把每层压回它在语料里的真实分量。

    **没抽到的层不被当成「和别的层一样」**：它们不进分子也不进分母，
    只体现在覆盖率里。返回 (精确率, 覆盖率)；一层都没覆盖时精确率为 None。
    """
    total_w = sum(w for w in weights.values() if w > 0)
    if total_w <= 0:
        return None, 0.0
    covered = {s: w for s, w in weights.items()
               if w > 0 and per_stratum.get(s, (0, 0))[1] > 0}
    covered_w = sum(covered.values())
    coverage = covered_w / total_w
    if covered_w <= 0:
        return None, 0.0
    p = sum(w * per_stratum[s][0] / per_stratum[s][1] for s, w in covered.items()) / covered_w
    return p, coverage


#: 正对照至少这么多格。与空白对照同一量级 —— 少于这个数，
#: 「判读者一律答不是」与「这几格碰巧真不是」分不开。
MIN_POSITIVE_CELLS = 6

#: 正对照允许失手的格数。留一格给「这一格确实边界模糊」，不是给判读者放水。
MAX_POSITIVE_MISSES = 1


def positive_control_ok(*, n_pos: int, n_pos_judged_positive: int) -> tuple[bool, str]:
    """正对照是否通过：判读者对「确实是该构件」的格子是否答了「是」。

    **空白对照只能抓一个方向的失效。** col3（柱，2026-09-15）空白对照 8/8、
    重测一致率 1.0、编号零编造，三项全过，却把轴线交点上带剖面填充的近方块
    判成「细线区域·不是」—— 一律答「不是」的判读者能轻松通过空白对照。
    同一批图纸、同一份代码、同一判据，col3 判出 1/13、col4 判出 12/21，
    两者差九倍而仪器毫无反应。正对照就是补上另一个方向。

    对照格来自**已双人核验**的历史格子（判读者判真 + 本人逐格看图复核），
    出处记在 `data/model3d/gold/positives/*.tsv`。
    """
    if n_pos < MIN_POSITIVE_CELLS:
        return False, f"正对照只有 {n_pos} 格，少于 {MIN_POSITIVE_CELLS}，证明不了仪器有效"
    misses = n_pos - n_pos_judged_positive
    if misses > MAX_POSITIVE_MISSES:
        return False, (f"正对照 {n_pos_judged_positive}/{n_pos}，失手 {misses} 格"
                       f"（上限 {MAX_POSITIVE_MISSES}）—— 判读者系统性少判，整批作废")
    return True, f"正对照 {n_pos_judged_positive}/{n_pos} 通过"


#: 偏移对照至少这么多格。与空白对照同一量级。
MIN_FOIL_CELLS = 6

#: 偏移对照允许失手的格数（判读者把「挪开了的标记」仍答成对）。
MAX_FOIL_MISSES = 1


def foil_control_ok(*, n_foil: int, n_foil_judged_negative: int) -> tuple[bool, str]:
    """偏移对照：把标记**故意挪开一段**，判读者该答「不对」。

    **空白对照抓不住「一律答是」里最要命的那一种。** 红十字打在白纸上，
    任何人都答得出「不是」；难的是打在图面上、周围确实有线、但**不在**
    系统声称的那个位置 —— 实测锚点批 18/18 全判为对，而判据允许
    「看不到轴号时只判位置」，这种答法与「一律答是」在结果上分不开。

    偏移对照就是把这一档补上：同一张图、同一类标记，位置挪开一个明确的
    距离。答「对」= 判读者没在看位置。它比空白对照贵（要构造），
    但只有它能证伪「系统说在哪就在哪」。
    """
    if n_foil < MIN_FOIL_CELLS:
        return False, f"偏移对照只有 {n_foil} 格，少于 {MIN_FOIL_CELLS}，证明不了仪器有效"
    misses = n_foil - n_foil_judged_negative
    if misses > MAX_FOIL_MISSES:
        return False, (f"偏移对照 {n_foil_judged_negative}/{n_foil}，失手 {misses} 格"
                       f"（上限 {MAX_FOIL_MISSES}）—— 判读者没在看位置，整批作废")
    return True, f"偏移对照 {n_foil_judged_negative}/{n_foil} 通过"
