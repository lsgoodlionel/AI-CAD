"""分区分组单测(GB/T 50001 §8.0.5)。

**为什么必须分组**:轴号是「分区号-轴线号」。序列本身可由 §8.0.3 推出且实测
逐带 100% 正确(24/24、16/16、14/14、12/12、11/11),但**每条带属于哪个分区**
推不出来时,所有带都会自称分区 1,合并后 99 条真值只剩 38 条唯一标签。

**可推的部分**:§8.0.2「编号宜注写在平面图**下方及左侧**」⇒ 同一分区的横行带与
竖列带互相**紧贴在对方跨度之外**。A-01-02A 实测(直接投影成员圈坐标):

    分区1  横行 y=2178 x∈[992,2522]  竖列 x= 846 y∈[1661,2137]  间隙比 9.5% / 8.6%
    分区2  横行 y=1575 x∈[1589,2259] 竖列 x=1505 y∈[ 778,1529]  间隙比 6.1% / 12.5%
    错配   分区1横行 × 分区2竖列                                  间隙比 0% / **86%**

**一个分区可含多于两条带**:实测分区 1 的轴号列在**左右两侧**都有(x=846 与 2606),
所以分组按连通分量做,不是两两严格配对。

**推不出的部分**:分区**编号**本身(哪个是 1、哪个是 2)。§8.0.5 未规定,
只能由 OCR 锚定或人工确认——每个分区一次,不是每条轴线一次。

**曾踩的坑**:用 `-offset_B` 换算带 B 在带 A 上的位置。该符号只在 αB = αA+90 成立,
而角度归一化到 [0,180) 后符号会翻,实测把分区 2 的两条带判成不同区(比值 3.13/4.62)。
现改为直接投影成员坐标,没有符号陷阱。
"""
from core.model3d.axis_label_band import detect_bands
from core.model3d.axis_zone_grouping import (
    ADJACENCY_SPAN_RATIO, group_bands_into_zones, is_same_zone,
)

DIRS = (0.0, 42.0, 90.0, 132.0)


def _circles(points: list[tuple[float, float]], d: float = 28.0) -> list[dict]:
    return [{"cx": x, "cy": y, "diameter_pt": d} for x, y in points]


def _row(n: int, y: float, x0: float, x1: float) -> list[tuple[float, float]]:
    step = (x1 - x0) / max(n - 1, 1)
    return [(x0 + i * step, y) for i in range(n)]


def _col(n: int, x: float, y0: float, y1: float) -> list[tuple[float, float]]:
    step = (y1 - y0) / max(n - 1, 1)
    return [(x, y0 + i * step) for i in range(n)]


#: 按实测坐标复刻的两个正交分区
Z1 = _row(24, 2178.6, 992.1, 2521.9) + _col(8, 846.1, 1661.1, 2136.8)
Z2 = _row(12, 1575.0, 1589.0, 2259.3) + _col(14, 1504.9, 777.8, 1529.4)


def _bands(points: list[tuple[float, float]]) -> list[dict]:
    return detect_bands(_circles(points), directions=DIRS)


def _by_count(bands: list[dict], count: int) -> dict:
    return next(b for b in bands if b["member_count"] == count)


# ── 配对判据 ──────────────────────────────────────────────────

def test_real_zone1_pair_is_same_zone():
    bands = _bands(Z1)
    assert is_same_zone(_by_count(bands, 24), _by_count(bands, 8))


def test_real_zone2_pair_is_same_zone():
    bands = _bands(Z2)
    assert is_same_zone(_by_count(bands, 12), _by_count(bands, 14))


def test_cross_zone_pair_is_rejected():
    """分区 1 的横行与分区 2 的竖列相距对方跨度的 86%,必须判否。"""
    z1_row = _by_count(_bands(Z1), 24)
    z2_col = _by_count(_bands(Z2), 14)
    assert not is_same_zone(z1_row, z2_col)


def test_parallel_bands_are_never_a_zone_pair():
    """同方向的两条带标注同一族轴线,不构成分区配对。"""
    z1_row = _by_count(_bands(Z1), 24)
    z2_row = _by_count(_bands(Z2), 12)
    assert not is_same_zone(z1_row, z2_row)


def test_pair_is_symmetric():
    bands = _bands(Z1)
    a, b = _by_count(bands, 24), _by_count(bands, 8)
    assert is_same_zone(a, b) == is_same_zone(b, a)


def test_tolerance_is_relative_to_span_not_absolute():
    """不同图纸的网格量级差很多,容差必须相对跨度。"""
    assert 0.1 <= ADJACENCY_SPAN_RATIO <= 0.4


def test_rotated_bands_pair_with_each_other():
    """旋转分区(42°/132°)按自己的方向配对 —— 正交侥幸正确曾掩盖符号 bug。"""
    import math
    ra, rb = math.radians(42.0), math.radians(132.0)
    # 沿 42° 的一行 16 个 + 沿 132° 的一列 11 个,两者在各自法向上紧邻
    row = [(math.cos(ra) * (1654 + i * 58) - math.sin(ra) * -1227,
            math.sin(ra) * (1654 + i * 58) + math.cos(ra) * -1227) for i in range(16)]
    col = [(math.cos(rb) * (-1186 + i * 55) - math.sin(rb) * -1594,
            math.sin(rb) * (-1186 + i * 55) + math.cos(rb) * -1594) for i in range(11)]
    bands = detect_bands(_circles(row + col), directions=DIRS)
    assert is_same_zone(_by_count(bands, 16), _by_count(bands, 11))


# ── 分组 ──────────────────────────────────────────────────────

def test_groups_two_orthogonal_zones():
    zones = group_bands_into_zones(_bands(Z1 + Z2))
    assert len(zones) == 2


def test_each_zone_holds_both_a_row_and_a_column():
    for zone in group_bands_into_zones(_bands(Z1 + Z2)):
        angles = {b["band_angle_deg"] for b in zone["bands"]}
        assert angles == {0.0, 90.0}


def test_zone_records_axis_counts_per_direction():
    zone = group_bands_into_zones(_bands(Z1))[0]
    assert zone["numeric_axes"] == 24      # 横行标注竖向轴线
    assert zone["alpha_axes"] == 8         # 竖列标注横向轴线


def test_a_zone_may_hold_more_than_two_bands():
    """实测分区 1 左右两侧都有轴号列 —— 分组必须允许三条带同区。"""
    both_sides = Z1 + _col(8, 2606.0, 1610.7, 2108.8)
    zone = group_bands_into_zones(_bands(both_sides))[0]
    assert len(zone["bands"]) == 3
    assert zone["alpha_axes"] == 16        # 左 8 + 右 8


def test_unpaired_band_becomes_its_own_zone():
    """只有一条带的分区也要出现 —— 丢掉它等于静默漏掉一批轴线。"""
    lonely = _row(5, 60.0, 100.0, 300.0)
    zones = group_bands_into_zones(_bands(Z1 + lonely))
    assert len(zones) == 2
    assert any(len(z["bands"]) == 1 for z in zones)


def test_zones_are_ordered_by_size_descending():
    """大分区在前,便于人工确认时先处理主网格。"""
    zones = group_bands_into_zones(_bands(Z2 + Z1))
    assert zones[0]["numeric_axes"] == 24


def test_zone_number_is_not_guessed():
    """§8.0.5 未规定哪个分区是 1;推不出就留空,不能瞎猜。"""
    assert group_bands_into_zones(_bands(Z1))[0]["zone"] is None


def test_group_on_empty_input():
    assert group_bands_into_zones([]) == []


def test_a_band_joins_exactly_one_zone():
    bands = _bands(Z1 + Z2)
    placed = [id(b) for z in group_bands_into_zones(bands) for b in z["bands"]]
    assert len(placed) == len(set(placed)) == len(bands)


def test_group_does_not_mutate_input():
    bands = _bands(Z1)
    before = [dict(b) for b in bands]
    group_bands_into_zones(bands)
    assert [dict(b) for b in bands] == before


# ── 小带吸附(按分区二维范围,不是一维带跨度)──────────────────────

def test_small_band_attaches_by_two_dimensional_zone_extent():
    """一维带跨度判不了小带归属,二维分区范围可以。

    实测:band(x=2631, y 938~1164)是**分区 2 的附加轴线** `2-1/k`。
    按「最近的带」判会归到分区 1(比值 0.07 < 0.56)——**错**;
    按分区二维范围判:分区 2 的 y[778,1529] 把它包住,而分区 1 的
    y[1661,2179] 离它 497pt,归属一目了然。
    """
    from core.model3d.axis_zone_grouping import attach_small_bands

    zones = group_bands_into_zones(_bands(Z1 + Z2))
    extra = detect_bands(_circles(_col(4, 2631.0, 938.0, 1164.0)),
                         directions=DIRS, min_members=3)
    got = attach_small_bands(zones, extra)
    host = next(z for z in got if any(b["member_count"] == 4 for b in z["bands"]))
    assert host["numeric_axes"] == 12          # 分区 2(12+14),不是分区 1(24+8)


def test_small_band_of_2_13_to_2_15_lands_in_zone2():
    """实测 band(y=1308, x 2220~2572)= 2-13/14/15,属分区 2。"""
    from core.model3d.axis_zone_grouping import attach_small_bands

    zones = group_bands_into_zones(_bands(Z1 + Z2))
    extra = detect_bands(_circles(_row(5, 1307.9, 2220.0, 2572.0)),
                         directions=DIRS, min_members=3)
    host = next(z for z in attach_small_bands(zones, extra)
                if any(b["member_count"] == 5 for b in z["bands"]))
    assert host["numeric_axes"] == 12 + 5


def test_attach_requires_direction_compatibility():
    """旋转带不能被吸进只有正交轴线的分区。"""
    import math
    from core.model3d.axis_zone_grouping import attach_small_bands

    zones = group_bands_into_zones(_bands(Z1))
    ra = math.radians(132.0)
    rot = [(1500.0 + math.cos(ra) * i * 55.0, 1900.0 + math.sin(ra) * i * 55.0)
           for i in range(4)]
    extra = detect_bands(_circles(rot), directions=DIRS, min_members=3)
    assert len(attach_small_bands(zones, extra)) == 2   # 自成一区


def test_far_away_band_is_not_attached():
    """离所有分区都远的带自成一区,不能硬塞。"""
    from core.model3d.axis_zone_grouping import attach_small_bands

    zones = group_bands_into_zones(_bands(Z1))
    extra = detect_bands(_circles(_row(4, 100.0, 100.0, 400.0)),
                         directions=DIRS, min_members=3)
    assert len(attach_small_bands(zones, extra)) == 2


def test_attach_on_empty_leftovers():
    from core.model3d.axis_zone_grouping import attach_small_bands

    zones = group_bands_into_zones(_bands(Z1))
    assert attach_small_bands(zones, []) == zones


def test_attach_does_not_mutate_input_zones():
    from core.model3d.axis_zone_grouping import attach_small_bands

    zones = group_bands_into_zones(_bands(Z1 + Z2))
    before = [len(z["bands"]) for z in zones]
    extra = detect_bands(_circles(_row(5, 1307.9, 2220.0, 2572.0)),
                         directions=DIRS, min_members=3)
    attach_small_bands(zones, extra)
    assert [len(z["bands"]) for z in zones] == before
