"""矢量轴线提取单测(主方向聚类 / 双闸筛选 / 间距模数)。

**契约已变更**:本模块**不再是轴线识别的主路线**。主路线是
`axis_label_circle` + `axis_label_band`——按 GB/T 50001 §8.0.2「一个圈一条轴线、
圆心在轴线延长线上」由轴号圈锚定轴线。在完整真值分母下两者实测:

    几何筛线(本模块)  正交 48/68 = 71%,旋转 **过检 75 条**(真值 31)
    圈锚定(主路线)    全部 103/108 = 95%,旋转 **精确 31 条**

本模块保留的作用是**交叉校验与降级**:
- 校验:圈锚定出的轴线是否真有一条点划线穿过(无线的圈=可疑);
- 降级:PDF 里没有轴号圈时(非定位图、被裁切、别的出图习惯)仍能出候选。

**因此不要再在这里调阈值去追召回率**——那条路已经量过了,天花板是 71% 且
伴随严重过检。真正的收益来自按国标读(线型判据、圈锚定),不是调参数。
详见 `docs/PHASE_I_BLUEPRINT.md` §4「九个错误结论」与 §6「两条路线正面对比」。
"""
import math

from core.model3d.vector_axis_extractor import (
    axes_in_direction, build_axes, dominant_directions, extract_vector_axes,
    modulus_score, orthogonal_families, segment_angle_deg, segment_length,
    to_normalized_line,
)

PAGE_W, PAGE_H = 3370.0, 2384.0


def _v(x, y0=200.0, y1=2100.0):
    """竖线(90°)"""
    return (x, y0, x, y1)


def _h(y, x0=200.0, x1=3100.0):
    """横线(0°)"""
    return (x0, y, x1, y)


#: 实测 A-01-02A 轴线的真实节奏:长划 10.5 / 短划 2.1 / 空 2.1(GB/T 50001 单点长画线)
def _dash_dot_spans(start: float, end: float) -> list[tuple[float, float]]:
    """按实测节奏(长划 10.5 / 短划 2.1 / 空 2.1)生成划的区间。"""
    out, pos, i = [], start, 0
    while True:
        dash = 10.5 if i % 2 == 0 else 2.1
        if pos + dash > end:
            return out
        out.append((pos, pos + dash))
        pos += dash + 2.1
        i += 1


def _dash_dot_v(x, y0=200.0, y1=2100.0):
    """竖向**点划线**轴线,按实测节奏生成碎段。"""
    return [(x, lo, x, hi) for lo, hi in _dash_dot_spans(y0, y1)]


def _dash_dot_h(y, x0=200.0, x1=3100.0):
    return [(lo, y, hi, y) for lo, hi in _dash_dot_spans(x0, x1)]


def _dash_dot_rot(cx, cy, length, angle_deg):
    """任意角度的**点划线**轴线(旋转轴网用)。"""
    import math as _m
    rad = _m.radians(angle_deg)
    ux, uy = _m.cos(rad), _m.sin(rad)
    x0, y0 = cx - ux * length / 2, cy - uy * length / 2
    return [(x0 + ux * lo, y0 + uy * lo, x0 + ux * hi, y0 + uy * hi)
            for lo, hi in _dash_dot_spans(0.0, length)]


def _rot(cx, cy, length, angle_deg):
    """过 (cx,cy) 的指定角度线段"""
    rad = math.radians(angle_deg)
    dx, dy = math.cos(rad) * length / 2, math.sin(rad) * length / 2
    return (cx - dx, cy - dy, cx + dx, cy + dy)


# ── 基础几何 ────────────────────────────────────────────────────

def test_segment_angle_and_length():
    assert segment_angle_deg(_v(100)) == 90.0
    assert segment_angle_deg(_h(100)) == 0.0
    assert abs(segment_length((0, 0, 3, 4)) - 5.0) < 1e-9


def test_segment_angle_is_direction_agnostic():
    assert segment_angle_deg((0, 0, 10, 10)) == segment_angle_deg((10, 10, 0, 0))


# ── 主方向:必须按长度加权 ───────────────────────────────────────

def test_dominant_directions_weighs_by_length_not_count():
    """轴线长而少,构件轮廓短而多。按条数统计会被噪声压过去。"""
    segs = [_v(500)] * 2                            # 2 条长竖线(各 1900pt)
    segs += [_rot(1000, 1000, 40, 30)] * 30         # 30 条短斜线(各 40pt)
    got = dominant_directions(segs)
    assert got[0]["angle_deg"] == 90.0              # 长度胜出


def test_dominant_directions_ranks_real_axes_above_short_noise():
    """长度加权后,成批短噪声仍可能上榜,但必排在真轴线之后。

    不能靠长度门槛去噪——点划线的划只有 10.5pt,门槛一高就把虚线轴线一起
    挡掉了(这是踩过的坑)。去噪由后续的**线型判据**承担。
    """
    segs = [_v(500), _h(500)] + [_rot(100, 100, 5, 17)] * 200
    got = dominant_directions(segs)
    assert {got[0]["angle_deg"], got[1]["angle_deg"]} == {90.0, 0.0}


def test_dominant_directions_finds_rotated_family():
    """实测 A-01-04A 有一整套旋转 43° 的正交轴网,必须能识别出来。"""
    segs = [_v(x) for x in (500, 600, 700)]
    segs += [_rot(1500, 1200, 1500, 43) for _ in range(4)]
    angles = {d["angle_deg"] for d in dominant_directions(segs)}
    assert 90.0 in angles
    assert any(abs(a - 43.0) <= 2.0 for a in angles)


def test_dominant_directions_on_empty_input():
    assert dominant_directions([]) == []


# ── 正交配对 ────────────────────────────────────────────────────

def test_orthogonal_families_pairs_43_and_133():
    dirs = [{"angle_deg": 43.0, "length": 9618.0, "share": 0.1},
            {"angle_deg": 133.0, "length": 8682.0, "share": 0.09}]
    fams = orthogonal_families(dirs)
    assert len(fams) == 1
    assert fams[0]["angles"] == [43.0, 133.0] and fams[0]["paired"] is True


def test_orthogonal_families_separates_two_grid_systems():
    """主系统 0/90 与副系统 43/133 是两套轴网,不能混成一套。"""
    dirs = [{"angle_deg": 0.0, "length": 55266.0, "share": 0.5},
            {"angle_deg": 90.0, "length": 53175.0, "share": 0.48},
            {"angle_deg": 43.0, "length": 9618.0, "share": 0.09},
            {"angle_deg": 133.0, "length": 8682.0, "share": 0.08}]
    fams = orthogonal_families(dirs)
    assert len(fams) == 2
    assert fams[0]["angles"] == [0.0, 90.0]          # 长度大的排前
    assert fams[1]["angles"] == [43.0, 133.0]


def test_orthogonal_families_keeps_unpaired_direction_flagged():
    fams = orthogonal_families([{"angle_deg": 0.0, "length": 100.0, "share": 1.0}])
    assert fams[0]["paired"] is False


# ── 双闸:跨度 + 覆盖率 ─────────────────────────────────────────

def test_axes_in_direction_finds_dash_dot_axes():
    """按 GB/T 50001 §8.0.1,轴线是**单点长画线**。

    法向偏移带符号(法向 = (-sinθ, cosθ)),竖向轴线的偏移是 -x;
    偏移只在同方向内用于聚类与排序,跨方向不可比。
    """
    segs = [s for x in (500, 900, 1300) for s in _dash_dot_v(x)]
    got = axes_in_direction(segs, 90.0, page_w=PAGE_W, page_h=PAGE_H)
    assert sorted(abs(a["offset_pt"]) for a in got) == [500.0, 900.0, 1300.0]


def test_axes_in_direction_rejects_solid_lines():
    """实线是构件轮廓/图框,**不是**轴线。

    这条曾是最大的错源:我按「覆盖率越高越像轴线」筛,而实线覆盖率恰是 1.0,
    方向正好反了——竖向检出 187 条(真值 36)。
    """
    assert axes_in_direction([_v(500), _v(900), _v(1300)], 90.0,
                             page_w=PAGE_W, page_h=PAGE_H) == []


def test_axes_in_direction_merges_dash_dot_segments():
    """点划线碎成多段,须并成一条;覆盖率落在实测区间 0.3~0.9。"""
    dashed = _dash_dot_v(700.0)
    got = axes_in_direction(dashed, 90.0, page_w=PAGE_W, page_h=PAGE_H)
    assert len(got) == 1
    assert got[0]["segments"] == len(dashed)
    assert 0.3 < got[0]["coverage"] < 0.9


def test_axes_in_direction_rejects_scattered_noise():
    """散落短段偶然共线,覆盖率极低(实测 0.01~0.05),必须挡住。"""
    noise = [(800.0, 200.0, 800.0, 210.0), (800.0, 2000.0, 800.0, 2010.0)]
    assert axes_in_direction(noise, 90.0, page_w=PAGE_W, page_h=PAGE_H) == []


def test_axes_in_direction_grid_constraint_is_opt_in():
    """成网约束是轴网级的,默认不生效——本函数只负责找该方向的点划线。"""
    segs = [s for x in (500, 900) for s in _dash_dot_v(x)]
    assert len(axes_in_direction(segs, 90.0, page_w=PAGE_W, page_h=PAGE_H)) == 2
    # 显式要求成网(≥3 条同区平行线)时,只有 2 条的那区被剔除
    assert axes_in_direction(segs, 90.0, page_w=PAGE_W, page_h=PAGE_H,
                             min_zone_members=3) == []


def test_axes_in_direction_keeps_a_proper_grid_family():
    segs = [s for x in (500, 900, 1300, 1700) for s in _dash_dot_v(x)]
    got = axes_in_direction(segs, 90.0, page_w=PAGE_W, page_h=PAGE_H)
    assert sorted(abs(a["offset_pt"]) for a in got) == [500.0, 900.0, 1300.0, 1700.0]


def test_axes_in_direction_keeps_local_subgrid():
    """实测教训:旋转子轴网只占图面一角,单线 600~900pt。按整页 25% 算门槛
    (842pt)会把它整体挡掉,我因此误判「43° 是斜撑不是轴网」。"""
    sub = [s for i in range(5)
           for s in _dash_dot_v(1500.0 + i * 200, 600.0, 1400.0)]
    got = axes_in_direction(sub, 90.0, page_w=PAGE_W, page_h=PAGE_H)
    assert len(got) == 5          # 800pt 跨度 < 整页 25%,但彼此相当,应全留


def test_axes_in_direction_only_uses_matching_angle():
    segs = _dash_dot_v(500) + _dash_dot_h(500)
    got = axes_in_direction(segs, 90.0, page_w=PAGE_W, page_h=PAGE_H)
    assert len(got) == 1 and abs(got[0]["offset_pt"]) == 500.0


def test_axes_in_direction_works_for_rotated_family():
    """旋转 43° 的一族平行点划线轴线,同样要能提出来。"""
    segs = [s for i in range(4)
            for s in _dash_dot_rot(1500 + i * 200, 1200, 1800, 43)]
    got = axes_in_direction(segs, 43.0, page_w=PAGE_W, page_h=PAGE_H)
    assert len(got) == 4
    offs = [a["offset_pt"] for a in got]
    assert all(b > a for a, b in zip(offs, offs[1:]))    # 单调,说明法向投影对了


# ── 间距模数 ────────────────────────────────────────────────────

def test_modulus_score_recognizes_regular_grid():
    """实测真柱网间距 68.8/68.8/68.9,应判为高规律性。"""
    offs = [100.0, 168.8, 237.6, 306.5, 375.3]
    got = modulus_score(offs)
    assert got["ratio"] == 1.0
    assert abs(got["base"] - 68.8) < 0.5


def test_modulus_score_accepts_integer_multiples():
    """轴网常有跳号(某跨是两倍模数),整数倍也算符合。"""
    offs = [0.0, 60.0, 120.0, 240.0, 300.0]
    assert modulus_score(offs)["ratio"] == 1.0


def test_modulus_score_low_for_irregular_noise():
    offs = [0.0, 2.4, 5.4, 8.7, 100.0, 233.7]
    assert modulus_score(offs)["ratio"] < 1.0


def test_modulus_score_needs_enough_points():
    assert modulus_score([0.0, 10.0])["base"] is None


# ── 归一化输出 ──────────────────────────────────────────────────

def test_to_normalized_line_matches_page_height_convention():
    """竖线:x 恒定、y 从 lo 到 hi。偏移带符号,故用 -500 表示 x=+500。"""
    axis = {"offset_pt": -500.0, "along_lo": 200.0, "along_hi": 2100.0}
    line = to_normalized_line(axis, 90.0, PAGE_H)
    assert abs(line["x1_norm"] - 500.0 / PAGE_H) < 1e-6
    assert abs(line["x2_norm"] - 500.0 / PAGE_H) < 1e-6
    assert abs(line["y1_norm"] - 200.0 / PAGE_H) < 1e-6


def test_to_normalized_line_round_trips_rotated_axis():
    """法向修正后,旋转轴线的重建必须落回原位——这是之前彻底错掉的部分。"""
    from core.model3d.vector_axis_extractor import _normal_offset, axes_in_direction
    segs = _dash_dot_rot(1600.0, 1200.0, 1400.0, 43.0)
    got = axes_in_direction(segs, 43.0, page_w=PAGE_W, page_h=PAGE_H)
    assert len(got) == 1
    expected = _normal_offset(1600.0, 1200.0, 43.0)
    assert abs(got[0]["offset_pt"] - expected) < 1.0


def test_to_normalized_line_preserves_rotated_angle():
    from services.axis_geometry import line_angle_deg
    axis = {"offset_pt": 800.0, "along_lo": 100.0, "along_hi": 1900.0}
    line = to_normalized_line(axis, 43.0, PAGE_H)
    assert abs(line_angle_deg(line) - 43.0) < 0.1


# ── 端到端(纯计算)+ 降级 ───────────────────────────────────────

def test_build_axes_separates_families_and_excludes_frame():
    segs = [_v(x) for x in (500, 900, 1300)] + [_h(y) for y in (600, 1000, 1400)]
    # 图框:贴页边且实线满覆盖
    segs += [(20.0, 20.0, 20.0, PAGE_H - 20), (20.0, 20.0, PAGE_W - 20, 20.0)]
    segs += [_rot(1600, 1200, 1600, 43) for _ in range(3)]
    got = build_axes(segs, PAGE_W, PAGE_H)
    fam_angles = [f["angles"] for f in got["families"]]
    assert [0.0, 90.0] in fam_angles
    # 图框那两条不该出现在轴线里
    all_offsets = [a["offset_pt"] for f in got["families"]
                   for g in f["groups"] for a in g["axes"]]
    assert 20.0 not in all_offsets


def test_build_axes_reports_modulus_per_group():
    segs = [s for i in range(5) for s in _dash_dot_v(500 + i * 68.8)]
    got = build_axes(segs, PAGE_W, PAGE_H)
    group = got["families"][0]["groups"][0]
    assert group["modulus"]["ratio"] == 1.0


def test_extract_vector_axes_degrades_on_non_pdf():
    got = extract_vector_axes(b"not a pdf")
    assert got == {"directions": [], "families": [], "segments": 0}
