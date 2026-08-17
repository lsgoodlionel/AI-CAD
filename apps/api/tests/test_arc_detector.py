"""圆弧检测单测。

**为什么单独做**:外轮廓(右侧一条平滑长弧)与同心圆定心,卡在同一个子问题上
——把**真正的弧**从 8 万条碎段里分离出来。此前两次失败:

1. 「顶点到中心等距」——全命中**离中心远的短线段**(半径 2000pt 时 2% 容差
   就是 40pt,天然满足);
2. 「连续三点外接圆心一致」用**绝对**容差 20pt ——半径 5pt 的文字笔画在
   20pt 容差下必然通过,中位数被噪声主导,定心偏 373pt。

两次同一类错:**尺度相关的量必须用相对容差**。所以这里的一致性判据是
`圆心离散度 / 半径`,而不是圆心离散度本身。
"""
import math

import pytest

from core.model3d.arc_detector import (
    CENTER_SPREAD_RATIO, MIN_ARC_POINTS, MIN_SWEEP_DEG, circumcenter,
    detect_arcs, fit_arc,
)


def _arc_points(center, radius, start_deg, sweep_deg, n=12):
    return [(center[0] + math.cos(math.radians(start_deg + sweep_deg * i / (n - 1))) * radius,
             center[1] + math.sin(math.radians(start_deg + sweep_deg * i / (n - 1))) * radius)
            for i in range(n)]


# ── 外接圆心 ──────────────────────────────────────────────────

def test_circumcenter_of_three_points_on_a_circle():
    got = circumcenter((100.0, 0.0), (0.0, 100.0), (-100.0, 0.0))
    assert got == pytest.approx((0.0, 0.0), abs=1e-6)


def test_circumcenter_of_collinear_points_is_none():
    assert circumcenter((0.0, 0.0), (1.0, 1.0), (2.0, 2.0)) is None


# ── 单段弧拟合 ────────────────────────────────────────────────

def test_fits_a_clean_arc():
    pts = _arc_points((500.0, 400.0), 300.0, 0.0, 90.0)
    got = fit_arc(pts)
    assert got is not None
    assert got["center"] == pytest.approx((500.0, 400.0), abs=0.5)
    assert got["radius"] == pytest.approx(300.0, abs=0.5)
    assert got["sweep_deg"] == pytest.approx(90.0, abs=1.0)


def test_rejects_a_straight_polyline():
    pts = [(float(i) * 30.0, 100.0) for i in range(12)]
    assert fit_arc(pts) is None


def test_rejects_a_zigzag():
    """折线的转向忽左忽右,不是弧 —— 文字笔画大多是这种。"""
    pts = [(float(i) * 20.0, 100.0 + (20.0 if i % 2 else -20.0)) for i in range(12)]
    assert fit_arc(pts) is None


def test_relative_tolerance_rejects_a_tiny_noisy_curve():
    """**绝对容差会放行小半径噪声**:半径 5pt 的抖动在 20pt 容差下必然通过。

    这正是上一次定心偏 373pt 的根因,判据必须按半径相对化。
    """
    import random

    rng = random.Random(7)
    pts = [(5.0 * math.cos(math.radians(i * 30)) + rng.uniform(-1.5, 1.5),
            5.0 * math.sin(math.radians(i * 30)) + rng.uniform(-1.5, 1.5))
           for i in range(12)]
    assert fit_arc(pts) is None


def test_spread_ratio_is_relative_not_absolute():
    assert 0.0 < CENTER_SPREAD_RATIO <= 0.1


def test_rejects_too_few_points():
    assert MIN_ARC_POINTS >= 5
    assert fit_arc(_arc_points((0.0, 0.0), 100.0, 0.0, 90.0, n=3)) is None


def test_rejects_a_sliver_sweep():
    """扫掠角太小的一小段,圆心极不稳定 —— 不能当弧用。"""
    assert MIN_SWEEP_DEG >= 5.0
    assert fit_arc(_arc_points((0.0, 0.0), 900.0, 0.0, 1.0, n=12)) is None


def test_large_radius_arc_is_accepted():
    """同心圆半径可达上千 pt,不能因为「太平」被当成直线。"""
    got = fit_arc(_arc_points((1680.0, 1080.0), 1200.0, 10.0, 40.0, n=16))
    assert got is not None
    assert got["radius"] == pytest.approx(1200.0, abs=2.0)


def test_fit_records_how_many_points_supported_it():
    got = fit_arc(_arc_points((0.0, 0.0), 300.0, 0.0, 90.0, n=14))
    assert got["points"] == 14


# ── 多路径检测 ────────────────────────────────────────────────

def test_detects_arcs_across_paths():
    paths = [_arc_points((500.0, 400.0), 300.0, 0.0, 90.0),
             [(float(i) * 30.0, 900.0) for i in range(12)]]      # 直线,应排除
    got = detect_arcs(paths)
    assert len(got) == 1
    assert got[0]["center"] == pytest.approx((500.0, 400.0), abs=0.5)


def test_detect_filters_by_minimum_radius():
    """小半径弧多是文字笔画,按需过滤。"""
    paths = [_arc_points((10.0, 10.0), 6.0, 0.0, 120.0),
             _arc_points((500.0, 400.0), 300.0, 0.0, 90.0)]
    got = detect_arcs(paths, min_radius=50.0)
    assert len(got) == 1
    assert got[0]["radius"] == pytest.approx(300.0, abs=1.0)


def test_detect_on_empty():
    assert detect_arcs([]) == []


def test_detect_does_not_mutate_input():
    paths = [_arc_points((500.0, 400.0), 300.0, 0.0, 90.0)]
    before = [list(p) for p in paths]
    detect_arcs(paths)
    assert [list(p) for p in paths] == before


# ── 同心归组 ──────────────────────────────────────────────────

def test_groups_concentric_arcs_and_returns_their_center():
    from core.model3d.arc_detector import concentric_center

    paths = [_arc_points((1680.0, 1080.0), r, 0.0, 60.0, n=14)
             for r in (400.0, 700.0, 1000.0, 1300.0)]
    arcs = detect_arcs(paths, min_radius=50.0)
    got = concentric_center(arcs)
    assert got is not None
    assert got["center"] == pytest.approx((1680.0, 1080.0), abs=1.0)
    assert got["arcs"] == 4


def test_concentric_center_needs_agreement():
    """圆心各不相同的弧不构成同心族 —— 返回 None 而不是取平均。"""
    from core.model3d.arc_detector import concentric_center

    paths = [_arc_points((300.0, 300.0), 200.0, 0.0, 90.0, n=14),
             _arc_points((1500.0, 1500.0), 200.0, 0.0, 90.0, n=14)]
    assert concentric_center(detect_arcs(paths)) is None


def test_concentric_center_on_empty():
    from core.model3d.arc_detector import concentric_center

    assert concentric_center([]) is None


# ── 跨 path 平滑追链(同心弧不是一 path 一条)────────────────────────

def _seg(a, b):
    return (a[0], a[1], b[0], b[1])


def _arc_segments(center, radius, start_deg, sweep_deg, n=16):
    pts = _arc_points(center, radius, start_deg, sweep_deg, n)
    return [_seg(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]


def test_chains_segments_of_one_arc_across_paths():
    """同心弧被炸成独立短段(实测逐 path 拟合只找到 5 条弧)——必须跨 path 追。"""
    from core.model3d.arc_detector import trace_smooth_chains

    segs = _arc_segments((500.0, 400.0), 300.0, 0.0, 90.0)
    chains = trace_smooth_chains(segs)
    assert len(chains) == 1
    assert len(chains[0]) == len(segs)


def test_crossing_line_does_not_derail_the_chain():
    """**这正是此前失败的地方**:轴线穿过弧时,连通追踪把整个轴网串成一坨
    (实测 53100 段的巨链)。轴线以大角度穿过,不会是最平滑的延续。
    """
    from core.model3d.arc_detector import trace_smooth_chains

    segs = _arc_segments((500.0, 400.0), 300.0, 0.0, 90.0)
    joint = (segs[8][0], segs[8][1])
    segs = segs + [_seg(joint, (joint[0] + 400.0, joint[1] + 400.0)),
                   _seg(joint, (joint[0] - 400.0, joint[1] - 400.0))]
    longest = max(trace_smooth_chains(segs), key=len)
    assert len(longest) == 15          # 整段弧,没把两条直线吞进来


def test_sharp_turn_ends_the_chain():
    from core.model3d.arc_detector import trace_smooth_chains

    segs = [_seg((0.0, 0.0), (100.0, 0.0)), _seg((100.0, 0.0), (100.0, 100.0))]
    assert all(len(c) == 1 for c in trace_smooth_chains(segs))


def test_chain_to_points_feeds_fit_arc():
    from core.model3d.arc_detector import chain_points, trace_smooth_chains

    segs = _arc_segments((1680.0, 1080.0), 900.0, 0.0, 60.0, n=30)
    chain = max(trace_smooth_chains(segs), key=len)
    got = fit_arc(chain_points(chain))
    assert got is not None
    assert got["center"] == pytest.approx((1680.0, 1080.0), abs=2.0)


def test_trace_on_empty():
    from core.model3d.arc_detector import trace_smooth_chains

    assert trace_smooth_chains([]) == []


def test_resampling_makes_large_radius_arcs_fittable():
    """**不抽稀就拟合不出大半径弧**:半径 900pt 上相邻 2pt 碎段的真实转角只有
    0.13°,坐标精度 0.1pt —— 噪声淹没曲率。实测 83401 条碎段追出 1116 段的
    长链却一条弧都拟合不出,根因就在这里。
    """
    import random

    from core.model3d.arc_detector import RESAMPLE_POINTS

    rng = random.Random(11)
    pts = []
    for i in range(400):                       # 密集采样 + 0.1pt 量化噪声
        a = math.radians(i * 0.15)
        pts.append((round(1680.0 + math.cos(a) * 900.0 + rng.uniform(-.05, .05), 1),
                    round(1080.0 + math.sin(a) * 900.0 + rng.uniform(-.05, .05), 1)))
    got = fit_arc(pts)
    assert got is not None, "抽稀后应能拟合出大半径弧"
    assert got["center"] == pytest.approx((1680.0, 1080.0), abs=20.0)
    assert RESAMPLE_POINTS >= 12
