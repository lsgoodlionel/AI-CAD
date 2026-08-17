"""轴距异常检测 —— 只**标记**，不自动修比例。纯函数。

**为什么不反推比例**（上一版这么做，把 F6 层跨度从 89 米压成 5 米，已回退）：

轴距是 `pt差 × 比例` 算出来的，异常时有两种可能而**数据上无法区分**：

| 可能 | 乘系数修正的后果 |
|---|---|
| 比例错了 | 修好 |
| **轴线检测噪声**（把两条紧邻的线当轴网） | **更离谱** |

实测证明后者是主因：修正倍数最大的几张，原轴距是 **0.11 / 0.12 / 0.28 米**
—— 11 厘米在图上就是两条挨着的线；另一端 177.91 米的「单跨」同样不可能。

所以本模块只做一件事：**把证据摆出来，让人分辨是哪一种**。
"""
from __future__ import annotations

from typing import Any

#: 工程上说得通的轴距区间（米）。
#:
#: 下限 2.0：紧凑柱网（车库、设备房）可到 3 米，留些余量；
#: 上限 30.0：大跨结构（剧院观众厅、体育馆）可到 20+ 米。
#: **落在区间外的不可能是柱网**，多半是轴线检测把非轴线当成了轴线。
GAP_SANE_RANGE_M = (2.0, 30.0)

#: 偏离共识多少才值得报（倍数）。真实建筑本就有不同柱网，
#: 只有成倍差异才说明有问题。
DEVIATION_FACTOR = 2.0


def detect_gap_anomaly(
    drawing_id: str, gap_m: float | None, consensus_m: float | None,
    samples: int = 0,
) -> dict[str, Any] | None:
    """该图轴距是否异常 → 证据（正常返回 None）。

    **不给「建议比例」**：反推不成立，给了就是诱导人接受错值。
    """
    if not gap_m or not consensus_m or gap_m <= 0 or consensus_m <= 0:
        return None

    ratio = gap_m / consensus_m
    if (1 / DEVIATION_FACTOR) <= ratio <= DEVIATION_FACTOR:
        return None

    low, high = GAP_SANE_RANGE_M
    if gap_m < low or gap_m > high:
        cause = ("轴线检测噪声——这个间距不可能是柱网"
                 "（多半把两条紧邻的线、或非轴线的构造线当成了轴网）")
    else:
        cause = ("比例尺可疑——轴距本身在工程合理区间内，"
                 "但与全项目共识差了一倍以上")

    return {
        "drawing_id": drawing_id,
        "gap_m": round(float(gap_m), 3),
        "consensus_m": round(float(consensus_m), 2),
        "ratio": round(ratio, 3),
        "samples": int(samples),
        "likely_cause": cause,
        "action": ("人工核对该图的轴网识别结果与图上标注的比例尺；"
                   "**系统不自动修正** —— 轴距异常的两种成因"
                   "（比例错 / 轴线误检）从数据上分不开，猜错会把构件坐标改坏。"),
    }


def summarize_gap_anomalies(items: list[dict | None] | None) -> dict[str, Any]:
    """汇总，供 scene.quality 与前端展示。"""
    valid = [i for i in (items or []) if i]
    by_cause: dict[str, int] = {}
    for item in valid:
        key = "轴线误检" if "噪声" in item["likely_cause"] else "比例可疑"
        by_cause[key] = by_cause.get(key, 0) + 1
    return {
        "count": len(valid),
        "by_cause": by_cause,
        "items": valid[:50],       # 明细截断，计数照实
    }
