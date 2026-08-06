"""坐标标注识别单测。

**图面结构**(渲图确认 + 实测):`文字 → 水平段 → 斜段 → 末端落在轴线交叉点上`。
A-01-02A 实测检出 16 处引线,末端到轴线的法向距离 0.03~1.47pt,
且水平段长度高度一致(93.7pt × 12 处)。

**为什么坐标必须读而不能推**:轴号有编写顺序可推(§8.0.3),坐标值是任意实数。
所幸坐标文字比轴号大一个量级,**OCR 实测置信 0.96~1.00 且逐字符正确**:

    X=-6084.141 / Y=23.524      ✓
    X=-6164.580 / Y=-179.651    ✓
    X=-6228.501 / Y=-156.750    ✓
    X= 6005.463 / 109.401       ← **负号丢了**

负号是致命项——符号错会把模型挪到 12 公里外。用两重一致性修复:
1. 同一张图的坐标聚成一簇(实测 X∈[-6229,-5922]),孤立的正值是丢号;
2. 页面↔世界必须满足同一个相似变换,符号错的点残差会爆掉(交给 `drawing_anchor`)。
"""
import pytest

from core.model3d.coord_annotation import (
    HORIZONTAL_TOLERANCE_DEG, JOINT_TOLERANCE_PT, find_leaders,
    parse_coordinate_tokens, repair_sign_by_consensus,
)


def _seg(x0, y0, x1, y1):
    return (x0, y0, x1, y1)


# ── 引线几何 ──────────────────────────────────────────────────

def test_finds_a_horizontal_plus_diagonal_leader():
    """实测形态:水平段 93.7pt + 斜段约 67pt,末端落在轴线上。"""
    segs = [_seg(2572.5, 1566.6, 2478.8, 1566.6),      # 水平
            _seg(2478.8, 1566.6, 2521.9, 1610.7)]      # 斜向,末端在轴线上
    axes = [{"angle_deg": 0.0, "offset_pt": 1610.7}]
    got = find_leaders(segs, axes)
    assert len(got) == 1
    assert got[0]["tip"] == pytest.approx((2521.9, 1610.7))
    assert got[0]["text_anchor"] == pytest.approx((2572.5, 1566.6))


def test_rejects_leader_whose_tip_misses_every_axis():
    """末端不落在轴线上就不是坐标引线(§图面惯例:坐标定位交叉点)。"""
    segs = [_seg(100.0, 100.0, 200.0, 100.0), _seg(200.0, 100.0, 250.0, 150.0)]
    axes = [{"angle_deg": 0.0, "offset_pt": 900.0}]
    assert find_leaders(segs, axes) == []


def test_rejects_two_horizontal_segments():
    """两段都水平不构成引线(那是尺寸线)。"""
    segs = [_seg(0.0, 500.0, 100.0, 500.0), _seg(100.0, 500.0, 200.0, 500.0)]
    axes = [{"angle_deg": 0.0, "offset_pt": 500.0}]
    assert find_leaders(segs, axes) == []


def test_rejects_disconnected_segments():
    """两段不相接就不是折线。"""
    segs = [_seg(0.0, 500.0, 100.0, 500.0), _seg(300.0, 500.0, 350.0, 550.0)]
    axes = [{"angle_deg": 0.0, "offset_pt": 550.0}]
    assert find_leaders(segs, axes) == []


def test_joint_tolerance_is_tight():
    """接点容差必须紧 —— 松了会把无关线段凑成引线。"""
    assert JOINT_TOLERANCE_PT <= 2.0
    assert HORIZONTAL_TOLERANCE_DEG <= 1.0


def test_diagonal_may_point_either_way():
    """引线斜段可向上或向下(实测两种都有)。"""
    axes = [{"angle_deg": 0.0, "offset_pt": 100.0}]
    up = [_seg(300.0, 200.0, 200.0, 200.0), _seg(200.0, 200.0, 250.0, 100.0)]
    assert len(find_leaders(up, axes)) == 1


def test_dedupes_leaders_sharing_a_tip():
    """同一末端被多组线段凑出时只算一处。"""
    segs = [_seg(300.0, 200.0, 200.0, 200.0),
            _seg(200.0, 200.0, 250.0, 100.0),
            _seg(310.0, 200.0, 200.0, 200.0)]      # 另一条水平段,同一斜段
    axes = [{"angle_deg": 0.0, "offset_pt": 100.0}]
    assert len(find_leaders(segs, axes)) == 1


def test_find_leaders_on_empty_input():
    assert find_leaders([], []) == []


# ── 文字解析 ──────────────────────────────────────────────────

def test_parses_x_and_y_from_ocr_tokens():
    got = parse_coordinate_tokens(["X=-6084.141", "Y=23.524"])
    assert got == {"x": -6084.141, "y": 23.524}


def test_tolerates_space_after_equals():
    """实测 OCR 会输出 `X= -6228.501`。"""
    got = parse_coordinate_tokens(["X= -6228.501", "Y=-156.750"])
    assert got == {"x": -6228.501, "y": -156.750}


def test_bare_number_is_taken_as_the_missing_axis():
    """实测 OCR 把 `Y=-109.401` 读成 `109.401`(丢了标签和符号)。"""
    got = parse_coordinate_tokens(["X= 6005.463", "109.401"])
    assert got == {"x": 6005.463, "y": 109.401}


def test_returns_none_when_no_numbers_found():
    assert parse_coordinate_tokens(["备注:", "本工程"]) is None


def test_ignores_extra_tokens():
    got = parse_coordinate_tokens(["1-P", "X=-6017.133", "Y=-141.973", "8600"])
    assert got["x"] == -6017.133 and got["y"] == -141.973


def test_parse_on_empty():
    assert parse_coordinate_tokens([]) is None


# ── 符号一致性修复 ────────────────────────────────────────────

def test_repairs_a_dropped_minus_sign():
    """实测 X∈[-6229,-5922];孤立的 +6005.463 只能是丢了负号。"""
    values = [-6084.141, -6164.580, -6228.501, 6005.463, -6047.019]
    got = repair_sign_by_consensus(values)
    assert got[3] == pytest.approx(-6005.463)
    assert got[:3] == values[:3]          # 本来正确的不动


def test_does_not_touch_a_genuinely_mixed_set():
    """Y 值实测有正有负(-179.651 ~ +47.504),不能强行统一符号。"""
    values = [-179.651, -156.750, 23.524, 47.504, -105.784]
    assert repair_sign_by_consensus(values) == values


def test_requires_a_clear_majority_before_repairing():
    """多数不明显时不动手 —— 猜错符号比不修更糟。"""
    values = [-100.0, -200.0, 150.0, 250.0]
    assert repair_sign_by_consensus(values) == values


def test_repair_uses_magnitude_band_not_just_sign():
    """量级也要对得上:+6005 与 -6084 同量级才认为是丢号。

    若某值量级完全不同(如 12.5),翻符号也无意义,不动它。
    """
    values = [-6084.141, -6164.580, -6228.501, 12.5, -6047.019]
    got = repair_sign_by_consensus(values)
    assert got[3] == 12.5


def test_repair_on_short_input():
    assert repair_sign_by_consensus([5.0]) == [5.0]
    assert repair_sign_by_consensus([]) == []


def test_repair_does_not_mutate_input():
    values = [-6084.141, 6005.463, -6164.580, -6228.501]
    before = list(values)
    repair_sign_by_consensus(values)
    assert values == before


# ── RANSAC 定变换 + 按变换修符号 ──────────────────────────────────

def _pair(px, py, wx, wy):
    return {"page": (px, py), "world": (wx, wy)}


def _synth(n=12, scale=0.12, rot_deg=58.0, tx=-6300.0, ty=-200.0):
    """按已知相似变换造一批干净点(参数取自实测量级)。"""
    import math
    rad = math.radians(rot_deg)
    out = []
    for i in range(n):
        px, py = 1000.0 + i * 130.0, 800.0 + (i % 5) * 260.0
        wx = scale * (math.cos(rad) * px - math.sin(rad) * py) + tx
        wy = scale * (math.sin(rad) * px + math.cos(rad) * py) + ty
        out.append(_pair(px, py, round(wx, 3), round(wy, 3)))
    return out


def test_ransac_recovers_the_transform_from_clean_pairs():
    from core.model3d.coord_annotation import ransac_similarity

    got = ransac_similarity(_synth())
    assert len(got["inliers"]) == 12
    assert got["transform"]["scale"] == pytest.approx(0.12, abs=1e-4)


def test_ransac_survives_three_gross_outliers():
    """实测 16 点里有 3 个粗错(19%),最小二乘被拽偏到残差失去判别力。"""
    from core.model3d.coord_annotation import ransac_similarity

    pairs = _synth(13)
    pairs[3] = _pair(*pairs[3]["page"], -pairs[3]["world"][0], pairs[3]["world"][1])
    pairs[7] = _pair(*pairs[7]["page"], pairs[7]["world"][0], -pairs[7]["world"][1])
    pairs[9] = _pair(*pairs[9]["page"], -1.0, -1.0)
    got = ransac_similarity(pairs)
    assert set(got["outliers"]) == {3, 7, 9}
    assert got["transform"]["scale"] == pytest.approx(0.12, abs=1e-3)


def test_ransac_is_deterministic():
    """穷举点对而非随机采样 —— 同样输入必须给同样结果,便于复现。"""
    from core.model3d.coord_annotation import ransac_similarity

    pairs = _synth(10)
    assert ransac_similarity(pairs) == ransac_similarity(pairs)


def test_ransac_returns_none_with_too_few_pairs():
    from core.model3d.coord_annotation import ransac_similarity

    assert ransac_similarity([_pair(0, 0, 0, 0)]) is None


def test_repairs_a_flipped_sign_using_the_transform():
    """OCR 丢负号后 Y 本来正负混杂无法靠共识修 —— 但变换能判。"""
    from core.model3d.coord_annotation import (
        ransac_similarity, repair_outliers_by_transform)

    pairs = _synth(12)
    truth_y = pairs[5]["world"][1]
    pairs[5] = _pair(*pairs[5]["page"], pairs[5]["world"][0], -truth_y)
    got = ransac_similarity(pairs)
    fixed = repair_outliers_by_transform(pairs, got)
    assert fixed[5]["world"][1] == pytest.approx(truth_y, abs=1e-3)
    assert fixed[5]["repaired"] == "y_sign"


def test_unrepairable_outlier_is_flagged_not_silently_kept():
    """粗错(不是符号问题)必须标出来交给人工,不能悄悄留在锚点里。"""
    from core.model3d.coord_annotation import (
        ransac_similarity, repair_outliers_by_transform)

    pairs = _synth(12)
    pairs[4] = _pair(*pairs[4]["page"], -1.0, -1.0)
    fixed = repair_outliers_by_transform(pairs, ransac_similarity(pairs))
    assert fixed[4]["repaired"] is None
    assert fixed[4]["outlier"] is True


def test_repair_does_not_touch_inliers():
    from core.model3d.coord_annotation import (
        ransac_similarity, repair_outliers_by_transform)

    pairs = _synth(10)
    fixed = repair_outliers_by_transform(pairs, ransac_similarity(pairs))
    assert all(p["repaired"] is None and not p["outlier"] for p in fixed)


# ── 裁图窗口与裸数字兜底(A-01-03A/04A 大量粗错的根因)────────────────

def test_crop_window_scales_with_the_leader():
    """裁图窗口不能是固定常量 —— 实测水平段 93.7 / 58.3 / 33.4pt 三个量级。

    A-01-04A 的引线只有 36.9pt,用 ±130 的固定窗一次框进 2~3 处标注,
    OCR 于是在一个窗口里读出两个 X 值。
    """
    from core.model3d.coord_annotation import text_crop_rect

    narrow = text_crop_rect({"text_anchor": (1000.0, 500.0),
                             "joint": (1036.0, 500.0), "horizontal_len": 36.0})
    wide = text_crop_rect({"text_anchor": (1000.0, 500.0),
                           "joint": (1094.0, 500.0), "horizontal_len": 94.0})
    assert (narrow[2] - narrow[0]) < (wide[2] - wide[0])
    # 窄引线的窗口不该跨到 100pt 外去
    assert (narrow[2] - narrow[0]) < 100.0


def test_crop_covers_the_horizontal_segment():
    """文字写在水平段上方/下方,窗口必须盖住整段。"""
    from core.model3d.coord_annotation import text_crop_rect

    x0, y0, x1, y1 = text_crop_rect({"text_anchor": (1000.0, 500.0),
                                     "joint": (1094.0, 500.0),
                                     "horizontal_len": 94.0})
    assert x0 <= 1000.0 and x1 >= 1094.0
    assert y0 < 500.0 < y1


def test_bare_number_must_look_like_a_coordinate():
    """尺寸标注是整数(2900/4000/10200),坐标带 3 位小数 —— 这是可判的。

    实测 A-01-04A 把 `(10200, 5361)` 当成坐标写进了锚点候选。
    """
    assert parse_coordinate_tokens(["10200", "5361"]) is None
    assert parse_coordinate_tokens(["X=-6005.463", "109.401"]) == {
        "x": -6005.463, "y": 109.401}


def test_conflicting_labelled_values_are_rejected():
    """一个窗口里出现两个 X 值 = 框进了邻近标注,取第一个就是猜。

    实测 A-01-04A:`['X=-6006.746', 'Y=-145.589', 'X=-6005.950']`。
    """
    assert parse_coordinate_tokens(
        ["X=-6006.746", "Y=-145.589", "X=-6005.950"]) is None


def test_repeated_identical_label_is_fine():
    """同一个值被读到两次(换行/重叠)不算冲突。"""
    got = parse_coordinate_tokens(["X=-6084.141", "Y=23.524", "X=-6084.141"])
    assert got == {"x": -6084.141, "y": 23.524}
