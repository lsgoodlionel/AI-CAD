"""会审审查 V2 — 对象识别测试（object_identifier.identify）。

覆盖：显式命中 / 推定 / 知识缺失三条判定路径，跨专业 JG/GPS/MQ。
"""
import pytest

from core.ai_review.review_audit import object_identifier


@pytest.mark.unit
def test_identify_explicit_hit_returns_explicit_basis():
    # Arrange：正文显式点名 JG 对象「节点锚固」
    concerns = [{"label": "标高", "reason": "命中标高触发词"}]
    text = "关于节点锚固的标高表达不足，请设计明确"

    # Act
    result = object_identifier.identify("JG", concerns, text)

    # Assert
    assert result["object"] == "节点锚固"
    assert result["level"] == "节点级"
    assert "显式" in result["basis"]


@pytest.mark.unit
def test_identify_inferred_when_no_object_name_in_text():
    # Arrange：正文不含任何 JG 对象名 → 推定为该专业高频对象
    concerns = [{"label": "标高", "reason": "命中标高触发词"}]
    text = "现平面图与剖面图标高不一致，请明确以哪张图为准"

    # Act
    result = object_identifier.identify("JG", concerns, text)

    # Assert
    assert result["object"] == "梁、柱、板、墙、核心筒"  # objects 首项
    assert result["level"] == "部位级"
    assert "推定" in result["basis"]


@pytest.mark.unit
def test_identify_unknown_discipline_returns_evidence_insufficient():
    # Arrange：未知专业代码 → 无对象知识，退「证据不足」
    concerns = [{"label": "做法", "reason": "命中做法触发词"}]
    text = "某条无对象命中的会审记录"

    # Act
    result = object_identifier.identify("UNKNOWN_CODE", concerns, text)

    # Assert
    assert result["object"] == ""
    assert result["level"] == ""
    assert result["basis"] == "证据不足"


@pytest.mark.unit
def test_identify_unknown_discipline_without_concern_still_evidence_insufficient():
    # Arrange：无对象知识且无 concern
    result = object_identifier.identify("UNKNOWN_CODE", [], "无定位无对象记录")

    # Assert
    assert result["basis"] == "证据不足"
    assert result["object"] == ""


@pytest.mark.unit
def test_identify_gps_explicit_system_level():
    # Arrange：GPS 显式命中系统级对象「管道与阀件」
    concerns = [{"label": "系统", "reason": "命中系统触发词"}]
    text = "管道与阀件的系统图与平面图管线走向不一致"

    # Act
    result = object_identifier.identify("GPS", concerns, text)

    # Assert
    assert result["object"] == "管道与阀件"
    assert result["level"] == "系统级"
    assert "显式" in result["basis"]


@pytest.mark.unit
def test_identify_gps_inferred_defaults_to_head_object():
    # Arrange：GPS 正文无对象名 → 推定首项「管道与阀件」(系统级)
    concerns = [{"label": "管道", "reason": "命中管道触发词"}]
    text = "给排水系统数量不一致，请核对"

    # Act
    result = object_identifier.identify("GPS", concerns, text)

    # Assert
    assert result["object"] == "管道与阀件"
    assert result["level"] == "系统级"
    assert "推定" in result["basis"]


@pytest.mark.unit
def test_identify_mq_explicit_node_level_object():
    # Arrange：MQ 显式命中节点级对象「收边收口」
    concerns = [{"label": "节点", "reason": "命中节点触发词"}]
    text = "幕墙收边收口节点与主体结构冲突"

    # Act
    result = object_identifier.identify("MQ", concerns, text)

    # Assert
    assert result["object"] == "收边收口"
    assert result["level"] == "节点级"
    assert "显式" in result["basis"]


@pytest.mark.unit
def test_identify_prefers_longest_name_on_multiple_hits():
    # Arrange：MQ 正文同时含「埋件」与「埋件与连接件」，应取最长名
    concerns = [{"label": "连接", "reason": "命中连接触发词"}]
    text = "幕墙埋件与连接件定位不明，需复核"

    # Act
    result = object_identifier.identify("MQ", concerns, text)

    # Assert
    assert result["object"] == "埋件与连接件"
    assert "显式" in result["basis"]
