"""轴网识别编排单测。

**为什么要有编排层**:Phase I 的各块此前只在一次性脚本里串起来跑过,
结果没有落点——分区号靠脚本硬编、粗错只打印在 stdout。要进产品就得有
一个可测、可重跑、把三样人工确认项如实交出来的编排。

**可测性设计**:OCR 由调用方注入(`read_text`),于是整条链在离线环境下
完全可测,不依赖 RapidOCR 是否装得上。
"""
import math

import pytest

from services.axis_recognition import (
    ZONE_LABEL_PENDING, recognize, summarize,
)

PAGE_W, PAGE_H = 3370.0, 2384.0
DIAMETER = 28.0


def _circle(cx, cy):
    return {"cx": cx, "cy": cy, "diameter_pt": DIAMETER}


def _row(n, y, x0, step):
    return [_circle(x0 + i * step, y) for i in range(n)]


def _col(n, x, y0, step):
    return [_circle(x, y0 + i * step) for i in range(n)]


#: 一个最小但完整的正交分区:6 条竖向 + 4 条横向。
#: 行列必须**紧贴**(§8.0.2 编号注写在下方及左侧)——间隙比超过 25% 就不同区,
#: 这正是判据要挡的跨区错配,合成数据也得守。
#: 行在网格下方、列在左侧,**不共用角点圈**——共点会被贪心归到大带里。
CIRCLES = _row(6, 2100.0, 1000.0, 80.0) + _col(6, 940.0, 1800.0, 50.0)

#: 竖向轴线 x ∈ {1000,1080,…,1400};横向 y ∈ {1800,1850,…,2050}
#: 字母自下而上:y=2050→A, 2000→B, 1950→C, 1900→D, 1850→E, 1800→F


def _leader(tip_x, tip_y):
    """构造一条引线:水平段 + 斜段,末端落在 (tip_x, tip_y)。"""
    joint = (tip_x - 45.0, tip_y - 45.0)
    text = (joint[0] - 94.0, joint[1])
    return [(text[0], text[1], joint[0], joint[1]),
            (joint[0], joint[1], tip_x, tip_y)]


def _fake_ocr(mapping):
    """按文字锚点位置返回预设 token(键取整数坐标)。"""
    def read(leader):
        a = leader["text_anchor"]
        return mapping.get((round(a[0]), round(a[1])), [])
    return read


# ── 基本产出 ──────────────────────────────────────────────────

def test_recognizes_zones_axes_and_labels():
    got = recognize(CIRCLES, strokes=[], segments=[],
                    page_w=PAGE_W, page_h=PAGE_H, read_text=lambda leader: [])
    assert got["circle_count"] == 12
    assert len(got["zones"]) == 1
    assert got["axis_count"] == 12
    labels = {a["label"] for a in got["axes"]}
    assert "1" in labels and "A" in labels


def test_zone_label_is_pending_until_a_human_confirms():
    """§8.0.5 未规定哪个分区是 1,几何推不出 —— 必须留空等人工。"""
    got = recognize(CIRCLES, strokes=[], segments=[],
                    page_w=PAGE_W, page_h=PAGE_H, read_text=lambda leader: [])
    assert got["zones"][0]["zone_label"] is ZONE_LABEL_PENDING is None
    # 未确认分区号时不加前缀 —— 不能瞎猜一个
    assert all("-" not in a["label"] for a in got["axes"])


def test_confirmed_zone_labels_are_applied_to_the_axis_names():
    got = recognize(CIRCLES, strokes=[], segments=[],
                    page_w=PAGE_W, page_h=PAGE_H, read_text=lambda leader: [],
                    zone_labels={0: "2"})
    assert got["zones"][0]["zone_label"] == "2"
    assert all(a["label"].startswith("2-") for a in got["axes"])


def test_additional_axes_are_excluded_from_the_main_sequence():
    """§8.0.6 分数式附加轴线不占主序号,否则其后轴号整体偏移。"""
    extra = _circle(1480.0, 2100.0)
    slash = _slash_stroke(extra)
    got = recognize(CIRCLES + [extra], strokes=[slash], segments=[],
                    page_w=PAGE_W, page_h=PAGE_H, read_text=lambda leader: [])
    assert got["additional_count"] == 1
    assert got["axis_count"] == 12          # 附加轴线不进主序列


def _slash_stroke(circle):
    length = circle["diameter_pt"] * 0.475
    rad = math.radians(70.0)
    dx, dy = math.cos(rad) * length / 2, math.sin(rad) * length / 2
    return (circle["cx"] - dx, circle["cy"] + dy,
            circle["cx"] + dx, circle["cy"] - dy)


# ── 坐标锚点与粗错 ────────────────────────────────────────────

def test_produces_world_anchors_from_coordinate_leaders():
    """引线末端落在轴号对交点上 → 一条可用锚点。"""
    tip = (1000.0, 1900.0)                  # 1 号竖轴 × D 号横轴
    segs = _leader(*tip)
    anchor_text = (tip[0] - 45.0 - 94.0, tip[1] - 45.0)
    ocr = _fake_ocr({(round(anchor_text[0]), round(anchor_text[1])):
                     ["X=-6100.000", "Y=-100.000"]})
    got = recognize(CIRCLES, strokes=[], segments=segs,
                    page_w=PAGE_W, page_h=PAGE_H, read_text=ocr,
                    zone_labels={0: "1"})
    assert got["leader_count"] == 1
    assert len(got["anchors"]) == 1
    a = got["anchors"][0]
    assert (a["label_x"], a["label_y"]) == ("1-1", "1-D")


def test_gross_errors_are_surfaced_not_swallowed():
    """RANSAC 判出的粗错必须出现在 outliers 里交人工 —— 不能悄悄丢掉。"""
    tips = [(1000.0, 1900.0), (1160.0, 1900.0), (1320.0, 1900.0),
            (1000.0, 2000.0)]
    segs, mapping = [], {}
    good = {(1000.0, 1900.0): (-6100.0, -100.0),
            (1160.0, 1900.0): (-6080.0, -100.0),
            (1320.0, 1900.0): (-6060.0, -100.0),
            (1000.0, 2000.0): (-6100.0, -120.0)}
    for i, tip in enumerate(tips):
        segs += _leader(*tip)
        wx, wy = good[tip]
        if i == 3:
            wy = -1.0                        # 粗错:OCR 把 -120 读成 -1
        anchor = (tip[0] - 139.0, tip[1] - 45.0)
        mapping[(round(anchor[0]), round(anchor[1]))] = [
            f"X={wx:.3f}", f"Y={wy:.3f}"]
    got = recognize(CIRCLES, strokes=[], segments=segs,
                    page_w=PAGE_W, page_h=PAGE_H, read_text=_fake_ocr(mapping),
                    zone_labels={0: "1"})
    assert got["outliers"], "粗错必须报出来"
    assert all(o.get("world") for o in got["outliers"])
    # 粗错不得进入锚点
    assert len(got["anchors"]) < got["leader_count"]


def test_no_leaders_yields_no_anchors_and_no_transform():
    got = recognize(CIRCLES, strokes=[], segments=[],
                    page_w=PAGE_W, page_h=PAGE_H, read_text=lambda leader: [])
    assert got["anchors"] == [] and got["outliers"] == []
    assert got["transform"] is None


# ── 国标校验 ──────────────────────────────────────────────────

def test_violations_are_reported_alongside_the_result():
    got = recognize(CIRCLES, strokes=[], segments=[],
                    page_w=PAGE_W, page_h=PAGE_H, read_text=lambda leader: [])
    assert got["violations"] == []          # 推导序列本身合规


def test_summary_counts_what_needs_human_attention():
    """摘要要直接回答「有多少事等我处理」。

    单分区图**不算待办**(§8.0.5 分区编号只在多分区时才用),
    所以这里用多分区夹具才测得到「等人确认」。
    """
    got = recognize(CIRCLES_TWO_ZONES, strokes=[], segments=[],
                    page_w=PAGE_W, page_h=PAGE_H, read_text=lambda leader: [])
    s = summarize(got)
    assert s["zones_pending_label"] == len(got["zones"]) > 1
    assert s["outliers"] == 0 and s["violations"] == 0


def test_summary_after_confirmation_has_nothing_pending():
    got = recognize(CIRCLES, strokes=[], segments=[],
                    page_w=PAGE_W, page_h=PAGE_H, read_text=lambda leader: [],
                    zone_labels={0: "1"})
    assert summarize(got)["zones_pending_label"] == 0


# ── 降级 ──────────────────────────────────────────────────────

def test_empty_drawing_degrades_gracefully():
    got = recognize([], strokes=[], segments=[],
                    page_w=PAGE_W, page_h=PAGE_H, read_text=lambda leader: [])
    assert got["circle_count"] == 0 and got["zones"] == [] and got["axes"] == []
    assert got["transform"] is None


def test_zero_page_height_does_not_crash():
    got = recognize(CIRCLES, strokes=[], segments=[],
                    page_w=0.0, page_h=0.0, read_text=lambda leader: [])
    assert got["axes"] == []


def test_ocr_failure_does_not_break_the_whole_run():
    """OCR 抛异常时轴线部分仍要出结果 —— 坐标读不到不该拖垮识别。"""
    def broken(_leader):
        raise RuntimeError("ocr down")

    got = recognize(CIRCLES, strokes=[], segments=_leader(1000.0, 1900.0),
                    page_w=PAGE_W, page_h=PAGE_H, read_text=broken)
    assert got["axis_count"] == 12
    assert got["anchors"] == []
    assert any("ocr" in w.lower() for w in got["warnings"])


def test_result_does_not_mutate_input_circles():
    before = [dict(c) for c in CIRCLES]
    recognize(CIRCLES, strokes=[], segments=[], page_w=PAGE_W, page_h=PAGE_H,
              read_text=lambda leader: [])
    assert [dict(c) for c in CIRCLES] == before


#: 第二个分区,离 CIRCLES 足够远(不相邻 → 不会被并成一个区)。
CIRCLES_TWO_ZONES = CIRCLES + _row(6, 1100.0, 2400.0, 80.0) \
                            + _col(6, 2340.0, 800.0, 50.0)


def test_anchors_need_a_confirmed_zone_label():
    """锚点身份是轴号对;分区号未确认时轴号是裸标签,两个分区会撞身份。

    实测:未确认时锚点从 8 掉到 7 —— 被去重吃掉一个。
    宁可不写,也不能写一个会串图的错身份。

    **必须用多分区夹具**:此前这条用的是单分区的 CIRCLES,
    而单分区图根本不存在身份冲突 —— 断言成立但理由是错的。
    """
    tip = (1000.0, 1900.0)
    anchor_text = (tip[0] - 139.0, tip[1] - 45.0)
    ocr = _fake_ocr({(round(anchor_text[0]), round(anchor_text[1])):
                     ["X=-6100.000", "Y=-100.000"]})
    kw = dict(strokes=[], segments=_leader(*tip), page_w=PAGE_W,
              page_h=PAGE_H, read_text=ocr)
    got = recognize(CIRCLES_TWO_ZONES, **kw)
    assert len(got["zones"]) > 1, "夹具必须是多分区,否则测不到身份冲突"
    assert got["anchors"] == []                  # 没身份就不写
    # 引线本身照常检出,只是不产出锚点
    assert got["leader_count"] == 1

    confirmed = recognize(CIRCLES_TWO_ZONES, **kw,
                          zone_labels={i: str(i + 1)
                                       for i in range(len(got["zones"]))})
    assert len(confirmed["anchors"]) == 1


def test_single_zone_needs_no_human_confirmation():
    """§8.0.5 的分区编号**只在多分区时才用**。

    一张图只有一个分区时,轴号 `1` 就是 `1`,不存在 `1-1` vs `2-1` 撞车,
    人工确认无信息可加 —— 再要求确认就是白等。

    实测:全项目 1484 张有轴网的图里 399 张是单分区,
    其中 302 张已有坐标变换 —— 这道闸放开,轴网入模从 1 张变 302 张。
    """
    got = recognize(CIRCLES, strokes=[], segments=[], page_w=PAGE_W,
                    page_h=PAGE_H, read_text=lambda leader: [])
    assert len(got["zones"]) == 1
    assert all(a["zone_label_confirmed"] for a in got["axes"])
    assert got["zones"][0]["needs_confirmation"] is False
    # 但轴号仍**不加前缀** —— 单分区图本来就不该有分区号
    assert all("-" not in a["label"] for a in got["axes"])
    assert summarize(got)["zones_pending_label"] == 0


def test_multi_zone_still_needs_confirmation():
    """多分区才是 §8.0.5 的适用场景,必须等人工。"""
    got = recognize(CIRCLES_TWO_ZONES, strokes=[], segments=[],
                    page_w=PAGE_W, page_h=PAGE_H, read_text=lambda leader: [])
    assert len(got["zones"]) > 1
    assert not any(a["zone_label_confirmed"] for a in got["axes"])
    assert all(z["needs_confirmation"] for z in got["zones"])
    assert summarize(got)["zones_pending_label"] == len(got["zones"])


def test_single_zone_axes_reach_the_scene():
    """解闸的落点:单分区图的轴线要能进 3D 场景。"""
    from services.axis_recognition import axes_to_scene
    got = recognize(CIRCLES, strokes=[], segments=[], page_w=PAGE_W,
                    page_h=PAGE_H, read_text=lambda leader: [])
    scene = axes_to_scene(got["axes"], _transform())
    assert scene["x"] and scene["y"]


# ── 识别结果 → 3D 场景轴网 ────────────────────────────────────────

def _transform():
    from services.drawing_transform import DrawingTransform
    return DrawingTransform(scale_m_pt=0.1, origin_x=100.0, origin_y=50.0,
                            page_h=PAGE_H)


def test_scene_axes_split_numeric_to_x_and_alpha_to_y():
    """与 archive_axes_to_scene 同契约:x 是竖轴(数字)、y 是横轴(字母)。"""
    from services.axis_recognition import axes_to_scene

    axes = [{"label": "1-1", "angle_deg": 90.0, "offset_pt": -1000.0,
             "zone_label_confirmed": True},
            {"label": "1-A", "angle_deg": 0.0, "offset_pt": 1600.0,
             "zone_label_confirmed": True}]
    got = axes_to_scene(axes, _transform())
    assert [a[0] for a in got["x"]] == ["1-1"]
    assert [a[0] for a in got["y"]] == ["1-A"]


def test_scene_axis_position_matches_pt_to_meter():
    from services.axis_recognition import axes_to_scene
    from services.drawing_transform import pt_to_meter

    t = _transform()
    got = axes_to_scene([{"label": "1-1", "angle_deg": 90.0,
                          "offset_pt": -1000.0,
                          "zone_label_confirmed": True}], t)
    assert got["x"][0][1] == pytest.approx(pt_to_meter(1000.0, 0.0, t)[0])


def test_rotated_axes_are_skipped_not_projected():
    """scene 轴网是一维位置,表达不了斜轴 —— 硬投影会在模型里指向别处。"""
    from services.axis_recognition import axes_to_scene

    got = axes_to_scene([{"label": "3-1", "angle_deg": 132.0,
                          "offset_pt": -1600.0,
                          "zone_label_confirmed": True}], _transform())
    assert got == {"x": [], "y": []}


def test_scene_axes_skip_unlabelled():
    from services.axis_recognition import axes_to_scene

    got = axes_to_scene([{"label": "", "angle_deg": 90.0, "offset_pt": -10.0,
                          "zone_label_confirmed": True}], _transform())
    assert got == {"x": [], "y": []}


def test_scene_axes_without_transform_degrade_to_empty():
    from services.axis_recognition import axes_to_scene

    assert axes_to_scene([{"label": "1", "angle_deg": 90.0, "offset_pt": 0.0,
                           "zone_label_confirmed": True}],
                         None) == {"x": [], "y": []}


def test_scene_axes_skip_unconfirmed_zones():
    """分区号未确认时三个分区各自从 1 开始,scene 里会出现重复的 `1`。

    `_merge_axes` 会拿带标签的去升级无标签的 —— 歧义标签会污染别的轴线。
    与世界锚点同一条规则:标签就是身份,没有分区号就不给标签。
    """
    from services.axis_recognition import axes_to_scene

    got = axes_to_scene([{"label": "1", "angle_deg": 90.0, "offset_pt": -1000.0,
                          "zone_label_confirmed": False}], _transform())
    assert got == {"x": [], "y": []}


# ── 落库:JSON null 与 SQL NULL 不是一回事 ────────────────────────

def test_absent_json_fields_bind_sql_null_not_json_null():
    """`json.dumps(None)` 是字符串 "null",CAST 成 jsonb 后是 **JSON null**,
    于是 `transform IS NOT NULL` 对所有行都成立。

    实测全项目 2309 行里 2187 行是这种假非空,真有变换的只有 122 张。
    """
    import asyncio

    from services.axis_recognition_repo import save_result

    captured = {}

    class FakeDb:
        async def execute(self, _sql, params):
            captured.update(params)

    result = {"page_w": 1.0, "page_h": 2.0, "circle_count": 0,
              "additional_count": 0, "axis_count": 0,
              "zones": [], "axes": [], "anchors": [], "outliers": [],
              "violations": [], "transform": None}
    asyncio.run(save_result(FakeDb(), project_id="p", drawing_id="d",
                            result=result))
    assert captured["transform"] is None          # SQL NULL
    assert captured["zones"] == "[]"              # 空列表照常序列化


def test_band_cap_is_calibrated_from_real_drawings():
    """带数阈值必须离**真轴网的实测上限**足够远。

    **这条断言的旧版本是错的**:它写「真定位图只有 10~13 条带」,
    据此把闸设在 40 并**直接拦截**。后来实测
    `A-10-04C 一层完整平面图` 有 **42 条带** —— 只超 2 条就被整批丢弃,
    整层轴线全丢,而它是全项目最核心的一张平面图。

    「10~13 条带」这个前提来自三张**专用定位图**,不代表大型完整平面图。

    现在阈值只用于**打标记**,不再拦截(见 SYMBOL_FIELD_BAND_HINT),
    且必须高于实测真轴网上限 42。
    """
    from services.axis_recognition import SYMBOL_FIELD_BAND_HINT

    measured_real_grid_max = 42      # A-10-04C 一层完整平面图
    measured_symbol_field = 200      # P-21-09C 喷淋平面图
    assert measured_real_grid_max < SYMBOL_FIELD_BAND_HINT < measured_symbol_field


def test_band_cap_blind_spot_is_documented():
    """**局限如实说明**:闸的判别力来自真实图纸的杂乱,不是「圈多」。

    一个**均匀**的设备方阵会被带检测并成少数长带 —— 实测 1600 个圈只得
    17 条带,过不了闸也挡不住,仍会产出垃圾轴线。真实喷淋按房间/管路分段
    布置才产生 199 条带。挡不住的部分交给下游的分区配对与国标校验,
    而不是把阈值调低去误伤真轴网。
    """
    import random

    rng = random.Random(3)
    uniform_field = [_circle(200.0 + col * 60.0 + rng.uniform(-14, 14),
                             200.0 + row * 45.0 + rng.uniform(-14, 14))
                     for row in range(40) for col in range(40)]
    got = recognize(uniform_field, strokes=[], segments=[], page_w=PAGE_W,
                    page_h=PAGE_H, read_text=lambda leader: [])
    # 挡不住 —— 这是已知盲区,写成断言免得以后误以为已解决
    assert not any("设备符号场" in w for w in got["warnings"])
    assert got["axes"]


def test_a_normal_grid_is_below_the_band_cap():
    """正常轴网不该被这道闸误伤。"""
    got = recognize(CIRCLES, strokes=[], segments=[], page_w=PAGE_W,
                    page_h=PAGE_H, read_text=lambda leader: [])
    assert got["zones"] and not any("设备符号场" in w for w in got["warnings"])


# ── 一图多视图(分幅)────────────────────────────────────────────

@pytest.mark.unit
def test_split_view_is_flagged_and_needs_no_zone_label():
    """分幅不是 §8.0.5 分区 —— **不该要人工确认分区号**。

    实测 A-20-02A 南立面图有两幅立面(13/0 + 12/0,均单向),
    系统此前当成两个分区、各自要人工给分区号 —— 而分幅根本没有分区号。
    1083 张待确认里有一批是这种。
    """
    # 两条互不相邻的横带,各只标注竖向轴线(立面图形态)
    circles = _row(6, 600.0, 400.0, 90.0) + _row(5, 1600.0, 500.0, 90.0)
    got = recognize(circles, strokes=[], segments=[], page_w=PAGE_W,
                    page_h=PAGE_H, read_text=lambda leader: [])
    assert got["is_split_view"] is True
    assert all(not z["needs_confirmation"] for z in got["zones"]), \
        "分幅没有分区号可确认,不该挂在人工队列上"
    assert any("分幅" in w for w in got["warnings"])
    assert summarize(got)["zones_pending_label"] == 0


@pytest.mark.unit
def test_real_multi_zone_plan_is_not_flagged_as_split():
    """真分区(双向)不能被当成分幅。"""
    got = recognize(CIRCLES_TWO_ZONES, strokes=[], segments=[],
                    page_w=PAGE_W, page_h=PAGE_H, read_text=lambda leader: [])
    assert got["is_split_view"] is False
    assert all(z["needs_confirmation"] for z in got["zones"])


@pytest.mark.unit
def test_split_view_reports_the_continuous_numbering():
    """分幅要给出**串起来的连续编号**,供人核对与后续采用。"""
    circles = _row(6, 600.0, 400.0, 90.0) + _row(5, 1600.0, 500.0, 90.0)
    got = recognize(circles, strokes=[], segments=[], page_w=PAGE_W,
                    page_h=PAGE_H, read_text=lambda leader: [])
    plan = got["split_view_numbering"]
    assert len(plan) == 2
    assert plan[0]["start"] == 1
    # 第二幅接着第一幅(默认搭接 1 根)
    assert plan[1]["start"] == plan[0]["end"]


# ── 轴距过密也是符号场特征（实测 183 张假轴网）────────────────────

@pytest.mark.unit
def test_tiny_axis_gap_is_suspect_even_when_bands_are_few():
    """**带数判据漏掉了它们**：实测这批图带数没超 60，却检出几十条
    间距 0.29~1.45 米的「轴线」。

    对比同批图上的旧启发式（要求 ≥60% 页幅的长直线）——**一条都没检出**，
    即这些图本就没有贯通轴网，那些「圈」是桩位/设备符号。

    判据必须用**米**而非 pt：真值图 A-01-04A 约 4.9 米/轴距，
    误检图 0.29~1.45 米，能分开；而 pt 间距反而是真值图更密
    （99 条轴线 34pt vs 误检 66 条 51pt），分不开。
    """
    from services.axis_recognition import is_suspect_symbol_field

    assert is_suspect_symbol_field(30, gap_m=0.29) is True
    assert is_suspect_symbol_field(30, gap_m=1.45) is True


@pytest.mark.unit
def test_real_grid_gap_is_not_suspect():
    """真轴网不能被误标 —— 「判错的代价不该是丢掉整层轴线」。"""
    from services.axis_recognition import is_suspect_symbol_field

    assert is_suspect_symbol_field(30, gap_m=4.9) is False
    assert is_suspect_symbol_field(30, gap_m=8.0) is False


@pytest.mark.unit
def test_band_rule_still_applies():
    """带数判据保留 —— 新判据是**补充**，不是替换。"""
    from services.axis_recognition import is_suspect_symbol_field

    assert is_suspect_symbol_field(200) is True


@pytest.mark.unit
def test_unknown_gap_falls_back_to_bands():
    """算不出米轴距（无比例尺）时只用带数判 —— **判不出就说判不出**。"""
    from services.axis_recognition import is_suspect_symbol_field

    assert is_suspect_symbol_field(30, gap_m=None) is False


# --- §8.0.2 轴线两端可各注一个轴号 -----------------------------------

def _axis(label, offset, zone, kind="numeric", angle=90.0):
    return {"label": label, "offset_pt": offset, "zone_index": zone,
            "label_kind": kind, "angle_deg": angle, "circle_count": 1}


@pytest.mark.unit
def test_an_axis_labelled_at_both_ends_counts_once():
    """同一条轴线在上下两端各注一个轴号，是一条轴线不是两条。

    GB/T 50001 §8.0.2 允许轴号注写在轴线**两端**。识别器把两端的轴号带
    切成了两个分区，于是同一批轴线被数了两遍。

    **实测**（首层框架梁平面整体配筋图）：

        区0 轴号: 1@-542 2@-697 … 12@-2522   (图上边 y=227)
        区1 轴号: 1@-542 2@-697 … 12@-2522   (图下边 y=1275)
                       ↑ 偏移量完全相同

    该图识别 38 条，真实 22 条。整体重复率：大歌剧院 4%、轨道交通 **18%**，
    受影响图纸 17% / **40%**。
    """
    from services.axis_recognition import merge_both_end_labels

    axes = [_axis("1", -542.0, 0), _axis("2", -697.0, 0),
            _axis("1", -542.0, 1), _axis("2", -697.0, 1)]
    merged = merge_both_end_labels(axes)
    assert len(merged) == 2
    assert {a["label"] for a in merged} == {"1", "2"}
    assert merged[0]["circle_count"] == 2      # 两端的圈都记在这条轴线上


@pytest.mark.unit
def test_real_zones_with_the_same_label_are_not_merged():
    """真分区里两个「1 轴」在**不同位置**，不能合并。

    §8.0.5 的分区图上 `1-1` 与 `2-1` 是两条轴线 —— 偏移量不同即为证据。
    """
    from services.axis_recognition import merge_both_end_labels

    axes = [_axis("1", -542.0, 0), _axis("1", -1593.0, 1)]
    assert len(merge_both_end_labels(axes)) == 2


@pytest.mark.unit
def test_same_offset_but_different_direction_is_not_merged():
    """偏移量相同但方向不同，是两条互相垂直的轴线。"""
    from services.axis_recognition import merge_both_end_labels

    axes = [_axis("1", 525.0, 0, angle=90.0),
            _axis("A", 525.0, 1, kind="alpha", angle=0.0)]
    assert len(merge_both_end_labels(axes)) == 2
