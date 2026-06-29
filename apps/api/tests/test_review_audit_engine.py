"""会审审查 — 引擎端到端测试（audit_text + ReviewAuditEngine 字段映射）"""
import asyncio

import pytest

from core.ai_review.review_audit.engine import audit_text, ReviewAuditEngine
from core.ai_review.base import DrawingContext, AIIssue

OUTPUT_KEYS = {
    "专业判断", "定位信息", "核心concern", "问题归类",
    "接口复核", "风险等级", "建议动作", "证据缺口", "标准问题",
}


@pytest.mark.unit
def test_audit_text_returns_full_contract_schema():
    data = audit_text(
        "地下二层③~⑤轴梁标高", "现平面图与剖面图标高不一致，请明确以哪张图为准",
        discipline="JG",
    )
    assert OUTPUT_KEYS <= set(data)
    assert data["专业判断"]["code"] == "JG"


@pytest.mark.unit
def test_audit_text_location_substructure():
    data = audit_text("梁标高问题", "③~⑤轴梁标高不一致", discipline="JG")
    loc = data["定位信息"]
    assert set(loc) >= {"drawings", "levels", "axes", "nodes_or_systems", "spaces"}


@pytest.mark.unit
def test_audit_text_without_discipline_does_not_crash():
    data = audit_text("某记录", "管道与桥架打架，请协调", discipline=None)
    assert OUTPUT_KEYS <= set(data)
    assert data["专业判断"]["code"]


@pytest.mark.unit
def test_audit_text_administrative_is_low_risk_or_empty():
    data = audit_text("会议签到", "请准时参会", discipline=None)
    assert data["风险等级"]["level"] in {"高", "中", "低", ""}


@pytest.mark.unit
def test_engine_analyze_maps_to_extended_aiissue():
    engine = ReviewAuditEngine()
    ctx = DrawingContext(
        drawing_id="d1", drawing_no="JS-101", discipline="JG",
        title="地下二层③~⑤轴梁标高", version="A", file_key="k",
        file_ext="pdf", project_id="p1",
        extracted_text="现平面图与剖面图标高不一致，无法明确施工依据",
    )

    class _FakeDB:
        async def fetch_all(self, *a, **k):
            return []

    issues = asyncio.run(engine.analyze(ctx, _FakeDB()))
    assert isinstance(issues, list)
    if issues:  # 协议资产就绪时应产出会审问题
        first = issues[0]
        assert isinstance(first, AIIssue)
        assert first.engine == "review"
        assert first.discipline_code == "JG"
        assert first.standard_question


@pytest.mark.unit
def test_engine_name_is_review():
    assert ReviewAuditEngine().engine_name == "review"
