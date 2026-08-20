"""建模的比例门禁：低置信变换不当权威，离群图不污染场景包络。

**为什么需要**（轨道交通工程 v1 实测，用户反馈「完全失真、积压在一起」）：

| | 全场景包络 | 中间 90% 的点 | 最大单图跨度 |
|---|---|---|---|
| 轨道交通 | **4862 × 3701 m** | 762 × 534 m | **4176.7 m** |
| 大歌剧院 | 589 × 765 m | 214 × 137 m | 574.1 m |

场景被 2 张离群图撑到 **4.8 公里**，真实内容只有 760 米，
建筑于是缩成中间一小团。**「积压在一起」不是渲染问题，
是包络被离群值撑爆。**

根因在上游：`drawing_transform` 里 633 张来自 `geometry`（图幅推断）的
比例跨越三个数量级（0.001~1.707 m/pt）、**平均置信 0.02**，
却被无条件当作权威交给识别器；而来自 `axes`（轴号识别）的 80 张
中位正好是 1:100、平均置信 0.97。

两道防线：**门禁**挡住不可信的比例（预防），**离群剔除**保证
少数漏网的不至于毁掉整个场景（兜底）。
"""
from __future__ import annotations

from statistics import median

#: 排版点 → 毫米。
PT_TO_MM = 25.4 / 72.0

#: 建筑图纸比例分母的合理区间。1:20 到 1:2000 覆盖从节点详图到总平面图；
#: 实测的 1.707 m/pt ≈ 1:4838、0.00086 m/pt ≈ 1:2.4 都在此之外。
MIN_SCALE_DENOMINATOR = 20.0
MAX_SCALE_DENOMINATOR = 2000.0

#: 变换可信所需的最低置信度。**实测 607/633 张 `geometry` 变换置信 < 0.1，
#: 而 75/80 张 `axes` 变换置信 ≥ 0.8** —— 0.5 把两者干净分开。
MIN_TRANSFORM_CONFIDENCE = 0.5

#: 单图跨度相对项目中位数的上限。判据来自实测：**效果尚可的大歌剧院
#: 最大只有中位的 7.51 倍**，而轨道交通有 39.8 倍。取 8 对前者零影响。
MAX_SPAN_RATIO = 8.0

#: 参与离群判定所需的最少图纸数。样本太少时中位数没有意义——
#: 宁可不判，也不要凭两张图断定谁离群。
MIN_SOURCES_FOR_OUTLIER = 4


def scale_denominator(scale_m_pt: float) -> float:
    """`scale_m_pt` → 图纸比例分母（1:N 里的 N）。"""
    return float(scale_m_pt) * 1000.0 / PT_TO_MM


def is_transform_trustworthy(scale_m_pt: float | None,
                             confidence: float | None) -> bool:
    """这张图的坐标变换能不能当权威交给识别器。

    两个条件都要满足：
    - **比例落在建筑图纸的合理区间**——置信衡量的是「识别质量」，
      不是「比例对不对」，所以再高的置信也救不了一个荒谬的比例；
    - **置信度达标**——置信为空时**不能当满分**，那正是
      「没人评估过」的意思。

    不可信时不是报错，而是**不覆盖**：识别器回退到按图纸自身内容估，
    至少不会被钉死在错误尺度上。
    """
    if scale_m_pt is None or confidence is None:
        return False
    try:
        denominator = scale_denominator(float(scale_m_pt))
        conf = float(confidence)
    except (TypeError, ValueError):
        return False
    if not (MIN_SCALE_DENOMINATOR <= denominator <= MAX_SCALE_DENOMINATOR):
        return False
    return conf >= MIN_TRANSFORM_CONFIDENCE


def outlier_sources(spans_by_source: dict | None) -> set:
    """跨度相对中位数过大的图纸 id 集合。

    跨度为 0 或负的图**不算尺度离群**（实测有 3 张）——那是另一类问题
    （识别没产出有效轮廓），混进来会让「离群」这个词失去含义。
    """
    spans = {key: float(value) for key, value in (spans_by_source or {}).items()
             if key is not None and float(value or 0.0) > 0.0}
    if len(spans) < MIN_SOURCES_FOR_OUTLIER:
        return set()
    reference = median(spans.values())
    if reference <= 0:
        return set()
    limit = reference * MAX_SPAN_RATIO
    return {key for key, value in spans.items() if value > limit}
