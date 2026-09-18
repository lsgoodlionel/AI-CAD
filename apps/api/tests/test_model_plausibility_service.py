"""编排层：按规则聚合、截断样例，**并且把截断说出来**。

一个模型可能出上万条结论（几百根柱同时自交就是几百条）。截断是必要的，
但「只存了 50 条」不能让人以为「只有 50 条」——计数必须完整，截断量必须可见。
"""
from __future__ import annotations

import pytest

from services import model_plausibility as svc
from core.model3d.plausibility.types import Finding, Report


def _findings(rule: str, n: int, severity: str = "impossible"):
    return [Finding(rule, severity, "columns", f"main/F1/columns/{i}",
                    "轮廓自交", {"area_m2": 0.0, "bbox_m": 0.36}, "鞋带公式")
            for i in range(n)]


@pytest.mark.unit
def test_counts_stay_complete_while_samples_are_capped():
    total = svc.PER_RULE_CAP * 3
    report = Report(findings=tuple(_findings("geom.self_intersecting_outline", total)))

    summary = svc.summarize(report)
    rule = summary["rules"][0]

    assert rule["count"] == total, "计数是完整的"
    assert len(rule["samples"]) == svc.PER_RULE_CAP, "样例被截断"
    assert summary["truncated"] == {"geom.self_intersecting_outline": total}
    assert summary["counts"]["impossible"] == total


@pytest.mark.unit
def test_nothing_is_marked_truncated_when_it_fits():
    summary = svc.summarize(Report(findings=tuple(_findings("geom.x", 3))))
    assert summary["truncated"] == {}
    assert len(summary["rules"][0]["samples"]) == 3


@pytest.mark.unit
def test_skipped_and_errored_survive_into_the_stored_report():
    """跑不了的和炸了的都要落库 —— 少跑一条却报「通过」是最坏的结果。"""
    report = Report(findings=(), checked={"r.a": 10},
                    skipped={"r.b": "这批图没有标高"}, errored={"r.c": "ZeroDivisionError: x"})
    summary = svc.summarize(report)

    assert summary["skipped"] == {"r.b": "这批图没有标高"}
    assert summary["errored"] == {"r.c": "ZeroDivisionError: x"}
    assert summary["checked"] == {"r.a": 10}


@pytest.mark.unit
def test_rules_are_sorted_and_carry_their_basis_and_kind_breakdown():
    report = Report(findings=tuple(
        _findings("b.rule", 1) + _findings("a.rule", 2)
        + [Finding("a.rule", "impossible", "slabs", "main/F1/slabs/0", "自交", {}, "鞋带公式")]))
    summary = svc.summarize(report)

    assert [r["rule"] for r in summary["rules"]] == ["a.rule", "b.rule"]
    assert summary["rules"][0]["kinds"] == {"columns": 2, "slabs": 1}
    assert summary["rules"][0]["basis"] == "鞋带公式"


@pytest.mark.unit
def test_analyze_scene_on_an_empty_scene_reports_no_findings_but_keeps_rule_bookkeeping():
    report = svc.analyze_scene({"buildings": []})
    assert report.findings == ()
    # 规则族没装齐时，errored 里必须看得见 —— 见 registry.load_rules
    assert isinstance(report.errored, dict)
