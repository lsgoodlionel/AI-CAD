"""会审审查 V2 — 文书化输出测试（document_writer.write）。

覆盖：纪要口径(问题/责任/结论) 与 答复口径(设计意图/执行依据/修订说明/闭环条件)
结构、每条 {type,text}、两类口径不混写。
"""
import pytest

from core.ai_review.review_audit import document_writer

_JG_OBJ = {
    "level": "部位级",
    "object": "梁、柱、板、墙、核心筒",
    "basis": "推定",
    "concern": "标高",
    "scenario": "图间冲突",
}
_PACK = {
    "主问题": "主问题文本",
    "补充问题": "补充问题文本",
    "证据缺口": "证据缺口文本",
}
_INTERFACE = {"primary": "结构", "related": ["建筑", "给排水"], "reason": "默认联查"}


@pytest.mark.unit
def test_write_returns_both_document_dialects():
    # Act
    doc = document_writer.write("JG", _JG_OBJ, _PACK, _INTERFACE)

    # Assert
    assert set(doc) == {"会审纪要口径", "设计答复口径"}


@pytest.mark.unit
def test_write_minutes_have_problem_responsibility_conclusion_types():
    # Act
    doc = document_writer.write("JG", _JG_OBJ, _PACK, _INTERFACE)

    # Assert：纪要口径包含 问题/责任/结论 三类
    types = {entry["type"] for entry in doc["会审纪要口径"]}
    assert {"问题条目", "责任条目", "结论条目"} <= types


@pytest.mark.unit
def test_write_reply_has_four_design_dialect_types():
    # Act
    doc = document_writer.write("JG", _JG_OBJ, _PACK, _INTERFACE)

    # Assert：答复口径包含 设计意图/执行依据/修订说明/闭环条件
    types = {entry["type"] for entry in doc["设计答复口径"]}
    assert {"设计意图", "执行依据", "修订说明", "闭环条件"} <= types


@pytest.mark.unit
def test_write_each_entry_has_type_and_text():
    # Act
    doc = document_writer.write("JG", _JG_OBJ, _PACK, _INTERFACE)

    # Assert：每条均为 {type, text}
    for section in doc.values():
        for entry in section:
            assert set(entry) >= {"type", "text"}
            assert entry["text"].strip()


@pytest.mark.unit
def test_write_dialects_do_not_cross_contaminate():
    # Act
    doc = document_writer.write("JG", _JG_OBJ, _PACK, _INTERFACE)

    # Assert：纪要口径不混入答复口径措辞「设计意图」
    minutes_text = "".join(entry["text"] for entry in doc["会审纪要口径"])
    minutes_types = {entry["type"] for entry in doc["会审纪要口径"]}
    assert "设计意图" not in minutes_text
    assert "设计意图" not in minutes_types
    # 答复口径不混入纪要专属「责任条目」类型
    reply_types = {entry["type"] for entry in doc["设计答复口径"]}
    assert "责任条目" not in reply_types


@pytest.mark.unit
def test_write_injects_object_into_text():
    # Act
    doc = document_writer.write("JG", _JG_OBJ, _PACK, _INTERFACE)

    # Assert：对象名注入文书正文
    all_text = "".join(
        entry["text"] for section in doc.values() for entry in section
    )
    assert "梁、柱、板、墙、核心筒" in all_text


@pytest.mark.unit
def test_write_unknown_discipline_degrades_to_empty_lists():
    # Act：未知专业无模板 → 降级返回空列表，不抛异常
    doc = document_writer.write("UNKNOWN_CODE", _JG_OBJ, _PACK, _INTERFACE)

    # Assert
    assert doc["会审纪要口径"] == []
    assert doc["设计答复口径"] == []
