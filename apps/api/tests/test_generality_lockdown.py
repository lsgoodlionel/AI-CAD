"""通用性锁定 —— 用**合成第二工程**验证判据不依赖大歌剧院特征。

用户复核口径:「只是局部针对某特定工程提高无用,需要通用性识图信息
获取建模能力的系统级能力锁定」。

本文件模拟一个与大歌剧院处处相反的工程:
- 工程坐标是**正值**城市坐标系(X≈40000,大歌剧院是 −6300);
- 单体命名是住宅式「A栋/B栋」(大歌剧院是「××厅」);
- 柱网 6 米(大歌剧院共识 8 米)。
每个核心判据都必须在这个假想工程上同样成立。
"""
from __future__ import annotations

import pytest


# ── ① 坐标系分组不得假定工程坐标为负 ─────────────────────────────

@pytest.mark.unit
def test_world_range_is_derived_from_the_projects_own_anchors():
    """**实测特化点**:WORLD_RANGE=(-100000,-1000) 写死了「工程坐标为负」
    —— 那是大歌剧院的坐标系特征。正值城市坐标系(X≈40000)的工程,
    分组判据会把世界坐标全判成局部。

    通用原则:工程坐标区间从**项目自己的锚点**推导,不写死。
    """
    from services.axes_validation import world_range_from_anchors

    got = world_range_from_anchors([40012.5, 40180.2, 39950.0])
    lo, hi = got
    assert lo < 39950.0 < 40180.2 < hi, "区间要包住锚点并留余量"


@pytest.mark.unit
def test_negative_coordinate_projects_still_work():
    """大歌剧院(负值)同样从数据推出来 —— 新老工程一个口径。"""
    from services.axes_validation import world_range_from_anchors

    lo, hi = world_range_from_anchors([-6326.0, -6065.0])
    assert lo < -6326.0 and hi > -6065.0


@pytest.mark.unit
def test_no_anchors_means_single_system():
    """没有锚点 ⇒ 没有世界坐标 ⇒ 一切按局部处理(判不出就说判不出)。"""
    from services.axes_validation import world_range_from_anchors

    assert world_range_from_anchors([]) is None
    assert world_range_from_anchors(None) is None


@pytest.mark.unit
def test_classification_accepts_a_dynamic_range():
    from services.axes_validation import SYSTEM_LOCAL, SYSTEM_WORLD, coordinate_system_of

    positive = (39000.0, 41000.0)
    assert coordinate_system_of(40012.5, world_range=positive) == SYSTEM_WORLD
    assert coordinate_system_of(120.0, world_range=positive) == SYSTEM_LOCAL
    # 不传区间 ⇒ 无世界坐标概念,一律局部
    assert coordinate_system_of(-6200.0, world_range=None) == SYSTEM_LOCAL


# ── ② 子单体发现不得依赖「厅/馆」后缀词表 ────────────────────────

@pytest.mark.unit
def test_sub_units_are_discovered_from_level_name_prefixes():
    """住宅式命名「A栋3F」同样发现得出 —— 前缀在标准层 token 前、
    跨图一致出现即成子单体,零后缀词表。"""
    from services.sub_unit_discovery import discover_sub_units

    names = ["A栋3F", "A栋4F", "B栋3F", "B栋B1", "3F", "机房层"]
    got = discover_sub_units(names)
    assert got == {"A栋", "B栋"}


@pytest.mark.unit
def test_venue_style_names_are_discovered_the_same_way():
    """大歌剧院的「××厅」走同一条路 —— 不是为它单写的规则。"""
    from services.sub_unit_discovery import discover_sub_units

    # 小歌剧厅带两个不同楼层(真实数据里它有 1F/2F/4F/屋顶层 四个);
    # 只带一个楼层的前缀不成单体 —— 与住宅用例同一条孤证不立原则。
    names = ["大歌剧厅3F", "大歌剧厅4F", "小歌剧厅2F", "小歌剧厅4F"]
    got = discover_sub_units(names)
    assert "大歌剧厅" in got and "小歌剧厅" in got


@pytest.mark.unit
def test_single_occurrence_prefix_is_not_a_unit():
    """只出现一次的前缀不成单体 —— 孤证不立,防把笔误学成单体。"""
    from services.sub_unit_discovery import discover_sub_units

    assert discover_sub_units(["会议室3F", "4F", "5F"]) == set()


@pytest.mark.unit
def test_plain_floor_names_yield_nothing():
    from services.sub_unit_discovery import discover_sub_units

    assert discover_sub_units(["1F", "2F", "B1", "地下一层"]) == set()


# ── ③ 既有判据在第二工程参数下成立 ───────────────────────────────

@pytest.mark.unit
def test_axis_gap_anomaly_works_on_a_6m_grid_project():
    """柱网 6 米的工程:6.2 米正常、0.3 米照样判噪声 —— 判据是
    「工程合理区间+偏离共识」,不锚定 8 米。"""
    from services.axis_gap_anomaly import detect_gap_anomaly

    assert detect_gap_anomaly("d", 6.2, consensus_m=6.0, samples=10) is None
    got = detect_gap_anomaly("d", 0.3, consensus_m=6.0, samples=10)
    assert got and "噪声" in got["likely_cause"]


@pytest.mark.unit
def test_alias_learning_works_for_residential_naming():
    """图名共现学习对「A栋→1区」同样成立 —— 机制无词表。"""
    from services.level_elevation_consensus import learn_unit_aliases

    titled = [("1区A栋三层平面图", "zone1")] * 3 + [("2区B栋平面图", "zone2")] * 2
    got = learn_unit_aliases({"A栋", "B栋"}, titled)
    assert got == {"A栋": "zone1", "B栋": "zone2"}


@pytest.mark.unit
def test_equipment_codes_are_not_sub_units():
    """**实测垃圾**:AHU(空调机组)/AP\\/C(配电箱)/「:」被当成子单体 ——
    层 token 正则松到连裸数字都匹配,「AHU3」拆成 AHU+3。

    收紧仍是**结构约束**(零词表):层 token 必须是真楼层形态
    (1~2 位数字且带 F/层、B 层、中文层、屋面等),前缀须含中文、不含符号。
    """
    from services.sub_unit_discovery import discover_sub_units

    garbage = ["AHU3", "AHU4", "AP/C1", "AP/C2", ":3", ":5",
               "ANT-1F", "ANT-2F"]
    assert discover_sub_units(garbage) == set()


@pytest.mark.unit
def test_bare_digits_are_not_floor_tokens():
    from services.sub_unit_discovery import split_level_prefix

    assert split_level_prefix("AHU3") == (None, "AHU3")
    assert split_level_prefix("大歌剧厅3F") == ("大歌剧厅", "3F")


# ── 第二工程(轨道交通调度大楼)实测:楼层表达被误拆成幽灵单体 ────────

@pytest.mark.unit
def test_negative_floor_is_not_a_unit():
    """**实测幽灵单体「负」**(17 例):`负3F` 是「地下三层」的写法,
    `\\d{1,2}F` 从索引 1 匹配,把「负」剩成了前缀。

    它通过了所有既有闸门(含中文✓、无禁用标点✓、≥2 个不同楼层✓)——
    **闸门再多也挡不住 token 本身划错边界**。
    """
    from services.sub_unit_discovery import discover_sub_units, split_level_prefix

    assert split_level_prefix("负3F隔墙图") == (None, "负3F隔墙图")
    assert discover_sub_units(["负3F隔墙图", "负2F平顶尺寸图",
                               "负1F综合平顶图"]) == set()


@pytest.mark.unit
def test_roof_top_structure_is_not_a_unit():
    """**实测幽灵单体「出」**(2 例):「出屋面」是完整楼层表达
    (屋面以上构筑物层),不是「出」+「屋面」。"""
    from services.sub_unit_discovery import discover_sub_units, split_level_prefix

    assert split_level_prefix("出屋面结构平面图")[0] is None
    assert discover_sub_units(["出屋面结构平面图",
                               "出屋面梁、幕墙构架平面整体配筋图"]) == set()


@pytest.mark.unit
def test_a_single_building_yields_no_sub_units():
    """**单体建筑就该发现 0 个子单体** —— 这才是正确答案。

    轨道交通调度大楼是 9 层 + B3 的单体办公楼,此前却「发现」了
    「负」「出」两个,全是假的。
    """
    from services.sub_unit_discovery import discover_sub_units

    names = ["负3F隔墙图", "负2F平顶尺寸图", "负1F综合平顶图",
             "出屋面结构平面图", "3F平面布置图", "7F铝板收口节点图"]
    assert discover_sub_units(names) == set()


@pytest.mark.unit
def test_real_units_survive_the_stricter_token():
    """收紧不得误伤真单体 —— 大歌剧院的三个厅仍要发现得出。"""
    from services.sub_unit_discovery import discover_sub_units

    got = discover_sub_units(["大歌剧厅3F", "大歌剧厅4F",
                              "小歌剧厅2F", "小歌剧厅4F"])
    assert got == {"大歌剧厅", "小歌剧厅"}


# ── 第二工程实测:剖切**手法** vs 详图**图种** ──────────────────

@pytest.mark.unit
def test_node_detail_drawn_in_section_is_a_detail():
    """**实测 310 张**:幕墙「横剖节点详图」被判成 section。

    区别是实质性的:**剖面图**表达整栋的层高/标高关系(建模靠它恢复 z),
    **节点详图**是局部构造,用剖切方式画而已。误判会让 section-z
    拿幕墙节点去找楼层标高。

    通用判据:「详图/大样/节点」是**显式图种声明**,
    「剖」只是**表达手法** —— 共现时图种声明优先。
    """
    from services.drawing_filename_parser import (
        VIEW_TYPE_DETAIL, match_view_type_keyword,
    )

    for title in ("C1玻璃幕墙横剖节点详图", "C6明框幕墙横剖节点图",
                  "地面通用节点大样图", "竖剖节点大样"):
        hit = match_view_type_keyword(title)
        assert hit and hit.view_type == VIEW_TYPE_DETAIL, title


@pytest.mark.unit
def test_a_real_section_is_still_a_section():
    """**不得误伤真剖面** —— 大歌剧院靠剖面图恢复标高(13 个候选)。"""
    from services.drawing_filename_parser import (
        VIEW_TYPE_SECTION, match_view_type_keyword,
    )

    for title in ("基坑支护剖面图（三）", "1-1剖面图", "建筑剖面图",
                  "地下连续墙配筋剖面图（二）"):
        hit = match_view_type_keyword(title)
        assert hit and hit.view_type == VIEW_TYPE_SECTION, title


@pytest.mark.unit
def test_plan_detail_is_also_a_detail():
    """同理:平面词与详图词共现时也归详图。"""
    from services.drawing_filename_parser import (
        VIEW_TYPE_DETAIL, match_view_type_keyword,
    )

    hit = match_view_type_keyword("卫生间平面大样图")
    assert hit and hit.view_type == VIEW_TYPE_DETAIL


# ── 楼层骨架的识别不该依赖「完整」二字 ──────────────────────────

@pytest.mark.unit
def test_architectural_floor_plan_is_a_floor_skeleton():
    """**实测缺陷**:第二工程建筑专业有 11 张「N层平面图」,
    是楼层骨架却全判成 `component_source`。

    原规则是 `完整平面图|平面总图|整体平面` —— 「完整平面图」是
    **大歌剧院这家院的措辞**,轨道交通就叫「六层平面图」。

    通用判据取自国标专业分工:**建筑专业的楼层平面图定义楼层轮廓与房间**,
    是骨架;结构/机电的平面图表达构件与管线,是构件来源。
    """
    from services.drawing_role import ROLE_FLOOR_SKELETON, classify_role

    for title in ("六层平面图", "2F平面布置图", "首层平面图"):
        got = classify_role(
            {"title": title, "discipline": "architecture"})
        assert got.role == ROLE_FLOOR_SKELETON, title


@pytest.mark.unit
def test_structural_floor_plan_is_a_component_source():
    """**不得把结构平面图也升级** —— 它表达构件,不定义楼层轮廓。"""
    from services.drawing_role import ROLE_COMPONENT_SOURCE, classify_role

    got = classify_role(
        {"title": "六层梁配筋平面图", "discipline": "structure"})
    assert got.role == ROLE_COMPONENT_SOURCE


@pytest.mark.unit
def test_the_original_phrase_still_works():
    """大歌剧院的「完整平面图」措辞不受影响。"""
    from services.drawing_role import ROLE_FLOOR_SKELETON, classify_role

    got = classify_role(
        {"title": "地下一层完整平面图", "discipline": "architecture"})
    assert got.role == ROLE_FLOOR_SKELETON


@pytest.mark.unit
def test_decoration_plan_is_not_a_skeleton():
    """装饰/幕墙的平面布置图也不是楼层骨架(实测各有 9、8 张)。"""
    from services.drawing_role import ROLE_COMPONENT_SOURCE, classify_role

    got = classify_role(
        {"title": "2F平面布置图", "discipline": "decoration"})
    assert got.role == ROLE_COMPONENT_SOURCE


# ── 详图的轴线不参与装配(第二工程实测 5 张)──────────────────────

@pytest.mark.unit
def test_detail_drawings_do_not_contribute_axes():
    """**实测**:5 张详图产出 7~35 条轴线,全部未被符号场判据拦下。

    `钢立柱及立柱桩详图` 的 21 个「圈」显然是**桩位**,
    但轴距判据(绝对尺度 <2 米)在详图上失效 ——
    **详图比例尺比平面图大一个量级**(1:20 vs 1:100),
    同样的图上距离换算出的米数完全不同。

    更根本的判据是国标本身:**§8 定位轴线用于平面定位**,
    而详图表达的是局部构造,不表达平面定位。

    处置与符号场一致:**只排除不删除** —— 轴线照常留档可查,
    只是不进 3D 场景与世界锚点。
    """
    from services.axis_assembly_filter import excluded_from_assembly

    detail = {"title": "地下连续墙预埋件详图", "discipline": "structure"}
    got = excluded_from_assembly(detail)
    assert got and "详图" in got


@pytest.mark.unit
def test_plans_do_contribute_axes():
    """平面图正常参与装配 —— 它们正是轴网的来源。"""
    from services.axis_assembly_filter import excluded_from_assembly

    assert not excluded_from_assembly(
        {"title": "首层平面图", "discipline": "architecture"})
    assert not excluded_from_assembly(
        {"title": "围护体平面布置图", "discipline": "structure"})


@pytest.mark.unit
def test_sections_still_contribute():
    """剖面/立面带轴号是常规做法,不排除。"""
    from services.axis_assembly_filter import excluded_from_assembly

    assert not excluded_from_assembly(
        {"title": "地下连续墙配筋剖面图（二）", "discipline": "structure"})


@pytest.mark.unit
def test_missing_metadata_is_not_excluded():
    """**判不出就不排除** —— 宁可多一张待人审,不可少一张真轴网。"""
    from services.axis_assembly_filter import excluded_from_assembly

    assert not excluded_from_assembly({})
    assert not excluded_from_assembly(None)
