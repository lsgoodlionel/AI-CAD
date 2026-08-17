"""建模角色识别:**不依赖任何具体工程的图号体系**。

**为什么必须这样**:上海大歌剧院用 `A-01` 轴网定位、`A-10` 完整平面、
`A-20` 立面、`S-x-20` 结构平面。但这是**这一家设计院、这一个工程**的编号,
换一家就不成立。把它写死等于系统只能服务一个项目。

**三级级联,越靠前越不依赖编号**:

1. **内容特征**(与编号完全无关,最可靠)——轴号圈 + 坐标标注引线 → 坐标基准;
   主标高链 → 标高来源;轴网带 + 构件 → 楼层骨架
2. **国标术语**(GB/T 50001 §3.3.1 图种名 + GB/T 50104)——
   平面图/立面图/剖面图/详图/系统图/说明/目录,**全行业通用**
3. **编号模式**(**从本批图纸学出来,不写死**)——
   同一编号段的图若角色一致,则该段可外推到同段的其他图

**兜底**:三级都判不出 → `unknown`,**如实标注,绝不猜**。
"""
from __future__ import annotations

import pytest

from services.drawing_role import (
    ROLE_COMPONENT_SOURCE, ROLE_COORDINATE_BASE, ROLE_DETAIL,
    ROLE_ELEVATION_REFERENCE, ROLE_FLOOR_SKELETON, ROLE_NON_GEOMETRIC,
    ROLE_UNKNOWN, classify_role, learn_number_patterns,
)


def _d(no: str = "X-1", title: str = "", **kw) -> dict:
    return {"drawing_no": no, "title": title, **kw}


# ── 第 1 级:内容特征(与编号无关)────────────────────────────────

@pytest.mark.unit
def test_axis_circles_plus_read_coordinates_is_coordinate_base():
    """§8.0.2 轴号圈 + §11.8 **读出来的**工程坐标 —— 坐标基准图的内容指纹。

    图名可以叫任何名字,图号可以是任何体系,内容不会骗人。
    """
    got = classify_role(_d(no="ZZZ-999", title="乱七八糟的名字"),
                        evidence={"axis_circle_count": 108,
                                  "transform_inliers": 14,
                                  "transform_rmse_m": 0.0065})
    assert got.role == ROLE_COORDINATE_BASE
    assert got.source == "content"


@pytest.mark.unit
def test_axis_circles_without_coordinates_is_not_coordinate_base():
    """只有轴网没有坐标标注 —— 定不了世界坐标,不是坐标基准。"""
    got = classify_role(_d(), evidence={"axis_circle_count": 108,
                                        "transform_inliers": 0})
    assert got.role != ROLE_COORDINATE_BASE


@pytest.mark.unit
def test_many_leaders_without_readable_coordinates_is_not_coordinate_base():
    """**这条防的是一次真误判**:给排水图的管线是「水平段 + 斜段」,
    形状与坐标标注引线一样,`find_leaders` 会大量误检。

    实测 `P-29-41 四层喷淋抗震支架平面图` 检出 **64 条引线**、356 个圈,
    比真定位图 A-01-02A(16 条引线)还多 —— 但它**锚点 0、粗错 0**,
    因为引线末端根本读不出工程坐标。

    判据必须是「引线上**读出了坐标**」,不是「有引线」。
    实测 38 张候选里只有 5 张真读出坐标(含三张定位图)。
    """
    got = classify_role(_d(title="四层喷淋抗震支架平面图"),
                        evidence={"axis_circle_count": 356,
                                  "coordinate_leader_count": 64,
                                  "transform_inliers": 0})
    assert got.role != ROLE_COORDINATE_BASE


@pytest.mark.unit
def test_high_inlier_count_with_bad_rmse_is_rejected():
    """**内点多不等于拟合准**。

    实测 `ZNH-01-01 弱电室外总平面图` 内点 10 但 RMSE **0.94 米**、
    `A-04-02B 地下一层防火分区图` 内点 6 但 RMSE **1.07 米** ——
    这种变换拿去定位构件会整体偏出去一米,必须排除。
    """
    got = classify_role(_d(title="弱电室外总平面图"),
                        evidence={"axis_circle_count": 411,
                                  "transform_inliers": 10,
                                  "transform_rmse_m": 0.9365})
    assert got.role != ROLE_COORDINATE_BASE


@pytest.mark.unit
def test_three_inliers_is_too_few():
    """3 点拟合相似变换只剩 2 个残差自由度,极易过拟合噪声。

    实测内点恰为 3 的有 21 张,绝大多数是机电图。
    """
    got = classify_role(_d(), evidence={"axis_circle_count": 174,
                                        "transform_inliers": 3,
                                        "transform_rmse_m": 0.01})
    assert got.role != ROLE_COORDINATE_BASE


@pytest.mark.unit
def test_elevation_chain_makes_it_an_elevation_reference():
    """§11.8 竖向排列的标高链 —— 立面/剖面的内容指纹。"""
    got = classify_role(_d(no="QQ-7"),
                        evidence={"elevation_chain_length": 6})
    assert got.role == ROLE_ELEVATION_REFERENCE
    assert got.source == "content"


@pytest.mark.unit
def test_short_elevation_list_is_not_a_chain():
    """两三个零散标高不成链 —— 详图上也有标高。"""
    got = classify_role(_d(), evidence={"elevation_chain_length": 2})
    assert got.role != ROLE_ELEVATION_REFERENCE


@pytest.mark.unit
def test_full_axis_grid_with_components_is_floor_skeleton():
    """完整双向轴网 + 大量构件 = 覆盖整层的平面图。"""
    got = classify_role(_d(), evidence={"axis_bands_x": 2, "axis_bands_y": 2,
                                        "axis_circle_count": 112,
                                        "component_count": 800})
    assert got.role == ROLE_FLOOR_SKELETON


@pytest.mark.unit
def test_components_without_a_full_grid_is_component_source():
    """有构件但轴网不完整 —— 专项平面图,只供构件不当骨架。"""
    got = classify_role(_d(), evidence={"axis_bands_x": 1, "axis_bands_y": 0,
                                        "component_count": 400})
    assert got.role == ROLE_COMPONENT_SOURCE


# ── 第 2 级:国标术语(全行业通用,不是某院的编号)──────────────────

@pytest.mark.unit
@pytest.mark.parametrize("title,role", [
    ("南立面图", ROLE_ELEVATION_REFERENCE),
    ("7-7剖面图", ROLE_ELEVATION_REFERENCE),
    ("一层完整平面图", ROLE_FLOOR_SKELETON),
    ("地下二层平面图", ROLE_COMPONENT_SOURCE),
    ("楼梯ST-01放大详图", ROLE_DETAIL),
    ("墙身大样图", ROLE_DETAIL),
    ("电力配电箱系统图", ROLE_NON_GEOMETRIC),
    ("建筑设计说明", ROLE_NON_GEOMETRIC),
    ("图纸目录", ROLE_NON_GEOMETRIC),
    ("室内装修用料及做法表", ROLE_NON_GEOMETRIC),
])
def test_national_standard_terms_work_without_any_numbering(title, role):
    """图号故意给一个完全陌生的体系 —— 判别只能靠国标术语。"""
    got = classify_role(_d(no="独一无二-2024-0001", title=title))
    assert got.role == role
    assert got.source == "term"


@pytest.mark.unit
def test_axis_positioning_term_is_coordinate_base():
    """「定位图」+「轴网」是国标语境下的坐标基准。"""
    assert classify_role(_d(title="正交轴网定位图")).role == ROLE_COORDINATE_BASE


@pytest.mark.unit
def test_content_beats_terms():
    """内容与图名冲突时,**信内容**。图名会写错,内容不会。"""
    got = classify_role(_d(title="楼梯放大详图"),
                        evidence={"axis_circle_count": 108,
                                  "transform_inliers": 14,
                                  "transform_rmse_m": 0.0065})
    assert got.role == ROLE_COORDINATE_BASE


# ── 第 3 级:编号模式(学出来的,不是写死的)──────────────────────

@pytest.mark.unit
def test_learns_numbering_pattern_from_this_batch():
    """从**本批图纸**归纳编号段↔角色,而不是硬编码 `A-01`。

    换一个工程用 `JZ-SG-01` 也一样能学到。
    """
    labelled = [
        (_d(no="JZ-SG-01-01", title="正交轴网定位图"), ROLE_COORDINATE_BASE),
        (_d(no="JZ-SG-01-02", title="中心轴网定位图"), ROLE_COORDINATE_BASE),
        (_d(no="JZ-SG-01-03", title="竖向结构定位图"), ROLE_COORDINATE_BASE),
    ]
    patterns = learn_number_patterns(labelled)
    assert patterns.get("JZ-SG-01") == ROLE_COORDINATE_BASE


@pytest.mark.unit
def test_learned_pattern_fills_in_an_unnamed_drawing():
    """同段里图名缺失的那张,靠学到的模式补上。"""
    patterns = {"JZ-SG-01": ROLE_COORDINATE_BASE}
    got = classify_role(_d(no="JZ-SG-01-09", title=""), patterns=patterns)
    assert got.role == ROLE_COORDINATE_BASE
    assert got.source == "pattern"


@pytest.mark.unit
def test_inconsistent_segment_is_not_learned():
    """同一段里角色不一致 —— 学不出规律,**不能瞎学**。"""
    labelled = [
        (_d(no="AB-10-01", title="一层平面图"), ROLE_COMPONENT_SOURCE),
        (_d(no="AB-10-02", title="楼梯详图"), ROLE_DETAIL),
        (_d(no="AB-10-03", title="设计说明"), ROLE_NON_GEOMETRIC),
    ]
    assert "AB-10" not in learn_number_patterns(labelled)


@pytest.mark.unit
def test_pattern_needs_enough_samples():
    """一两张图不足以归纳一个编号段。"""
    labelled = [(_d(no="CD-20-01", title="南立面图"), ROLE_ELEVATION_REFERENCE)]
    assert learn_number_patterns(labelled) == {}


@pytest.mark.unit
def test_terms_beat_learned_patterns():
    """国标术语比学来的编号规律可靠 —— 后者只是外推。"""
    patterns = {"AB-10": ROLE_DETAIL}
    got = classify_role(_d(no="AB-10-05", title="南立面图"), patterns=patterns)
    assert got.role == ROLE_ELEVATION_REFERENCE


# ── 兜底 ──────────────────────────────────────────────────────

@pytest.mark.unit
def test_unknown_is_reported_honestly():
    """三级都判不出 —— 如实说 unknown,**绝不猜一个**。"""
    got = classify_role(_d(no="???", title="???"))
    assert got.role == ROLE_UNKNOWN
    assert got.confidence < 0.5


@pytest.mark.unit
def test_confidence_reflects_which_level_decided():
    """内容 > 术语 > 编号模式,置信度必须体现这个次序。"""
    content = classify_role(_d(), evidence={"axis_circle_count": 108,
                                            "transform_inliers": 14,
                                  "transform_rmse_m": 0.0065})
    term = classify_role(_d(title="正交轴网定位图"))
    pattern = classify_role(_d(no="AB-01-09"),
                            patterns={"AB-01": ROLE_COORDINATE_BASE})
    assert content.confidence > term.confidence > pattern.confidence
