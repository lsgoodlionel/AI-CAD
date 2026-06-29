"""会审审查 V2 — 问题包测试（question_pack_builder.build）。

覆盖：三段式结构、对象/场景注入、占位符无残留、证据缺口随定位动态变化。
"""
import pytest

from core.ai_review.review_audit import question_pack_builder

_PLACEHOLDERS = ("{对象}", "{待明确}", "{级别}", "{concern}")
_EMPTY_LOCATION = {
    "drawings": [],
    "levels": [],
    "axes": [],
    "nodes_or_systems": [],
    "spaces": [],
}
_FULL_LOCATION = {
    "drawings": ["JG-101"],
    "levels": ["地下二层"],
    "axes": ["③~⑤轴"],
    "nodes_or_systems": ["节点1"],
    "spaces": ["泵房"],
}


def _jg_obj() -> dict:
    return {"level": "部位级", "object": "梁、柱、板、墙、核心筒", "basis": "推定"}


def _conflict_scenario() -> dict:
    return {"name": "图间冲突", "priority_reason": "命中冲突信号"}


@pytest.mark.unit
def test_build_returns_three_segments():
    # Act
    pack = question_pack_builder.build(
        "JG", _jg_obj(), _conflict_scenario(), _EMPTY_LOCATION,
        [{"label": "标高", "reason": "命中"}],
    )

    # Assert
    assert set(pack) == {"主问题", "补充问题", "证据缺口"}


@pytest.mark.unit
def test_build_main_question_contains_object_name():
    # Act
    pack = question_pack_builder.build(
        "JG", _jg_obj(), _conflict_scenario(), _EMPTY_LOCATION,
        [{"label": "标高", "reason": "命中"}],
    )

    # Assert：主问题应含对象名（场景模板的对象前缀）
    assert "梁、柱、板、墙、核心筒" in pack["主问题"]


@pytest.mark.unit
def test_build_no_placeholder_residue_in_any_segment():
    # Act
    pack = question_pack_builder.build(
        "JG", _jg_obj(), _conflict_scenario(), _EMPTY_LOCATION,
        [{"label": "标高", "reason": "命中"}],
    )

    # Assert：所有占位符均已替换
    combined = pack["主问题"] + pack["补充问题"] + pack["证据缺口"]
    for placeholder in _PLACEHOLDERS:
        assert placeholder not in combined


@pytest.mark.unit
def test_build_evidence_gap_lists_missing_location_labels():
    # Arrange：定位全缺 → 证据缺口应提示补全图号/层位/轴线等
    pack = question_pack_builder.build(
        "JG", _jg_obj(), _conflict_scenario(), _EMPTY_LOCATION,
        [{"label": "标高", "reason": "命中"}],
    )

    # Assert
    gap = pack["证据缺口"]
    assert "图号" in gap
    assert "轴线" in gap


@pytest.mark.unit
def test_build_evidence_gap_changes_when_location_present():
    # Arrange：定位齐全 vs 全缺，证据缺口口径应不同
    gap_empty = question_pack_builder.build(
        "JG", _jg_obj(), _conflict_scenario(), _EMPTY_LOCATION, [],
    )["证据缺口"]
    gap_full = question_pack_builder.build(
        "JG", _jg_obj(), _conflict_scenario(), _FULL_LOCATION, [],
    )["证据缺口"]

    # Assert：定位齐全时回退固定模板口径，与逐项罗列缺失的口径不同
    assert gap_empty != gap_full
    # 全缺时逐项罗列「房间/设备名称」，齐全时的固定模板不含该项
    assert "房间/设备名称" in gap_empty
    assert "房间/设备名称" not in gap_full


@pytest.mark.unit
def test_build_supplement_question_non_empty_for_known_discipline():
    # Act
    pack = question_pack_builder.build(
        "GPS",
        {"level": "系统级", "object": "管道与阀件", "basis": "推定"},
        {"name": "图间冲突", "priority_reason": "x"},
        _EMPTY_LOCATION,
        [{"label": "系统", "reason": "命中"}],
    )

    # Assert
    assert pack["补充问题"].strip()


@pytest.mark.unit
def test_build_falls_back_to_pack_template_when_no_scenario_match():
    # Arrange：对象名不在 scenario_templates 中 → 回退 question_pack 主问题模板填位
    obj = {"level": "节点级", "object": "节点锚固", "basis": "显式命名"}
    pack = question_pack_builder.build(
        "JG", obj, _conflict_scenario(), _EMPTY_LOCATION,
        [{"label": "标高", "reason": "命中"}],
    )

    # Assert：主问题非空且占位符已替换
    assert pack["主问题"].strip()
    for placeholder in _PLACEHOLDERS:
        assert placeholder not in pack["主问题"]


@pytest.mark.unit
def test_build_unknown_discipline_returns_empty_main_and_supplement():
    # Arrange：未知专业既无场景模板也无问题包模板
    obj = {"level": "部位级", "object": "某对象", "basis": "推定"}
    pack = question_pack_builder.build(
        "UNKNOWN_CODE", obj, _conflict_scenario(), _EMPTY_LOCATION, [],
    )

    # Assert：主/补充问题降级为空，证据缺口仍按缺失定位生成
    assert pack["主问题"] == ""
    assert pack["补充问题"] == ""
    assert pack["证据缺口"]


@pytest.mark.unit
def test_build_main_question_includes_scenario_prefix_label():
    # Arrange：JG 图间冲突场景模板含「图间冲突」标签
    pack = question_pack_builder.build(
        "JG", _jg_obj(), _conflict_scenario(), _EMPTY_LOCATION,
        [{"label": "标高", "reason": "命中"}],
    )

    # Assert
    assert "图间冲突" in pack["主问题"]
