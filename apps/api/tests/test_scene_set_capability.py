"""scene 携带图纸角色统计与建模能力评估。

**为什么必须进 scene**:模型 v31 的 13 层里 10 层标高是
`DEFAULT_STORY_HEIGHT_M = 4.5` 硬推的,**而界面上完全看不出来**——
用户看到的 `F6 24.9` 与从图纸读出的 `36.800` 长得一模一样。

降级必须可见(蓝图 §7 约束 3)。这一层把
`drawing_role` + `partial_set` 的结论放进 scene,前端才拿得到。
"""
from __future__ import annotations

import pytest

from services.drawing_role import (
    ROLE_COORDINATE_BASE, ROLE_DETAIL, ROLE_ELEVATION_REFERENCE,
    ROLE_FLOOR_SKELETON,
)
from services.model_builder import build_set_capability_payload


def _d(no: str, title: str, **kw) -> dict:
    return {"drawing_no": no, "title": title, **kw}


@pytest.mark.unit
def test_payload_reports_role_counts_and_capability():
    payload = build_set_capability_payload([
        _d("X-1", "正交轴网定位图"),
        _d("X-2", "一层完整平面图"),
        _d("X-3", "南立面图"),
        _d("X-4", "楼梯放大详图"),
    ])
    assert payload["roles"][ROLE_COORDINATE_BASE] == 1
    assert payload["roles"][ROLE_FLOOR_SKELETON] == 1
    assert payload["roles"][ROLE_ELEVATION_REFERENCE] == 1
    assert payload["roles"][ROLE_DETAIL] == 1
    assert payload["capability"]["elevations"] == "full"


@pytest.mark.unit
def test_missing_elevation_drawings_surface_a_degradation():
    """核心用例:没有立面/剖面时,**界面必须能看到「层高是默认值」**。"""
    payload = build_set_capability_payload([
        _d("X-1", "一层完整平面图"), _d("X-2", "二层完整平面图")])
    assert payload["capability"]["elevations"] == "none"
    assert any("默认" in d for d in payload["capability"]["degradations"])


@pytest.mark.unit
def test_missing_positioning_drawings_surface_a_degradation():
    payload = build_set_capability_payload([_d("X-1", "一层完整平面图")])
    assert payload["capability"]["world_coords"] == "none"
    assert any("世界坐标" in d for d in payload["capability"]["degradations"])


@pytest.mark.unit
def test_stage_plan_is_ordered_by_dependency():
    payload = build_set_capability_payload([
        _d("X-1", "南立面图"), _d("X-2", "正交轴网定位图"),
        _d("X-3", "一层完整平面图")])
    assert [s["role"] for s in payload["stages"]] == [
        ROLE_COORDINATE_BASE, ROLE_FLOOR_SKELETON, ROLE_ELEVATION_REFERENCE]


@pytest.mark.unit
def test_learned_number_patterns_are_reported():
    """学到的编号规律要留档 —— 便于核对它学对了没有。

    **不硬编码任何前缀**:这里故意用一个陌生体系。
    """
    payload = build_set_capability_payload([
        _d("JZ-SG-01-01", "正交轴网定位图"),
        _d("JZ-SG-01-02", "中心轴网定位图"),
        _d("JZ-SG-01-03", "竖向轴网定位图"),
        _d("JZ-SG-01-09", ""),          # 图名缺失,靠学到的模式补
    ])
    assert payload["learned_patterns"].get("JZ-SG-01") == ROLE_COORDINATE_BASE
    assert payload["roles"][ROLE_COORDINATE_BASE] == 4


@pytest.mark.unit
def test_content_evidence_beats_the_title():
    """轴网识别给出的圈数/引线数应当压过图名(蓝图 §1.5 第 1 级)。"""
    payload = build_set_capability_payload(
        [_d("X-1", "楼梯放大详图")],
        evidence_by_drawing={"X-1": {"axis_circle_count": 108,
                                     "transform_inliers": 14,
                                  "transform_rmse_m": 0.0065}},
        key="drawing_no")
    assert payload["roles"][ROLE_COORDINATE_BASE] == 1


@pytest.mark.unit
def test_empty_project_is_safe():
    payload = build_set_capability_payload([])
    assert payload["capability"]["can_build"] is False
    assert payload["stages"] == []


@pytest.mark.unit
def test_payload_separates_real_unit_loss_from_non_applicable():
    """单体归属拆解要进 scene。

    **实测**:1866 张「未分配」里 959 张是目录/说明/详图/围护图,
    本就无单体归属;788 张有楼层可降级挂默认单体。
    真正需要处理的是 907 张,**原报虚高 2.1 倍**。
    """
    payload = build_set_capability_payload([
        _d("X-1", "图纸目录"),
        _d("X-2", "楼梯放大详图"),
        _d("X-3", "一层平面图"),
        _d("X-4", "南区二层平面图"),
    ])
    ua = payload["unit_assignment"]
    assert ua["not_applicable"] == 2      # 目录 + 详图
    assert ua["defaulted"] == 1           # 一层平面图,有楼层缺单体
    assert ua["assigned"] == 1            # 南区
    assert ua["needs_attention"] == 1     # 只算 defaulted + unresolved
