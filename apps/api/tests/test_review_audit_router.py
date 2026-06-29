"""会审审查 — 专业路由测试（discipline_router）"""
import pytest

from core.ai_review.review_audit.discipline_router import route
from core.ai_review.review_audit.protocol_loader import load_disciplines

VALID_CODES = {
    "ZH", "JG", "WH", "JZ", "ZJ", "RF", "GJG", "JDQ", "GPS", "ZS",
    "DQ", "NT", "MQ", "SWT", "JGUAN", "JN", "JK", "RD", "XF",
}


@pytest.mark.unit
@pytest.mark.parametrize("code", sorted(VALID_CODES))
def test_explicit_fine_code_is_respected(code):
    # Arrange / Act
    result = route(code, "某标题", "某正文")
    # Assert
    assert result["code"] == code
    assert result["basis"]


@pytest.mark.unit
def test_returns_dict_with_required_keys():
    result = route("JG", "梁标高问题", "平面与剖面不一致")
    assert set(result) >= {"code", "name", "basis"}


@pytest.mark.unit
def test_coarse_discipline_is_mapped_to_fine():
    # 现有 5 粗专业之一 → 应反推到 19 细分专业
    result = route("structure", "结构梁柱节点锚固", "节点详图缺失")
    assert result["code"] in VALID_CODES


@pytest.mark.unit
def test_missing_discipline_inferred_from_terms():
    # 缺失专业，正文术语指向给排水
    result = route(None, "喷淋系统管道", "消火栓与喷淋管道标高打架，请明确系统")
    assert result["code"] in VALID_CODES
    assert "推断" in result["basis"] or result["code"] != ""


@pytest.mark.unit
def test_administrative_text_falls_back_gracefully():
    # 无实体问题的行政文本 → 仍返回合法专业代码，不报错
    result = route(None, "会议签到通知", "请相关人员准时参会")
    assert result["code"] in VALID_CODES


@pytest.mark.unit
def test_discipline_names_resolve_for_all_codes():
    disciplines = load_disciplines()
    # 协议资产存在时，每个返回 code 都能解析出非空中文名
    if disciplines:
        for code in VALID_CODES:
            assert route(code, "t", "b")["name"]
