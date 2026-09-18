"""线状构件（梁、墙）去重 —— 此前识别器只给柱和设备去重。

**实测**（歌剧院 v86，2026-09-18）：5555 根梁里 4427 根被合理性引擎判为
冗余，其中 **3037 根与重合对象出自同一张图**；按 `src` 分组数精确重叠，
同一条中心线叠放 4、8、16、甚至 80 份（倍数多为 2 的幂 —— 同一条边线在
PDF 里被重画几遍，平行线配对就把每一遍都配成一根梁）。

梁和墙只有中心线 `path` + `width`，没有 `outline`，所以 `merge_overlapping`
按轮廓包围盒去重时它们**全部原样放行** —— 不是判据宽松，是根本没进判据。
"""
import pytest

from core.model3d.dedupe import merge_collinear, segment_box


def _beam(x0, y0, x1, y1, width=0.35, **extra):
    return {"path": [[x0, y0], [x1, y1]], "width": width, **extra}


def _merge(items):
    return merge_collinear(items)


@pytest.mark.unit
def test_a_beam_stacked_four_times_counts_once():
    """实测形态：同一条中心线叠 4 份 → 1 根。"""
    stacked = [_beam(0.0, 5.0, 8.0, 5.0) for _ in range(4)]
    assert len(_merge(stacked)) == 1


@pytest.mark.unit
def test_a_short_beam_lying_inside_a_longer_one_is_dropped_and_the_long_one_kept():
    """实测形态：`[-0.435, 49.309]→[-0.435, 47.055]` 落在整跨梁里 —— 留长的。"""
    full = _beam(-0.435, 55.906, -0.435, 47.055, tag="full")
    part = _beam(-0.435, 49.309, -0.435, 47.055, tag="part")
    kept = _merge([part, full])
    assert [e["tag"] for e in kept] == ["full"]


@pytest.mark.unit
def test_nearly_collinear_copies_within_a_fraction_of_width_merge():
    """两次配对的中线偏 4 毫米（实测 -13.091 vs -13.087）仍是同一根梁。"""
    a = _beam(-13.091, 21.143, -13.091, 19.623, width=0.483)
    b = _beam(-13.087, 21.143, -13.087, 19.623, width=0.475)
    assert len(_merge([a, b])) == 1


@pytest.mark.unit
def test_two_parallel_beams_a_bay_apart_both_survive():
    a = _beam(0.0, 0.0, 8.0, 0.0)
    b = _beam(0.0, 8.4, 8.0, 8.4)
    assert len(_merge([a, b])) == 2


@pytest.mark.unit
def test_crossing_beams_both_survive():
    """十字相交只在交点处重叠一个 w×w 小方块，不是重复。"""
    a = _beam(0.0, 4.0, 8.0, 4.0)
    b = _beam(4.0, 0.0, 4.0, 8.0)
    assert len(_merge([a, b])) == 2


@pytest.mark.unit
def test_beams_meeting_end_to_end_both_survive():
    """同一轴线上的相邻两跨在柱处接头，端部只重叠半个梁宽。"""
    a = _beam(0.0, 0.0, 8.0, 0.0)
    b = _beam(7.9, 0.0, 16.0, 0.0)
    assert len(_merge([a, b])) == 2


@pytest.mark.unit
def test_partially_overlapping_collinear_segments_both_survive():
    """0~8 与 4~12：合成一段会丢 4 米长度，比多算 4 米更难发现。

    柱的去重判据里有「IoU > 0.1」一条，照搬过来这一对 IoU = 1/3 会被合并。
    线状构件只按「短的几乎整段落在长的里面」合并。
    """
    a = _beam(0.0, 0.0, 8.0, 0.0)
    b = _beam(4.0, 0.0, 12.0, 0.0)
    assert len(_merge([a, b])) == 2


@pytest.mark.unit
def test_slanted_beams_are_not_judged_by_their_bounding_box():
    """斜梁的轴对齐包围盒远大于梁本身：两根交叉的斜梁包围盒几乎重合。

    拿包围盒判就会把两根不同的梁合成一根，所以斜的一律放行不判。
    """
    a = _beam(0.0, 0.0, 8.0, 8.0)
    b = _beam(0.0, 8.0, 8.0, 0.0)
    assert len(_merge([a, b])) == 2


@pytest.mark.unit
def test_beams_without_width_or_with_a_degenerate_path_pass_through():
    no_width = {"path": [[0.0, 0.0], [8.0, 0.0]]}
    one_point = {"path": [[0.0, 0.0]], "width": 0.3}
    copies = [_beam(0.0, 0.0, 8.0, 0.0), _beam(0.0, 0.0, 8.0, 0.0)]
    kept = _merge([no_width, one_point, *copies])
    assert no_width in kept and one_point in kept
    assert len(kept) == 3


@pytest.mark.unit
def test_segment_box_is_the_thick_segment_extent():
    assert segment_box(_beam(0.0, 5.0, 8.0, 5.0, width=0.4)) == pytest.approx(
        (0.0, 4.8, 8.0, 5.2))
