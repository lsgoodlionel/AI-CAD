"""部分图纸建模:没有整套图也要能出模型,但**必须如实说明降级到什么程度**。

**为什么必须支持**:工程上拿到整套竣工图是少数情况。常见的是
只有几张平面图、或只有结构图没有建筑图、或图纸分批到货。
系统若要求「齐了才能建」,大部分时候就用不上。

**设计原则**:每一阶段(P0 坐标基准 / P1 楼层骨架 / P2 楼层标高)
缺失时都有明确的降级路径,且降级结果**打上标记**——
绝不让降级产物冒充完整成果。

这与「兜底标准是国标」是同一条:图号体系可以缺、可以陌生,
但只要图上有轴号圈(§8.0.2)、有标高链(§11.8),就能往下走。
"""
from __future__ import annotations

import pytest

from services.drawing_role import (
    ROLE_COMPONENT_SOURCE, ROLE_COORDINATE_BASE, ROLE_ELEVATION_REFERENCE,
    ROLE_FLOOR_SKELETON,
)
from services.partial_set import (
    CAPABILITY_FULL, CAPABILITY_NONE, CAPABILITY_PARTIAL,
    assess_capability, plan_stages,
)


def _roles(**counts) -> dict[str, int]:
    return counts


# ── 能力评估 ────────────────────────────────────────────────────

@pytest.mark.unit
def test_full_set_reports_full_capability():
    got = assess_capability(_roles(**{
        ROLE_COORDINATE_BASE: 3, ROLE_FLOOR_SKELETON: 10,
        ROLE_ELEVATION_REFERENCE: 5, ROLE_COMPONENT_SOURCE: 900}))
    assert got.world_coords == CAPABILITY_FULL
    assert got.floors == CAPABILITY_FULL
    assert got.elevations == CAPABILITY_FULL


@pytest.mark.unit
def test_no_positioning_drawing_means_no_world_coordinates():
    """没有坐标基准图 —— 只能做**相对几何**,不能声称有世界坐标。"""
    got = assess_capability(_roles(**{ROLE_FLOOR_SKELETON: 4,
                                      ROLE_COMPONENT_SOURCE: 50}))
    assert got.world_coords == CAPABILITY_NONE
    assert "world" in " ".join(got.degradations).lower() or \
           any("世界坐标" in d for d in got.degradations)


@pytest.mark.unit
def test_no_elevation_drawing_falls_back_to_default_heights():
    """没有立面/剖面 —— 层高只能用默认值,**必须标出来**。"""
    got = assess_capability(_roles(**{ROLE_COORDINATE_BASE: 1,
                                      ROLE_FLOOR_SKELETON: 6,
                                      ROLE_COMPONENT_SOURCE: 40}))
    assert got.elevations == CAPABILITY_NONE
    assert any("默认" in d for d in got.degradations)


@pytest.mark.unit
def test_component_drawings_alone_can_still_infer_floors():
    """只有专业平面图、没有完整平面图 —— 仍可从图名归纳楼层,但是降级。"""
    got = assess_capability(_roles(**{ROLE_COMPONENT_SOURCE: 120}))
    assert got.floors == CAPABILITY_PARTIAL
    assert got.can_build is True


@pytest.mark.unit
def test_only_details_cannot_build_anything():
    """全是详图 —— 建不出模型,如实说不能建。"""
    from services.drawing_role import ROLE_DETAIL

    got = assess_capability(_roles(**{ROLE_DETAIL: 80}))
    assert got.can_build is False


@pytest.mark.unit
def test_empty_input_is_safe():
    got = assess_capability({})
    assert got.can_build is False
    assert got.world_coords == CAPABILITY_NONE


# ── 阶段调度 ────────────────────────────────────────────────────

@pytest.mark.unit
def test_stages_are_ordered_by_dependency():
    """P0 → P1 → P2 → P3:上游没定,下游做多少都要返工。"""
    stages = plan_stages(_roles(**{
        ROLE_COORDINATE_BASE: 2, ROLE_FLOOR_SKELETON: 8,
        ROLE_ELEVATION_REFERENCE: 4, ROLE_COMPONENT_SOURCE: 500}))
    assert [s["role"] for s in stages] == [
        ROLE_COORDINATE_BASE, ROLE_FLOOR_SKELETON,
        ROLE_ELEVATION_REFERENCE, ROLE_COMPONENT_SOURCE]


@pytest.mark.unit
def test_missing_stages_are_skipped_not_blocking():
    """缺的阶段直接跳过,**不阻断**后面的 —— 这是部分图纸建模的关键。"""
    stages = plan_stages(_roles(**{ROLE_COMPONENT_SOURCE: 300}))
    assert [s["role"] for s in stages] == [ROLE_COMPONENT_SOURCE]


@pytest.mark.unit
def test_stage_carries_its_drawing_count():
    stages = plan_stages(_roles(**{ROLE_COORDINATE_BASE: 3}))
    assert stages[0]["count"] == 3


@pytest.mark.unit
def test_non_geometric_never_enters_the_stage_plan():
    from services.drawing_role import ROLE_NON_GEOMETRIC, ROLE_UNKNOWN

    stages = plan_stages(_roles(**{ROLE_NON_GEOMETRIC: 600,
                                   ROLE_UNKNOWN: 200,
                                   ROLE_COMPONENT_SOURCE: 10}))
    assert [s["role"] for s in stages] == [ROLE_COMPONENT_SOURCE]
