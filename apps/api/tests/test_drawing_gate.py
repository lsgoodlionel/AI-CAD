"""图纸级准入闸的测试。

**测试的重点不是「拦得多」，而是「不该拦的一张都没拦」。**
本项目有过一次凭判断加阈值（`MAX_BANDS=40`）误杀 451 张核心平面图的事故，
所以每一条「不拦」的断言都是回归护栏，删不得。
"""
from __future__ import annotations

import pytest

from services.drawing_gate import (
    VERDICT_BUILD, VERDICT_DEGRADE, VERDICT_SKIP,
    CODE_NON_GEOMETRIC, CODE_DETAIL, CODE_COORDINATE_BASE,
    CODE_ELEVATION_REFERENCE, CODE_UNKNOWN_ROLE, CODE_SCALE_NOT_AUTHORITATIVE,
    DrawingSignals, GateResult, evaluate_drawing, gate_drawings, summarize,
)

#: 一张「什么都对」的楼层平面图：结构专业、比例 1:100、轴网齐全。
#: 1:100 → scale_m_pt = 100 × 25.4/72 / 1000 ≈ 0.03528
GOOD_SCALE = 0.03527777777777777


def _plan(**kw) -> DrawingSignals:
    base = dict(
        drawing_id="d-plan",
        drawing_no="S-33-01",
        title="结构-竣工图--三层结构平面图",
        discipline="structure",
        scale_m_pt=GOOD_SCALE,
        transform_confidence=1.0,
        axis_count=24,
        circle_count=30,
    )
    base.update(kw)
    return DrawingSignals(**base)


# ── 基线：能建的图必须判 build ───────────────────────────────────

@pytest.mark.unit
def test_normal_floor_plan_passes_without_reasons():
    """信号齐全的楼层平面图直接放行，依据链为空。"""
    # Arrange
    signals = _plan()

    # Act
    result = evaluate_drawing(signals)

    # Assert
    assert result.verdict == VERDICT_BUILD
    assert result.reasons == ()
    assert result.scale_authoritative is True


# ── G1：非几何图 → skip（唯一一条够格拦截的判据）────────────────

@pytest.mark.unit
@pytest.mark.parametrize("title", [
    "结构施工图设计统一说明（二）",
    "建筑-竣工图--设计说明32",
    "电气-竣工图--电气修改通知单",
    "电气-竣工图--设备材料表",
    "图纸目录",
    "v变电所低压配电系统图（五）",
])
def test_non_geometric_titles_are_skipped(title: str):
    """说明/通知单/材料表/目录/系统图整页无建筑几何 —— 拦截。

    实测依据：`drawing_usable_v1.json` 36 张里该判据拦 10 张，**误伤 0**。
    """
    # Arrange
    signals = _plan(title=title, drawing_no="A-00-06A")

    # Act
    result = evaluate_drawing(signals)

    # Assert
    assert result.verdict == VERDICT_SKIP
    assert CODE_NON_GEOMETRIC in {r.code for r in result.reasons}


# ── G2~G5：判不准的一律降级，绝不拦截 ───────────────────────────

@pytest.mark.unit
def test_detail_is_degraded_not_skipped():
    """详图的 22% 误伤落在判读者 74% 重测信度内 —— 不足以拦截。

    实测依据：`drawing_usable_v1.json` 该判据拦 9 张、误伤 2 张
    （`剪力墙详图`、`楼梯ST-24、25结构详图` 判读认为可用）。
    """
    # Arrange
    signals = _plan(title="建筑-竣工图--楼梯ST-11放大详图(一)")

    # Act
    result = evaluate_drawing(signals)

    # Assert
    assert result.verdict == VERDICT_DEGRADE
    assert CODE_DETAIL in {r.code for r in result.reasons}


@pytest.mark.unit
def test_elevation_reference_is_degraded():
    """剖立面是 z 的来源，不是构件来源 —— 降级而非拦截。"""
    # Arrange
    signals = _plan(title="建筑-竣工图--1-1剖面图")

    # Act
    result = evaluate_drawing(signals)

    # Assert
    assert result.verdict == VERDICT_DEGRADE
    assert CODE_ELEVATION_REFERENCE in {r.code for r in result.reasons}


@pytest.mark.unit
def test_coordinate_base_is_degraded():
    """轴网定位图用于定位，本身不贡献构件 —— 降级。"""
    # Arrange
    signals = _plan(title="A-01-02A 正交轴网定位图", drawing_no="A-01-02A")

    # Act
    result = evaluate_drawing(signals)

    # Assert
    assert result.verdict == VERDICT_DEGRADE
    assert CODE_COORDINATE_BASE in {r.code for r in result.reasons}


@pytest.mark.unit
def test_unknown_role_is_degraded_never_skipped():
    """判不出就说判不出（蓝图 §7 约束 5）—— `unknown` 永远不拦截。

    本样本上 `unknown` 4 张恰好全是不可用的（误伤 0），
    **仍然不升级为拦截** —— 4 张不足以支撑，且判不出不等于不该建。
    """
    # Arrange：图名与图号都给不出线索
    signals = _plan(title="", drawing_no="", discipline="general")

    # Act
    result = evaluate_drawing(signals)

    # Assert
    assert result.verdict == VERDICT_DEGRADE
    assert CODE_UNKNOWN_ROLE in {r.code for r in result.reasons}


# ── G6：比例不可信 → 降级（35% 误伤，只够降级）──────────────────

@pytest.mark.unit
@pytest.mark.parametrize("scale_m_pt,confidence", [
    (0.89744, 1.0),      # 1:2544，超出 scale_gate 上界
    (GOOD_SCALE, 0.0),   # 比例合理但置信为零
    (None, None),        # 根本没有变换
])
def test_untrustworthy_scale_degrades(scale_m_pt, confidence):
    """比例不可信 → 尺寸会整体错，但几何还在 —— 降级，不丢图。

    实测依据：`drawing_scale_v1.json` 56 条，该判据拦 34 条、
    误伤 12 条 = **35%**，只够降级。
    """
    # Arrange
    signals = _plan(scale_m_pt=scale_m_pt, transform_confidence=confidence)

    # Act
    result = evaluate_drawing(signals)

    # Assert
    assert result.verdict == VERDICT_DEGRADE
    assert CODE_SCALE_NOT_AUTHORITATIVE in {r.code for r in result.reasons}
    assert result.scale_authoritative is False


# ── 被实测驳回的判据：必须**不**影响判定（回归护栏）─────────────

@pytest.mark.unit
def test_zero_axis_count_alone_does_not_block():
    """`axis_count==0` 不作判据。

    实测依据：`axis_grid_presence_v1.json` 80 条 —— 系统识别 0 条轴线的
    40 张里有 **4 张实际有轴网（10% 误伤）**，外推全库 axis_count=0 的
    2045 张里约 204 张有轴网。拿它拦截等于重演 `MAX_BANDS=40` 事故。
    """
    # Arrange
    signals = _plan(axis_count=0, circle_count=0)

    # Act
    result = evaluate_drawing(signals)

    # Assert
    assert result.verdict == VERDICT_BUILD
    assert result.reasons == ()


@pytest.mark.unit
def test_general_discipline_alone_does_not_block():
    """`discipline=='general'` 不作判据。

    实测依据：`has_discipline_v1.json` 90 条 —— 该判据拦 20 张，
    其中 **11 张确实是图纸（55% 误伤）**。
    """
    # Arrange
    signals = _plan(discipline="general")

    # Act
    result = evaluate_drawing(signals)

    # Assert
    assert result.verdict == VERDICT_BUILD


# ── 依据链、优先级、不可变性 ────────────────────────────────────

@pytest.mark.unit
def test_skip_wins_over_degrade_and_both_reasons_kept():
    """拦截压过降级，但**两条依据都留在链上** —— 事后能追责到判据。"""
    # Arrange：既是说明（拦截）又没有可信比例（降级）
    signals = _plan(title="设计说明6", scale_m_pt=None,
                    transform_confidence=None)

    # Act
    result = evaluate_drawing(signals)

    # Assert
    codes = {r.code for r in result.reasons}
    assert result.verdict == VERDICT_SKIP
    assert codes == {CODE_NON_GEOMETRIC, CODE_SCALE_NOT_AUTHORITATIVE}


@pytest.mark.unit
def test_every_reason_carries_measured_basis():
    """每条依据都必须带**实测出处**，否则就是又一次凭判断加阈值。"""
    # Arrange
    signals = _plan(title="设计说明6", scale_m_pt=None,
                    transform_confidence=None)

    # Act
    result = evaluate_drawing(signals)

    # Assert
    for reason in result.reasons:
        assert reason.basis, f"{reason.code} 缺实测依据"
        assert "_v1.json" in reason.basis


@pytest.mark.unit
def test_result_is_immutable():
    """判定结果不可变 —— 下游改不动上游的结论。"""
    # Arrange
    result = evaluate_drawing(_plan())

    # Act / Assert
    with pytest.raises(Exception):
        result.verdict = VERDICT_SKIP  # type: ignore[misc]
    assert isinstance(result, GateResult)


@pytest.mark.unit
def test_explicit_role_overrides_classification():
    """调用方已经算过角色时不重算 —— 避免同一张图两处判出不同角色。"""
    # Arrange：图名像平面图，但调用方给定角色为非几何
    signals = _plan(role="non_geometric")

    # Act
    result = evaluate_drawing(signals)

    # Assert
    assert result.verdict == VERDICT_SKIP
    assert result.role == "non_geometric"
    assert result.role_source == "given"


@pytest.mark.unit
def test_content_evidence_beats_title():
    """内容证据压过图名（与 `drawing_role` 同一优先级）。"""
    # Arrange：图名叫详图，但内容是成组一致的世界坐标 + 大量轴号圈
    signals = _plan(
        title="某某放大详图",
        evidence={"axis_circle_count": 120, "transform_inliers": 8,
                  "transform_rmse_m": 0.006},
    )

    # Act
    result = evaluate_drawing(signals)

    # Assert
    assert result.role == "coordinate_base"
    assert CODE_COORDINATE_BASE in {r.code for r in result.reasons}


# ── 批量与统计 ──────────────────────────────────────────────────

@pytest.mark.unit
def test_gate_drawings_preserves_order_and_count():
    """批量判定逐张返回，不吞不并。"""
    # Arrange
    batch = [_plan(drawing_id="a"), _plan(drawing_id="b", title="设计说明"),
             _plan(drawing_id="c", title="1-1剖面图")]

    # Act
    results = gate_drawings(batch)

    # Assert
    assert [r.drawing_id for r in results] == ["a", "b", "c"]
    assert [r.verdict for r in results] == [
        VERDICT_BUILD, VERDICT_SKIP, VERDICT_DEGRADE]


@pytest.mark.unit
def test_summarize_counts_by_verdict_and_reason():
    """统计口径固定，便于接线前后对比拦截量。"""
    # Arrange
    results = gate_drawings([
        _plan(drawing_id="a"),
        _plan(drawing_id="b", title="设计说明"),
        _plan(drawing_id="c", title="材料做法表"),
        _plan(drawing_id="d", title="1-1剖面图"),
    ])

    # Act
    stats = summarize(results)

    # Assert
    assert stats["total"] == 4
    assert stats["by_verdict"][VERDICT_SKIP] == 2
    assert stats["by_verdict"][VERDICT_BUILD] == 1
    assert stats["by_verdict"][VERDICT_DEGRADE] == 1
    assert stats["by_reason"][CODE_NON_GEOMETRIC] == 2


@pytest.mark.unit
def test_gate_drawings_logs_interception(caplog):
    """拦截量必须记 log，不静默。"""
    # Arrange
    import logging
    caplog.set_level(logging.INFO, logger="services.drawing_gate")

    # Act
    gate_drawings([_plan(drawing_id="a"), _plan(drawing_id="b", title="设计说明")])

    # Assert
    assert any("drawing_gate" in rec.name and "skip" in rec.getMessage()
               for rec in caplog.records)


@pytest.mark.unit
def test_gate_drawings_accepts_mappings():
    """也接受普通 dict —— 调用方不必先造 dataclass。"""
    # Arrange
    batch = [{"drawing_id": "m1", "title": "设计说明", "drawing_no": "A-00-01"}]

    # Act
    results = gate_drawings(batch)

    # Assert
    assert results[0].verdict == VERDICT_SKIP


@pytest.mark.unit
def test_empty_batch_is_safe():
    """空批次不炸，统计为零。"""
    # Arrange / Act
    results = gate_drawings([])
    stats = summarize(results)

    # Assert
    assert results == ()
    assert stats["total"] == 0
