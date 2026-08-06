"""圈内字形判据单测:识别附加轴线的分数式标签(§8.0.6)。

**为什么需要**:附加轴线混在主序列里会让其后的轴号整体偏移——实测分区 2
字母向检出 18 条(真值 14),从第 8 条起 `2-H` 被标成了 `2-J`。

**为什么不是全字符识别**:OCR 在这些发丝笔画上 8 种配置最好 1/24。
但要解决偏移只需回答一个是非题:**这个标签里有没有 `/`**。

**为什么不用字符个数**:主轴号 3 字符、附加轴号 5~6 字符,看似可分。
实测在 A-01-02A 上只找到 6/8 —— 漏的那两个字形在 x 上**互相接触**,
被并成一簇(单簇宽 16.1pt),字符数退化为 2。

**可用的判据是 `/` 笔画本身的长度**。实测「最长陡斜笔画 ÷ 圈径」:

    A-01-02A   0.42 × 6(字母 A/M 的斜画)   0.47~0.48 × 8(`/`)
    A-01-03A   0.42 × 5                     无 ≥0.44
    A-01-04A   0.42 × 5                     0.47~0.48 × 6

**0.43~0.46 区间三张图全空**,分界是实测出来的,不是拍的。
"""
import math

import pytest

from core.model3d.axis_label_glyph import (
    FRACTION_RATIO_THRESHOLD, STEEP_MAX_DEG, STEEP_MIN_DEG, has_fraction_label,
    longest_steep_ratio, strokes_inside,
)

CENTER = (1000.0, 500.0)
DIAMETER = 28.0


def _stroke(dx0, dy0, dx1, dy1):
    """相对圈心的一笔。"""
    return (CENTER[0] + dx0, CENTER[1] + dy0, CENTER[0] + dx1, CENTER[1] + dy1)


def _slash(ratio: float = 0.475):
    """一根 `/`:长度 = 圈径 × ratio,倾角 70°。"""
    length = DIAMETER * ratio
    rad = math.radians(70.0)
    dx, dy = math.cos(rad) * length / 2, math.sin(rad) * length / 2
    return _stroke(-dx, dy, dx, -dy)


# ── 圈内笔画提取 ──────────────────────────────────────────────

def test_keeps_only_strokes_fully_inside_the_circle():
    inside = _stroke(-3.0, -3.0, 3.0, 3.0)
    outside = _stroke(-3.0, -3.0, 60.0, 60.0)         # 一端在圈外
    got = strokes_inside([inside, outside], {"cx": CENTER[0], "cy": CENTER[1],
                                             "diameter_pt": DIAMETER})
    assert got == [inside]


def test_inset_excludes_the_circle_outline_itself():
    """取 80% 半径,避免把圆周与外接短划算进来。"""
    on_rim = _stroke(DIAMETER / 2 - 0.5, 0.0, DIAMETER / 2 - 0.5, 1.0)
    got = strokes_inside([on_rim], {"cx": CENTER[0], "cy": CENTER[1],
                                    "diameter_pt": DIAMETER})
    assert got == []


def test_strokes_inside_on_empty():
    assert strokes_inside([], {"cx": 0.0, "cy": 0.0, "diameter_pt": 28.0}) == []


# ── 陡斜笔画比 ────────────────────────────────────────────────

def test_slash_gives_the_measured_ratio():
    got = longest_steep_ratio([_slash(0.475)], DIAMETER)
    assert got == pytest.approx(0.475, abs=0.01)


def test_horizontal_and_vertical_strokes_are_ignored():
    """`-` 与 `1` 的竖画不是斜画,不能算进来。"""
    horizontal = _stroke(-6.0, 0.0, 6.0, 0.0)
    vertical = _stroke(0.0, -6.0, 0.0, 6.0)
    assert longest_steep_ratio([horizontal, vertical], DIAMETER) == 0.0


def test_steep_window_is_wide_enough_for_letter_diagonals():
    """窗口要能覆盖字母 A/K/M/N 的斜画 —— 否则分界就成了角度窗的巧合。

    此前用 50~85° 时,K(约 45°)与 N(约 87°)落在窗外测出 0.063,
    看似「主轴线无斜画」,其实是判据没测到,不是图上没有。
    """
    assert STEEP_MIN_DEG <= 45.0 and STEEP_MAX_DEG >= 85.0


def test_letter_diagonal_stays_below_the_threshold():
    """字母斜画实测 0.42,`/` 实测 0.47~0.48,阈值取 0.44。"""
    assert longest_steep_ratio([_slash(0.42)], DIAMETER) < FRACTION_RATIO_THRESHOLD


def test_threshold_sits_in_the_measured_empty_band():
    """0.43~0.46 在三张图上全空 —— 阈值落在实测空白带里,不是拍的。"""
    assert 0.43 <= FRACTION_RATIO_THRESHOLD <= 0.46


def test_ratio_on_no_strokes():
    assert longest_steep_ratio([], DIAMETER) == 0.0


def test_ratio_on_zero_diameter():
    assert longest_steep_ratio([_slash()], 0.0) == 0.0


# ── 分数式判定 ────────────────────────────────────────────────

def test_detects_a_fraction_label():
    circle = {"cx": CENTER[0], "cy": CENTER[1], "diameter_pt": DIAMETER}
    assert has_fraction_label([_slash(0.475)], circle)


def test_letter_a_is_not_a_fraction_label():
    """字母 A 有两根斜画,但都短于 `/`。误判会把主轴线踢出序列。"""
    circle = {"cx": CENTER[0], "cy": CENTER[1], "diameter_pt": DIAMETER}
    assert not has_fraction_label([_slash(0.42)], circle)


def test_touching_glyphs_do_not_break_detection():
    """字形在 x 上接触时字符簇会退化为 2 —— 但 `/` 笔画长度不受影响。

    实测漏判的那个圈:簇数 2、单簇宽 16.1pt,而斜画比仍是 0.475。
    """
    circle = {"cx": CENTER[0], "cy": CENTER[1], "diameter_pt": DIAMETER}
    crowded = [_slash(0.475)] + [_stroke(x, -5.0, x + 0.1, 5.0)
                                 for x in range(-8, 8)]
    assert has_fraction_label(crowded, circle)


def test_no_strokes_is_not_a_fraction_label():
    circle = {"cx": CENTER[0], "cy": CENTER[1], "diameter_pt": DIAMETER}
    assert not has_fraction_label([], circle)


def test_detection_scales_with_circle_diameter():
    """A-01-04A 的圈只有 16pt —— 判据必须按比例,不能用绝对长度。"""
    small = {"cx": CENTER[0], "cy": CENTER[1], "diameter_pt": 16.0}
    length = 16.0 * 0.475
    rad = math.radians(70.0)
    dx, dy = math.cos(rad) * length / 2, math.sin(rad) * length / 2
    assert has_fraction_label([_stroke(-dx, dy, dx, -dy)], small)
