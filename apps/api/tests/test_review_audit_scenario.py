"""会审审查 V2 — 场景路由测试（scenario_router.route）。

覆盖：四场景信号命中、优先级排序、高风险升级，返回结构与四值约束。
"""
import pytest

from core.ai_review.review_audit import scenario_router

_SCENARIO_VALUES = {"正常审图", "图间冲突", "施工落地", "验收风险"}
_LOW_RISK = {"level": "低", "trigger": ""}
_HIGH_RISK = {"level": "高", "trigger": "命中风险触发词"}


@pytest.mark.unit
def test_route_conflict_signal_maps_to_image_conflict():
    # Arrange：正文含「不一致」冲突信号
    result = scenario_router.route("平面与剖面标高不一致", _LOW_RISK, [])

    # Assert
    assert result["name"] == "图间冲突"


@pytest.mark.unit
def test_route_landing_signal_maps_to_construction_landing():
    # Arrange：正文含「无法施工」施工落地信号
    result = scenario_router.route("现场无法施工，预留洞缺失", _LOW_RISK, [])

    # Assert
    assert result["name"] == "施工落地"


@pytest.mark.unit
def test_route_acceptance_signal_maps_to_acceptance_risk():
    # Arrange：正文含「消防验收」验收风险信号
    result = scenario_router.route("不满足消防验收要求", _LOW_RISK, [])

    # Assert
    assert result["name"] == "验收风险"


@pytest.mark.unit
def test_route_routine_when_no_signal():
    # Arrange：普通正文，无任何场景信号
    result = scenario_router.route("请核对图纸表达是否完整", _LOW_RISK, [])

    # Assert
    assert result["name"] == "正常审图"


@pytest.mark.unit
def test_route_priority_conflict_over_landing_and_acceptance():
    # Arrange：同时命中冲突、施工、验收信号 → 取最高优先级 图间冲突
    text = "平面与剖面不一致，且现场无法施工，影响消防验收"

    # Act
    result = scenario_router.route(text, _LOW_RISK, [])

    # Assert：图间冲突 > 施工落地 > 验收风险
    assert result["name"] == "图间冲突"


@pytest.mark.unit
def test_route_priority_landing_over_acceptance():
    # Arrange：命中施工 + 验收信号（无冲突）→ 取 施工落地
    result = scenario_router.route("现场无法施工，且影响节能审查", _LOW_RISK, [])

    # Assert
    assert result["name"] == "施工落地"


@pytest.mark.unit
def test_route_high_risk_escalates_from_routine():
    # Arrange：普通文本但风险高且含施工信号 → 升级到施工落地
    result = scenario_router.route("预留套管条件不足", _HIGH_RISK, [])

    # Assert
    assert result["name"] in {"施工落地", "验收风险"}
    assert result["name"] != "正常审图"


@pytest.mark.unit
def test_route_high_risk_with_acceptance_signal_to_acceptance():
    # Arrange：高风险 + 验收信号 → 验收风险
    result = scenario_router.route("不满足消防验收，存在安全隐患", _HIGH_RISK, [])

    # Assert
    assert result["name"] == "验收风险"


@pytest.mark.unit
def test_route_issue_class_maps_to_scenario():
    # Arrange：正文无信号，但 issue_class 含「图纸冲突」
    result = scenario_router.route("请核对", _LOW_RISK, ["图纸冲突"])

    # Assert
    assert result["name"] == "图间冲突"


@pytest.mark.unit
def test_route_returns_name_and_priority_reason_with_valid_value():
    # Arrange + Act
    result = scenario_router.route("平面与剖面不一致", _LOW_RISK, [])

    # Assert：结构 {name, priority_reason}，name 属四值
    assert set(result) == {"name", "priority_reason"}
    assert result["name"] in _SCENARIO_VALUES
    assert result["priority_reason"]
