"""圈内字形判据:识别附加轴线的分数式标签(GB/T 50001 §8.0.6)。

**要解决的问题**:附加轴线混在主序列里会让其后的轴号**整体偏移**。实测分区 2
字母向检出 18 条(真值 14),4 条附加轴线夹在中间,从第 8 条起 `2-H` 被标成 `2-J`。

**为什么不做全字符识别**:圈内是发丝笔画字形,OCR 在 8 种配置(300/600/900dpi ×
含圈/圈内 × 形态学加粗 0/5)下最好只有 1/24。但要消除偏移**只需回答一个是非题**:
这个标签里有没有 `/`。

**为什么不用字符个数**:主轴号 3 字符、附加轴号 5~6 字符,看似可分;实测在
A-01-02A 上只找到 6/8——漏的两个**字形在 x 上互相接触**,被并成一簇
(单簇宽 16.1pt),字符数退化为 2。而它们的 `/` 笔画长度完全正常。

**可用的判据是 `/` 笔画本身的长度**。实测「最长陡斜笔画 ÷ 圈径」:

    A-01-02A   0.42 × 6(字母 A/M 的斜画)   **0.47~0.48 × 8**(`/`)
    A-01-03A   0.42 × 5                     无 ≥0.44(同心圆轴网,无分数式)
    A-01-04A   0.42 × 5                     **0.47~0.48 × 6**

**0.43~0.46 这一区间在三张图上全空**——分界是实测出来的空白带,不是拍的阈值。

**曾踩的坑**:一开始把陡斜窗口取成 50~85°,于是 K(约 45°)与 N(约 87°)落在
窗外测出 0.063,看起来「主轴线没有斜画」,分界显得很干净——那其实是**判据没测到**,
不是图上没有。窗口放宽到 40~88° 后,字母斜画真实地出现在 0.42,分界依然成立,
这时才算真的验证过。
"""
from __future__ import annotations

import math

#: 取圈内 80% 半径,避开圆周本身与外接的短划
GLYPH_INSET_RATIO = 0.80

#: 「陡斜」的角度窗(度)。必须宽到覆盖字母 A/K/M/N 的斜画,
#: 否则分界只是角度窗的巧合而非真实差异
STEEP_MIN_DEG = 40.0
STEEP_MAX_DEG = 88.0

#: 判为分数式的「最长陡斜笔画 ÷ 圈径」下限。落在实测空白带 0.43~0.46 内
FRACTION_RATIO_THRESHOLD = 0.44


def strokes_inside(strokes: list[tuple], circle: dict,
                   inset: float = GLYPH_INSET_RATIO) -> list[tuple]:
    """圈内笔画(两端都在 inset 半径内)。"""
    radius = circle["diameter_pt"] / 2 * inset
    cx, cy = circle["cx"], circle["cy"]
    return [s for s in strokes
            if math.dist((s[0], s[1]), (cx, cy)) <= radius
            and math.dist((s[2], s[3]), (cx, cy)) <= radius]


def longest_steep_ratio(strokes: list[tuple], diameter_pt: float) -> float:
    """最长陡斜笔画的长度 ÷ 圈径。按比例算,才能同时适配 28pt 与 16pt 的圈。"""
    if diameter_pt <= 0:
        return 0.0
    longest = 0.0
    for s in strokes:
        angle = math.degrees(math.atan2(abs(s[3] - s[1]), abs(s[2] - s[0])))
        if STEEP_MIN_DEG <= angle <= STEEP_MAX_DEG:
            longest = max(longest, math.dist((s[0], s[1]), (s[2], s[3])))
    return round(longest / diameter_pt, 4)


def has_fraction_label(strokes: list[tuple], circle: dict,
                       threshold: float = FRACTION_RATIO_THRESHOLD) -> bool:
    """该圈内是否为 §8.0.6 的分数式轴号(即这是一条附加轴线)。

    入参 strokes 可以是整页笔画——内部会先筛出落在圈内的。
    """
    inside = strokes_inside(strokes, circle)
    return longest_steep_ratio(inside, circle["diameter_pt"]) >= threshold


def mark_fraction_circles(strokes: list[tuple], circles: list[dict],
                          threshold: float = FRACTION_RATIO_THRESHOLD) -> list[dict]:
    """给每个圈打上 `is_additional` 标记(不改入参)。

    附加轴线**不参与主序列编号**——否则其后的轴号会整体偏移。
    """
    return [{**c, "is_additional": has_fraction_label(strokes, c, threshold)}
            for c in circles]


def strokes_from_pdf(pdf_bytes: bytes) -> list[tuple]:
    """PDF 首页 → 线段(贝塞尔按端点近似)。无矢量数据时降级为空。"""
    try:
        import fitz
    except ImportError:                                    # pragma: no cover
        return []
    try:
        page = fitz.open(stream=pdf_bytes, filetype="pdf")[0]
    except Exception:                                      # pragma: no cover
        return []
    out: list[tuple] = []
    for path in page.get_drawings():
        for item in path["items"]:
            if item[0] == "l":
                out.append((item[1].x, item[1].y, item[2].x, item[2].y))
            elif item[0] == "c":
                out.append((item[1].x, item[1].y, item[4].x, item[4].y))
    return out
