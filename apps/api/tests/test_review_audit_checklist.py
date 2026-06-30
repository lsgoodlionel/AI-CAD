"""会审审查 V3 — SOP 逐项清单核查（checklist_runner + audit_text 扩展 + 引擎追加 finding）"""
import asyncio

import pytest

from core.ai_review.review_audit import checklist_runner
from core.ai_review.review_audit.engine import audit_text, ReviewAuditEngine
from core.ai_review.review_audit.protocol_loader import load_review_checklists
from core.ai_review.base import DrawingContext

# audit_text V3 新增 key
V3_KEYS = {"审图目标", "未来影响", "逐项清单"}
# V1/V2 旧 key（向后兼容必须保留）
LEGACY_KEYS = {
    "专业判断", "定位信息", "核心concern", "问题归类", "接口复核",
    "风险等级", "建议动作", "证据缺口", "标准问题",
    "对象识别", "场景识别", "问题包", "文书输出",
}


# ── 知识资产 ────────────────────────────────────────────────────
@pytest.mark.unit
def test_checklists_cover_all_19_disciplines():
    cl = load_review_checklists()
    codes = {
        "ZH", "JG", "WH", "JZ", "ZJ", "RF", "GJG", "JDQ", "GPS", "ZS",
        "DQ", "NT", "MQ", "SWT", "JGUAN", "JN", "JK", "RD", "XF",
    }
    assert codes <= set(cl), f"缺失专业：{codes - set(cl)}"
    jg = cl["JG"]
    assert jg["protected_result"]
    assert jg["checklist"]
    # 每项必问问题/输出口径非空
    for item in jg["checklist"]:
        assert item["必问问题"] and item["输出口径"]


# ── checklist_runner ─────────────────────────────────────────────
@pytest.mark.unit
def test_runner_covers_when_location_present():
    """有定位 + 无冲突 → 覆盖率较高，无升级未覆盖项。"""
    result = checklist_runner.run(
        "JG",
        "JG-101 ③~⑤轴梁标高已明确，节点详图齐全",
        concerns=[{"label": "标高", "reason": "x"}],
        location={"drawings": ["JG-101"], "axes": ["③~⑤轴"], "levels": [], "nodes_or_systems": [], "spaces": []},
        scenario={"name": "正常审图", "priority_reason": ""},
        issue_class=["表达遗漏"],
        risk={"level": "低", "trigger": ""},
    )
    assert result["protected_result"]
    assert result["coverage"]["checked"] > 0
    assert result["coverage"]["ratio"] >= 0.8


@pytest.mark.unit
def test_runner_flags_uncovered_on_interface_and_constructability():
    """接口冲突 + 施工条件问题 → 对应升级清单项应未覆盖。"""
    result = checklist_runner.run(
        "JG",
        "套管预留未明确，接口未确定，安装顺序无法施工",
        concerns=[],
        location={"drawings": [], "levels": [], "axes": [], "nodes_or_systems": [], "spaces": []},
        scenario={"name": "施工落地", "priority_reason": ""},
        issue_class=["接口冲突", "施工条件问题"],
        risk={"level": "高", "trigger": "涉及安装顺序"},
    )
    upgrade_gaps = [u["检查项"] for u in result["coverage"]["uncovered"] if u["升级"]]
    assert upgrade_gaps, "应识别出升级类未覆盖清单项"


@pytest.mark.unit
def test_runner_future_impact_picks_highest_cost_stage():
    """同时命中表达遗漏(设计深化)与接口冲突(预留预埋) → 取高代价阶段 预留预埋。"""
    result = checklist_runner.run(
        "JG", "预留未明确", concerns=[],
        location={"drawings": ["JG-1"], "levels": [], "axes": [], "nodes_or_systems": [], "spaces": []},
        scenario={"name": "图间冲突", "priority_reason": ""},
        issue_class=["表达遗漏", "接口冲突"],
        risk={"level": "中", "trigger": ""},
    )
    assert result["future_impact"]["stage"] == "预留预埋"
    assert result["future_impact"]["effect"]


@pytest.mark.unit
def test_runner_unknown_discipline_degrades_to_empty():
    assert checklist_runner.run(
        "UNKNOWN", "x", [], {}, {}, [], {}
    ) == {}


# ── audit_text V3 扩展 + 向后兼容 ────────────────────────────────
@pytest.mark.unit
def test_audit_text_has_v3_keys_and_legacy_keys():
    data = audit_text(
        "地下二层③~⑤轴梁标高", "现平面与剖面标高不一致，未注明套管预留",
        discipline="JG",
    )
    assert V3_KEYS <= set(data)
    assert LEGACY_KEYS <= set(data)
    assert data["审图目标"]["protected_result"]
    assert data["未来影响"]["stage"]
    assert data["逐项清单"]["checked"] > 0


# ── ReviewAuditEngine 追加 finding + review_sop（离线，无 LLM）─────
def _ctx(text: str) -> DrawingContext:
    ctx = DrawingContext(
        drawing_id="d1", drawing_no="JG-101", discipline="structure",
        title="③~⑤轴梁标高问题", version="A", file_key="k", file_ext="pdf", project_id="p1",
    )
    ctx.extracted_text = text
    return ctx


@pytest.mark.unit
def test_engine_attaches_review_sop_to_main_finding():
    issues = asyncio.run(ReviewAuditEngine().analyze(_ctx("标高不一致，未注明套管预留"), db=None))
    assert issues
    sop = issues[0].review_sop
    assert sop and sop["protected_result"]
    assert "future_impact" in sop and "checklist" in sop


@pytest.mark.unit
def test_engine_extra_findings_capped_at_three():
    """高冲突文本触发多个升级清单项 → 追加 finding ≤ 3，全部 engine=review。"""
    text = "平面与剖面标高不一致，套管预留未明确，接口未确定，安装顺序无法施工，可能影响验收"
    issues = asyncio.run(ReviewAuditEngine().analyze(_ctx(text), db=None))
    extra = [i for i in issues[1:] if i.category == "会审审查·SOP清单"]
    assert len(extra) <= 3
    assert all(i.engine == "review" for i in issues)
    assert all(i.standard_question for i in extra)


@pytest.mark.unit
def test_engine_offline_runs_without_llm_or_db():
    """无 redis/db 时不调用 LLM，正常返回（review_question_writer 路径被跳过）。"""
    issues = asyncio.run(ReviewAuditEngine(redis=None).analyze(_ctx("管道与桥架打架"), db=None))
    assert issues and issues[0].engine == "review"
