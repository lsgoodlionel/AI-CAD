"""会审审查 V4 — 方法论升级（五维审查 + 控制链闭环判定 + 结构化处理建议）。

知识来源：~/work/031 图纸会审/06 方法论与AI原则/
- drawing_review_core_principles.md（五步审查顺序 + 6 条底层原则）
- meeting_ai_agent_principles.md（触发→边界→风险→责任→动作→闭环 + 闭环不足标记）
- drawing_review_output_template.md（处理建议表 + 闭环要求）
- meeting_pattern_summary.md（高频对象/动作/责任方先验）
"""
import asyncio

import pytest

from core.ai_review.review_audit import (
    action_recommender,
    control_chain,
    dimension_checker,
)
from core.ai_review.review_audit.engine import ReviewAuditEngine, audit_text
from core.ai_review.review_audit.protocol_loader import load_review_methodology
from core.ai_review.base import DrawingContext

# audit_text V4 新增 key
V4_KEYS = {"控制链", "五维审查", "处理建议", "闭环要求", "优先对象"}
# V1/V2/V3 旧 key（向后兼容必须保留）
LEGACY_KEYS = {
    "专业判断", "定位信息", "核心concern", "问题归类", "接口复核",
    "风险等级", "建议动作", "证据缺口", "标准问题",
    "对象识别", "场景识别", "问题包", "文书输出",
    "审图目标", "未来影响", "逐项清单",
}

DIMENSION_ORDER = ["完整性", "界面一致性", "可施工性", "验收可达性", "闭环性"]

_EMPTY_LOCATION = {
    "drawings": [], "levels": [], "axes": [], "nodes_or_systems": [], "spaces": [],
}


# ── 知识资产 ────────────────────────────────────────────────────
@pytest.mark.unit
def test_methodology_asset_loaded():
    m = load_review_methodology()
    assert [d["name"] for d in m["dimensions"]] == DIMENSION_ORDER
    assert {o["name"] for o in m["priority_objects"]} == {
        "进度移交", "质量验收", "图纸界面", "基坑围护", "施工组织",
    }
    assert set(m["action_types"]) == {"补图", "复核", "RFI", "会签", "专题协调"}
    assert {p["name"] for p in m["responsible_parties"]} >= {
        "总包", "建设单位", "监理", "安装", "设计单位",
    }
    assert set(m["closure_elements"]) == {"责任方", "时限", "输出件", "确认方式"}


# ── dimension_checker：五维审查（固定顺序）─────────────────────────
@pytest.mark.unit
def test_dimensions_follow_stable_order():
    rows = dimension_checker.check("风管标高与结构梁底净空冲突，无法安装", _EMPTY_LOCATION, [], [])
    assert [r["维度"] for r in rows] == DIMENSION_ORDER


@pytest.mark.unit
def test_dimension_flags_interface_and_constructability():
    rows = dimension_checker.check(
        "风管标高与结构梁底净空冲突，按现图无法施工", _EMPTY_LOCATION, [], ["图纸冲突"],
    )
    by_name = {r["维度"]: r for r in rows}
    assert by_name["界面一致性"]["状态"] == "存疑"
    assert by_name["可施工性"]["状态"] == "存疑"
    # 未命中的维度输出问题集追问，供人工核对
    assert by_name["验收可达性"]["状态"] == "待核"
    assert by_name["验收可达性"]["追问"]


@pytest.mark.unit
def test_dimension_empty_text_all_pending():
    rows = dimension_checker.check("", _EMPTY_LOCATION, [], [])
    assert all(r["状态"] == "待核" for r in rows)


# ── control_chain：触发→边界→风险→责任→动作→闭环 ────────────────────
def _classification() -> dict:
    return {
        "issue_class": ["图纸冲突"],
        "risk": {"level": "高", "trigger": "涉及标高冲突"},
        "interface": {"primary": "结构", "related": ["建筑", "给排水"], "reason": ""},
    }


@pytest.mark.unit
def test_control_chain_has_six_steps():
    chain = control_chain.build(
        "风管标高与梁底冲突", _classification(),
        {"name": "图间冲突", "priority_reason": "正文命中冲突信号"},
        ["调整风管走向并复核净高"],
    )
    for key in ("触发", "边界", "风险", "责任", "动作", "闭环", "闭环判定"):
        assert key in chain
    assert chain["风险"].startswith("高")


@pytest.mark.unit
def test_control_chain_marks_closure_insufficient():
    """正文无责任方/时限/输出件 → 闭环不足 + 追问项（agent 原则：闭环不足必须标记）。"""
    chain = control_chain.build(
        "风管标高与梁底冲突", _classification(),
        {"name": "图间冲突", "priority_reason": ""}, [],
    )
    verdict = chain["闭环判定"]
    assert verdict["status"] == "闭环不足"
    assert verdict["缺失项"]
    assert verdict["追问项"]


@pytest.mark.unit
def test_control_chain_closure_complete_when_elements_present():
    text = "由设计单位于7月4日前补充节点详图，经监理会签确认后闭环"
    chain = control_chain.build(
        text, _classification(), {"name": "正常审图", "priority_reason": ""}, ["补图"],
    )
    assert chain["闭环判定"]["status"] == "闭环完整"
    assert not chain["闭环判定"]["缺失项"]


@pytest.mark.unit
def test_control_chain_identifies_responsible_party():
    chain = control_chain.build(
        "设计单位出图缺少大样，总包现场无法施工", _classification(),
        {"name": "施工落地", "priority_reason": ""}, [],
    )
    assert "设计单位" in chain["责任"]


# ── action_recommender：结构化处理建议 + 闭环要求 ────────────────────
@pytest.mark.unit
def test_recommend_produces_structured_actions():
    result = action_recommender.recommend(
        "穿墙套管节点仅给平面定位，缺防水收口大样，请设计明确",
        _classification(),
        {"name": "图间冲突", "priority_reason": ""},
        {"level": "节点级", "object": "穿墙套管", "basis": "显式命名"},
    )
    actions = result["处理建议"]
    assert actions, "应至少产出一条结构化处理建议"
    for action in actions:
        assert set(action) == {"动作", "动作类型", "责任方", "配合方", "输出件"}
        assert action["动作类型"] in {"补图", "复核", "RFI", "会签", "专题协调"}
        assert action["责任方"] and action["输出件"]
    # 缺大样 → 必须包含补图动作
    assert any(a["动作类型"] == "补图" for a in actions)


@pytest.mark.unit
def test_recommend_closure_requirements_flags():
    result = action_recommender.recommend(
        "基坑围护图纸冲突，影响开工，工作面穿插受阻，需多专业界面协调",
        {
            "issue_class": ["接口冲突"],
            "risk": {"level": "高", "trigger": "涉及基坑"},
            "interface": {"primary": "基坑", "related": ["结构", "围护"], "reason": ""},
        },
        {"name": "施工落地", "priority_reason": ""},
        {"level": "部位级", "object": "基坑围护", "basis": "显式命名"},
    )
    closure = result["闭环要求"]
    assert closure["是否影响开工"] is True
    assert closure["是否影响穿插"] is True
    assert closure["是否需要专题会"] is True
    assert closure["下次复核节点"]


@pytest.mark.unit
def test_recommend_actions_capped():
    """处理建议保持紧凑（≤4 条），不堆砌低密度动作。"""
    text = "缺详图不一致无法施工需澄清会签跨专业协调验收"
    result = action_recommender.recommend(
        text, _classification(), {"name": "图间冲突", "priority_reason": ""}, {},
    )
    assert 1 <= len(result["处理建议"]) <= 4


# ── audit_text V4 扩展 + 向后兼容 ────────────────────────────────
@pytest.mark.unit
def test_audit_text_has_v4_keys_and_legacy_keys():
    data = audit_text(
        "地下二层机房", "风管标高与结构梁底净空冲突，套管预留未明确，基坑围护监测待加密",
        discipline="JG",
    )
    assert V4_KEYS <= set(data)
    assert LEGACY_KEYS <= set(data)
    assert [r["维度"] for r in data["五维审查"]] == DIMENSION_ORDER
    assert data["控制链"]["闭环判定"]["status"] in {"闭环完整", "闭环不足"}
    assert data["处理建议"]
    # 高频对象命中：基坑围护
    assert any(o["name"] == "基坑围护" for o in data["优先对象"])


# ── 引擎挂载 review_method（离线，无 LLM/db）─────────────────────────
def _ctx(text: str) -> DrawingContext:
    ctx = DrawingContext(
        drawing_id="d1", drawing_no="JG-101", discipline="structure",
        title="③~⑤轴梁标高问题", version="A", file_key="k", file_ext="pdf",
        project_id="p1",
    )
    ctx.extracted_text = text
    return ctx


@pytest.mark.unit
def test_methodology_loader_degrades_when_asset_missing(monkeypatch):
    """yaml 缺失 → 各 key 降级为空结构，绝不抛异常。"""
    from core.ai_review.review_audit import protocol_loader

    monkeypatch.setattr(protocol_loader, "_load_yaml", lambda _f: {})
    protocol_loader.load_review_methodology.cache_clear()
    try:
        m = protocol_loader.load_review_methodology()
        assert m["dimensions"] == []
        assert m["action_types"] == {}
        assert m["closure_elements"] == {}
    finally:
        protocol_loader.load_review_methodology.cache_clear()


@pytest.mark.unit
def test_control_chain_degrades_without_asset(monkeypatch):
    """方法论资产缺失 → 责任回退默认设计单位，闭环判定不误报完整。"""
    monkeypatch.setattr(
        control_chain,
        "load_review_methodology",
        lambda: {"responsible_parties": [], "closure_elements": {}, "closure_followups": {}},
    )
    chain = control_chain.build("任意文本", {}, {}, [])
    assert chain["责任"] == "设计单位"
    assert chain["闭环判定"]["status"] == "闭环不足"


@pytest.mark.unit
def test_engine_attaches_review_method_to_main_finding():
    issues = asyncio.run(
        ReviewAuditEngine().analyze(_ctx("标高不一致，套管预留未明确"), db=None)
    )
    assert issues
    method = issues[0].review_method
    assert method
    assert {"控制链", "五维审查", "处理建议", "闭环要求", "优先对象"} <= set(method)
    # 追加的 SOP finding 不重复挂载
    for extra in issues[1:]:
        assert not extra.review_method


# ── LLM 润色路径（fake router，不触网）─────────────────────────────
class _FakeRouter:
    def __init__(self, content: str | None = None, error: bool = False):
        self._content = content
        self._error = error

    async def route(self, engine_name: str, messages: list):
        if self._error:
            raise RuntimeError("provider down")

        class _Resp:
            content = self._content

        return _Resp()


def _engine_with_router(router: _FakeRouter) -> ReviewAuditEngine:
    engine = ReviewAuditEngine(redis=object())
    engine._router = router
    return engine


@pytest.mark.unit
def test_engine_polish_replaces_standard_question():
    engine = _engine_with_router(_FakeRouter(content="润色后的闭环问题"))
    issues = asyncio.run(engine.analyze(_ctx("标高不一致，套管预留未明确"), db=object()))
    assert issues[0].standard_question == "润色后的闭环问题"
    assert issues[0].description == "润色后的闭环问题"


@pytest.mark.unit
def test_engine_polish_falls_back_on_error():
    engine = _engine_with_router(_FakeRouter(error=True))
    issues = asyncio.run(engine.analyze(_ctx("标高不一致，套管预留未明确"), db=object()))
    assert issues[0].standard_question  # 回退模板原句，不为空


@pytest.mark.unit
def test_engine_polish_falls_back_on_empty_content():
    engine = _engine_with_router(_FakeRouter(content=""))
    issues = asyncio.run(engine.analyze(_ctx("标高不一致，套管预留未明确"), db=object()))
    assert issues[0].standard_question
