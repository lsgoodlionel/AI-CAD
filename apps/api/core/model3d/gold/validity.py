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
