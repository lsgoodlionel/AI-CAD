"""合理性引擎（AI 审图第 6 引擎）单测。

**不碰数据库、不跑真实图纸**：规则一律现场构造假 `Rule` 注入，
所以本文件不依赖仍在并行开发的 `rules_*` 规则族 —— 那几族此刻可能
还不存在，而「规则族缺失时引擎怎么办」本身就是要测的行为之一。
"""
from __future__ import annotations

import pytest

from core.ai_review.base import DrawingContext, IssueSeverity
from core.ai_review.plausibility_engine import (
    ELEMENTS_METADATA_KEY,
    SAMPLES_PER_ISSUE,
    SEVERITY_MAP,
    SINGLE_FLOOR_KEY,
    PlausibilityEngine,
    aggregate,
    report_to_issues,
    single_drawing_model,
)
from core.model3d.plausibility import registry
from core.model3d.plausibility.types import Finding, Report, Rule, RuleNotApplicable
from core.model3d.types import FloorElements


# ── 构造辅助 ────────────────────────────────────────────────────────────
def _square(x: float = 0.0, y: float = 0.0, size: float = 0.6) -> list[tuple[float, float]]:
    return [(x, y), (x + size, y), (x + size, y + size), (x, y + size)]


def _elements(columns: int = 2, walls: int = 1) -> dict:
    return {
        "columns": [{"outline": _square(i * 6.0)} for i in range(columns)],
        "walls": [{"path": [(0.0, 0.0), (6.0, 0.0)], "width": 0.2} for _ in range(walls)],
    }


def _ctx(**over) -> DrawingContext:
    base = dict(
        drawing_id="D-001", drawing_no="S-1-01", discipline="structure",
        title="一层结构平面图", version="A", file_key="k", file_ext="pdf",
        project_id="P-1",
    )
    base.update(over)
    return DrawingContext(**base)


def _finding(rule: str = "geo.self_intersecting", severity: str = "impossible",
             target: str = "D-001/single/columns/0", **over) -> Finding:
    kwargs = dict(
        rule=rule, severity=severity, kind="columns", target=target,
        detail="柱轮廓自交，面积恒为 0",
        evidence={"area_m2": 0.0, "vertices": 6},
        basis="PHYSICS: 鞋带公式在自交环上等值反号相消",
    )
    kwargs.update(over)
    return Finding(**kwargs)


def _rule(rule_id: str, check, *, severity: str = "impossible", scope: str = "element") -> Rule:
    return Rule(id=rule_id, title=rule_id, scope=scope, severity=severity,
                basis="测试用假规则", check=check)


# ── 单张图 → 模型 ───────────────────────────────────────────────────────
@pytest.mark.unit
def test_single_drawing_model_wraps_one_building_one_floor():
    # Arrange
    elements = _elements(columns=3, walls=2)

    # Act
    model = single_drawing_model(elements, drawing_id="D-001")

    # Assert
    assert len(model.buildings) == 1
    assert len(model.buildings[0].floors) == 1
    assert model.count() == 5
    assert model.count("columns") == 3


@pytest.mark.unit
def test_single_drawing_model_uid_carries_drawing_id_and_single_floor():
    # Arrange / Act
    model = single_drawing_model(_elements(columns=1, walls=0), drawing_id="D-001")

    # Assert
    uid = next(iter(model.elements("columns"))).uid
    assert uid == f"D-001/{SINGLE_FLOOR_KEY}/columns/0"


@pytest.mark.unit
def test_single_drawing_model_has_no_elevation_or_story_height():
    # 一张平面图本来就没有标高——这里必须是 None，不能替它补个默认值
    # Arrange / Act
    model = single_drawing_model(_elements(), drawing_id="D-001")

    # Assert
    floor = model.buildings[0].floors[0]
    assert floor.elevation_m is None
    assert floor.height_m is None
    assert floor.elevation_estimated is True


@pytest.mark.unit
def test_single_drawing_model_accepts_floor_elements_dataclass():
    # Arrange
    elements = FloorElements(columns=[{"outline": _square()}], walls=[])

    # Act
    model = single_drawing_model(elements, drawing_id="D-002")

    # Assert
    assert model.count("columns") == 1
    assert model.meta["drawing_id"] == "D-002"


# ── Finding → AIIssue ───────────────────────────────────────────────────
@pytest.mark.unit
@pytest.mark.parametrize(
    "severity,expected",
    [("impossible", IssueSeverity.CRITICAL),
     ("implausible", IssueSeverity.MAJOR),
     ("suspect", IssueSeverity.MINOR)],
)
def test_severity_maps_by_strength_of_basis(severity, expected):
    # Arrange
    report = Report(findings=(_finding(rule=f"r.{severity}", severity=severity),))

    # Act
    issues = report_to_issues(report)

    # Assert
    assert [i.severity for i in issues] == [expected]
    assert SEVERITY_MAP[severity] is expected


@pytest.mark.unit
def test_issue_description_carries_basis_and_evidence_numbers():
    # 没有数字的审图意见没法复核——依据与实测值都必须落在描述里
    # Arrange
    report = Report(findings=(_finding(evidence={"b_mm": 180, "limit_mm": 300}),),
                    checked={"geo.self_intersecting": 40})

    # Act
    issue = report_to_issues(report)[0]

    # Assert
    assert "鞋带公式" in issue.description
    assert "b_mm=180" in issue.description
    assert "limit_mm=300" in issue.description
    assert issue.regulation_ref == "PHYSICS: 鞋带公式在自交环上等值反号相消"


@pytest.mark.unit
def test_same_rule_hit_many_times_becomes_one_issue_with_true_count():
    # Arrange：同一规则命中 12 次
    findings = tuple(_finding(target=f"D-001/single/columns/{i}") for i in range(12))
    report = Report(findings=findings, checked={"geo.self_intersecting": 100})

    # Act
    issues = report_to_issues(report)

    # Assert
    assert len(issues) == 1
    assert "12" in issues[0].description
    listed = sum(1 for f in findings if f.target in issues[0].description)
    assert listed == SAMPLES_PER_ISSUE
    assert "其余 7" in issues[0].description


@pytest.mark.unit
def test_different_severities_of_one_rule_are_not_merged():
    # Arrange
    findings = (_finding(severity="impossible"),
                _finding(severity="suspect", target="D-001/single/columns/9"))

    # Act
    groups = aggregate(findings)

    # Assert
    assert {g.severity for g in groups} == {"impossible", "suspect"}
    assert all(g.count == 1 for g in groups)


# ── 降级必须可见 ─────────────────────────────────────────────────────────
@pytest.mark.unit
async def test_rule_not_applicable_is_surfaced_not_counted_as_pass():
    # Arrange：跨层规则在单图上跑不了
    def _needs_elevation(model):
        raise RuleNotApplicable("缺楼层标高，无法判断上下层支承")

    engine = PlausibilityEngine(rules=(_rule("support.column_below", _needs_elevation),))
    ctx = _ctx()
    ctx.ocr_metadata = {ELEMENTS_METADATA_KEY: _elements()}

    # Act
    issues = await engine.analyze(ctx, db=None)

    # Assert
    assert issues, "跑不了的规则被吞成空结果——那等于把『没查』说成『合格』"
    assert any("缺楼层标高" in i.description for i in issues)
    assert engine.last_meta["skipped"]["support.column_below"].startswith("缺楼层标高")


@pytest.mark.unit
async def test_rule_crash_does_not_kill_engine_and_stays_visible():
    # Arrange
    def _boom(model):
        raise ZeroDivisionError("division by zero")

    good = _rule("geo.ok", lambda m: [_finding(rule="geo.ok")])
    engine = PlausibilityEngine(rules=(_rule("geo.boom", _boom), good))
    ctx = _ctx()
    ctx.ocr_metadata = {ELEMENTS_METADATA_KEY: _elements()}

    # Act
    issues = await engine.analyze(ctx, db=None)

    # Assert：好规则照常出结论，坏规则的异常被带出来而不是吞掉
    assert any(i.severity is IssueSeverity.CRITICAL for i in issues)
    assert "ZeroDivisionError" in engine.last_meta["errored"]["geo.boom"]
    assert any("geo.boom" in i.description for i in issues)


@pytest.mark.unit
async def test_missing_rule_family_is_reported_not_swallowed(monkeypatch):
    # Arrange：模拟 rules_statics 还没写完 / 导入失败
    monkeypatch.setattr(registry, "_LOAD_ERRORS", {})

    def _fake_load():
        registry._LOAD_ERRORS["rules_statics"] = "ModuleNotFoundError: rules_statics"
        return ()

    monkeypatch.setattr(registry, "load_rules", _fake_load)
    engine = PlausibilityEngine()          # rules=None → 走 registry.load_rules
    ctx = _ctx()
    ctx.ocr_metadata = {ELEMENTS_METADATA_KEY: _elements()}

    # Act
    issues = await engine.analyze(ctx, db=None)

    # Assert
    assert issues, "规则族全缺时返回空列表 = 谎报『全部通过』"
    assert any("rules_statics" in i.description for i in issues)
    assert any("rules_statics" in key for key in engine.last_meta["errored"])


@pytest.mark.unit
async def test_engine_survives_rule_loading_crash(monkeypatch):
    # Arrange
    def _explode():
        raise ImportError("dgl 装不上")

    monkeypatch.setattr(registry, "load_rules", _explode)
    engine = PlausibilityEngine()
    ctx = _ctx()
    ctx.ocr_metadata = {ELEMENTS_METADATA_KEY: _elements()}

    # Act
    issues = await engine.analyze(ctx, db=None)

    # Assert
    assert any("dgl 装不上" in i.description for i in issues)
    assert engine.last_report is None


@pytest.mark.unit
async def test_no_recognized_elements_degrades_visibly():
    # Arrange：视觉引擎没产出构件
    engine = PlausibilityEngine(rules=())
    ctx = _ctx()

    # Act
    issues = await engine.analyze(ctx, db=None)

    # Assert
    assert len(issues) == 1
    assert issues[0].severity is IssueSeverity.INFO
    assert "未拿到构件识别结果" in issues[0].description


# ── 正常通路 ─────────────────────────────────────────────────────────────
@pytest.mark.unit
async def test_clean_model_produces_no_issues_but_records_denominator():
    # Arrange
    engine = PlausibilityEngine(rules=(_rule("geo.clean", lambda m: []),))
    ctx = _ctx()
    ctx.ocr_metadata = {ELEMENTS_METADATA_KEY: _elements(columns=2, walls=1)}

    # Act
    issues = await engine.analyze(ctx, db=None)

    # Assert
    assert issues == []
    assert engine.last_meta["checked"]["geo.clean"] == 3
    assert engine.last_meta["elements"] == 3


@pytest.mark.unit
async def test_custom_elements_provider_is_used():
    # Arrange
    engine = PlausibilityEngine(
        rules=(_rule("geo.hit", lambda m: [_finding(rule="geo.hit")]),),
        elements_provider=lambda ctx: _elements(columns=1, walls=0),
    )

    # Act
    issues = await engine.analyze(_ctx(), db=None)

    # Assert
    assert [i.engine for i in issues] == ["plausibility"]
    assert issues[0].severity is IssueSeverity.CRITICAL


@pytest.mark.unit
async def test_provider_failure_does_not_raise_out_of_engine():
    # Arrange
    def _bad(ctx):
        raise RuntimeError("识别结果读取超时")

    engine = PlausibilityEngine(rules=(), elements_provider=_bad)

    # Act
    issues = await engine.analyze(_ctx(), db=None)

    # Assert
    assert any("识别结果读取超时" in i.description for i in issues)
