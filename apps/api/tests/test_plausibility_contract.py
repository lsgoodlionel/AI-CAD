"""合理性分析的契约：结论必须带依据，跑不了不等于通过。

这两条是这一层的**全部价值所在**。没有依据的否定无法复核；把「缺数据」
算成「通过」，会让一道闸在无人察觉的情况下形同虚设 —— 本仓库为后者
付过两次代价（字段名写错被宽泛 except 吞掉、判读健全性检查从未生效）。
"""
from __future__ import annotations

import pytest

from core.model3d.plausibility import registry
from core.model3d.plausibility.model import PlausibilityModel
from core.model3d.plausibility.types import (
    Finding, Report, Rule, RuleNotApplicable, sort_findings,
)


def _finding(rule="r.x", severity="impossible", target="t"):
    return Finding(rule, severity, "columns", target, "说明", {"v": 1}, "依据")


@pytest.mark.unit
def test_finding_without_basis_is_rejected():
    with pytest.raises(ValueError, match="依据"):
        Finding("r.x", "impossible", "columns", "t", "说明", {}, "")


@pytest.mark.unit
def test_unknown_severity_is_rejected():
    with pytest.raises(ValueError):
        Finding("r.x", "很严重", "columns", "t", "说明", {}, "依据")


@pytest.mark.unit
def test_findings_sort_by_severity_then_rule_then_target():
    got = sort_findings([
        _finding("b", "suspect", "t2"), _finding("a", "impossible", "t9"),
        _finding("a", "impossible", "t1"), _finding("c", "implausible", "t3"),
    ])
    assert [(f.rule, f.target) for f in got] == [
        ("a", "t1"), ("a", "t9"), ("c", "t3"), ("b", "t2")]


@pytest.mark.unit
def test_rule_that_cannot_run_is_skipped_not_counted_as_passing():
    def _cannot(_model):
        raise RuleNotApplicable("这批图没有标高")

    rule = Rule("r.needs_levels", "要标高", "floor", "impossible", "依据", _cannot)
    report = registry.run(PlausibilityModel(), rules=(rule,))

    assert report.findings == ()
    assert report.skipped == {"r.needs_levels": "这批图没有标高"}
    assert "r.needs_levels" not in report.checked, "没查过的规则不该有分母"


@pytest.mark.unit
def test_a_buggy_rule_is_recorded_separately_from_missing_data():
    """规则自己抛异常 = 代码 bug，与「缺数据」分开记 —— 混在一起会把
    bug 说成「这个模型没数据」，从而永远查不出来。"""
    def _boom(_model):
        raise ZeroDivisionError("division by zero")

    rule = Rule("r.boom", "会炸", "model", "impossible", "依据", _boom)
    report = registry.run(PlausibilityModel(), rules=(rule,))

    assert report.skipped == {}
    assert "ZeroDivisionError" in report.errored["r.boom"]


@pytest.mark.unit
def test_missing_rule_pack_shows_up_as_errored_not_as_silence():
    """规则族导入失败时，报告里要看得见 —— 少跑一族却说「全部通过」最坏。"""
    registry.load_rules()                      # 触发一次真实导入
    report = registry.run(PlausibilityModel())
    for name, reason in registry._LOAD_ERRORS.items():
        assert f"<{name} 未加载>" in report.errored
        assert reason


@pytest.mark.unit
def test_report_counts_and_dict_are_stable():
    report = Report(findings=(_finding(severity="suspect"), _finding()))
    assert report.counts == {"impossible": 1, "implausible": 0, "suspect": 1}
    as_dict = report.as_dict()
    assert as_dict["findings"][0]["basis"] == "依据"
    assert set(as_dict) == {"counts", "checked", "skipped", "errored", "findings"}
