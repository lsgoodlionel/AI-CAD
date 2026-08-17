"""比例的第三道闸：**同一工程的轴距应当同一量级**。纯函数。

前两道闸只防「离谱」：`is_scale_plausible` 卡分母 1~5000、
`MAX_DRAWING_EXTENT_M` 卡图幅换算不超 3 公里。
它们防不住**看似合理却彼此不一致**的比例 —— 而实测这才是大头：

| 偏离共识 | 图数 | 占比 |
|---|---:|---:|
| 正常（<20%） | 334 | 45.4% |
| 偏小 20~50% | 80 | 10.9% |
| **偏小 >50%** | **216** | **29.3%** |
| 偏大 20~100% | 69 | 9.4% |
| 偏大 >100% | 37 | 5.0% |

**只有 45% 的图比例是对的**（上海大歌剧院 736 张有轴距的图）。
用户看到的「模型分成好几块、轴线对不上」正是这么来的：
同一栋楼的「轴 3 → 轴 12」在三层算出 126.9 / 91.5 / **22.7** 米，
差 5.6 倍，而真实建筑里这个距离是固定的。

**判据来自工程事实而非参数区间**：共识 8.01 米是 736 张图自己算出来的。
"""
from __future__ import annotations

from statistics import median

#: 算共识至少要几张图 —— 少于此不足以代表「这个工程」。
MIN_CONSENSUS_SAMPLES = 2

#: 偏离共识多少才动它。
#:
#: 放到 ±50%：真实建筑本就有不同柱网（主楼 8 米、车库 8.4 米、局部 5 米都常见），
#: 只有**成倍**的偏差才是比例错误。宁可漏掉一些轻度错误，
#: 也不要把正常的小柱网区域「修正」坏。
GAP_TOLERANCE = 0.5


def consensus_gap(gap_medians: list[float] | None) -> float | None:
    """全项目的共识轴距（米）—— 取**中位数**，少数离谱图带不偏它。

    样本不足时返回 None（**判不出就说判不出**），调用方据此不做修正。
    """
    values = [float(g) for g in (gap_medians or []) if g and float(g) > 0]
    if len(values) < MIN_CONSENSUS_SAMPLES:
        return None
    return float(median(values))


def correct_scale_by_consensus(
    scale: float, gap_m: float | None, consensus_m: float | None,
    tolerance: float = GAP_TOLERANCE,
) -> float:
    """按轴距共识校正比例；不可信或无从判断时**原样返回**。

    轴距是比例换算出来的（`gap_m = gap_pt × scale`），所以本图轴距偏小
    k 倍，就意味着比例偏小 k 倍 —— 乘回去即可还原。

    **自验证**：修正后的比例必须落在 §6.0.4 的标准值上。
    真实图纸的比例只能是规范表里的某一个；推出来的若不是，
    说明这张图不适用共识（可能真是小柱网详图），**宁可不改**。
    """
    from services.drawing_transform import (
        is_standard_scale, snap_scale_to_standard,
    )

    if not scale or scale <= 0 or not gap_m or not consensus_m:
        return scale
    if gap_m <= 0 or consensus_m <= 0:
        return scale

    ratio = gap_m / consensus_m
    if (1 - tolerance) <= ratio <= (1 + tolerance):
        return scale

    corrected = scale * (consensus_m / gap_m)
    snapped = snap_scale_to_standard(corrected)
    # 落不到标准比例上 ⇒ 推断不可信，保持原值
    return snapped if is_standard_scale(snapped) else scale
