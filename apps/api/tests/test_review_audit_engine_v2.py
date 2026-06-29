"""会审审查 V2 — 引擎端到端测试（audit_text V1+V2 schema）。

覆盖：V1 9 key + V2 4 key 共存；标准问题[0]==问题包.主问题；
用 07 抽样报告样本3(JG)/样本2(GPS) 正文参数化，断言对象/场景方向与报告一致。
"""
import pytest

from core.ai_review.review_audit.engine import audit_text

_V1_KEYS = {
    "专业判断", "定位信息", "核心concern", "问题归类",
    "接口复核", "风险等级", "建议动作", "证据缺口", "标准问题",
}
_V2_KEYS = {"对象识别", "场景识别", "问题包", "文书输出"}


@pytest.mark.unit
def test_audit_text_returns_v1_and_v2_keys():
    # Arrange + Act
    data = audit_text(
        "地下二层③~⑤轴梁标高",
        "现平面图与剖面图标高不一致，请明确以哪张图为准",
        discipline="JG",
    )

    # Assert：V1 9 key + V2 4 key 全在
    assert _V1_KEYS <= set(data)
    assert _V2_KEYS <= set(data)


@pytest.mark.unit
def test_standard_question_first_equals_question_pack_main():
    # Arrange + Act
    data = audit_text(
        "梁标高冲突",
        "现平面图与剖面图标高不一致，请明确以哪张图为准",
        discipline="JG",
    )

    # Assert：标准问题[0] == 问题包.主问题
    main = data["问题包"]["主问题"]
    assert main  # 主问题非空
    assert data["标准问题"][0] == main


@pytest.mark.unit
def test_v2_object_section_structure():
    # Act
    data = audit_text("梁标高冲突", "平面与剖面不一致", discipline="JG")

    # Assert
    obj = data["对象识别"]
    assert set(obj) == {"level", "object", "basis"}
    assert obj["level"] in {"部位级", "系统级", "节点级", ""}


@pytest.mark.unit
def test_v2_scenario_section_structure():
    # Act
    data = audit_text("梁标高冲突", "平面与剖面不一致", discipline="JG")

    # Assert
    scenario = data["场景识别"]
    assert set(scenario) == {"name", "priority_reason"}
    assert scenario["name"] in {"正常审图", "图间冲突", "施工落地", "验收风险"}


# ── 07 抽样报告样本参数化（对象/场景方向校验）──
# 样本正文均含「不一致/以哪张图为准」冲突信号 → 期望主场景 图间冲突。
_SAMPLES = [
    pytest.param(
        "JG",
        "结构图纸会审",
        "地下室结构集水坑定位为 8/Q轴，现平面图与剖面图标高不一致，请明确以哪张图为准",
        "梁、柱、板、墙、核心筒",
        "部位级",
        id="sample3-JG",
    ),
    pytest.param(
        "GPS",
        "给排水图纸会审",
        "系统图的管线走向和平面图的管线不一致，机房夹层套管无法施工，请明确以哪张图为准",
        "管道与阀件",
        "系统级",
        id="sample2-GPS",
    ),
]


@pytest.mark.unit
@pytest.mark.parametrize("discipline,title,body,exp_object,exp_level", _SAMPLES)
def test_audit_text_object_and_scenario_match_07_report(
    discipline, title, body, exp_object, exp_level
):
    # Act
    data = audit_text(title, body, discipline=discipline)

    # Assert：专业判断、对象方向、级别、主场景与 07 报告一致
    assert data["专业判断"]["code"] == discipline
    assert data["对象识别"]["object"] == exp_object
    assert data["对象识别"]["level"] == exp_level
    assert data["场景识别"]["name"] == "图间冲突"


@pytest.mark.unit
def test_audit_text_document_output_populated_for_known_discipline():
    # Act
    data = audit_text(
        "结构图纸会审", "平面与剖面不一致，请明确以哪张图为准", discipline="JG"
    )

    # Assert：文书输出含纪要与答复两类，均非空
    doc = data["文书输出"]
    assert doc["会审纪要口径"]
    assert doc["设计答复口径"]
