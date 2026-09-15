"""建模能力要按**建模结果**说话，不能只看「有没有这类图」。

**实测**（大歌剧院 v85 模型页「建模能力与降级」）：

| 项 | 页面显示 | 实际 |
|---|---|---|
| 标高 | 图纸实测 | 12 层里 **11 层** `elevation_estimated`（默认/人工），只有 1 层是图纸读的 |
| 世界坐标 | 图纸实测 | 2309 张图里只有 **29 张**按工程坐标定位，1层五张结构图互相错开几十米 |

原因：`assess_capability` 只数角色 —— 有 1 张坐标基准图就判世界坐标 full，
有 1 张立面/剖面就判标高 full。输入具备不等于结果达成。
"""
from __future__ import annotations

import pytest

from services.partial_set import reconcile_capability

FULL = {"world_coords": "full", "floors": "full", "elevations": "full",
        "can_build": True, "degradations": []}


def _floor(label, *, estimated=False, placed=0, n_elements=10):
    return {"label": label, "elevation_estimated": estimated, "placed_drawings": placed,
            "elements": {"columns": [{}] * n_elements}}


@pytest.mark.unit
def test_estimated_elevations_downgrade_to_partial_and_say_how_many():
    floors = [_floor("1层"), _floor("2层", estimated=True), _floor("3层", estimated=True)]
    got = reconcile_capability(FULL, floors)
    assert got["elevations"] == "partial"
    assert any("2/3" in msg for msg in got["degradations"])


@pytest.mark.unit
def test_all_estimated_elevations_mean_no_drawing_basis():
    """一层都没从图纸读出来 —— 与世界坐标同一口径，判「缺图纸依据」而不是「降级」。"""
    floors = [_floor("1层", estimated=True), _floor("2层", estimated=True)]
    assert reconcile_capability(FULL, floors)["elevations"] == "none"


@pytest.mark.unit
def test_world_coords_partial_when_only_some_floors_are_placed():
    floors = [_floor("1层", placed=5), _floor("2层"), _floor("3层")]
    got = reconcile_capability(FULL, floors)
    assert got["world_coords"] == "partial"
    assert any("1/3" in msg for msg in got["degradations"])


@pytest.mark.unit
def test_world_coords_none_when_no_floor_is_placed():
    floors = [_floor("1层"), _floor("2层")]
    assert reconcile_capability(FULL, floors)["world_coords"] == "none"


@pytest.mark.unit
def test_floors_without_elements_do_not_count_against_world_coords():
    floors = [_floor("1层", placed=3), _floor("屋面", n_elements=0)]
    assert reconcile_capability(FULL, floors)["world_coords"] == "full"


@pytest.mark.unit
def test_achieved_outcome_keeps_full():
    floors = [_floor("1层", placed=2), _floor("2层", placed=1)]
    got = reconcile_capability(FULL, floors)
    assert (got["world_coords"], got["elevations"]) == ("full", "full")
    assert got["degradations"] == []


@pytest.mark.unit
def test_scene_payload_is_reconciled_with_floors(monkeypatch):
    """**接线用例**：`build_set_capability_payload` 拿到楼层就要校正 —— 只测纯函数，
    调用点没接上时上面几条照样全绿（「接线静默失效」本项目栽过）。"""
    from services import partial_set
    from services.model_builder import build_set_capability_payload

    monkeypatch.setattr(partial_set, "assess_capability", lambda counts: partial_set.SetCapability(
        world_coords="full", floors="full", elevations="full", can_build=True))
    payload = build_set_capability_payload(
        [], floors=[_floor("1层", placed=1), _floor("2层", estimated=True, placed=1)])
    assert payload["capability"]["elevations"] == "partial"


@pytest.mark.unit
def test_never_upgrades_and_does_not_mutate():
    """结果只能往下校，不能把「缺图纸依据」抬成 full；输入不被改动。"""
    base = {**FULL, "world_coords": "none", "degradations": ["无坐标基准图"]}
    snapshot = {**base, "degradations": list(base["degradations"])}
    got = reconcile_capability(base, [_floor("1层", placed=4)])
    assert got["world_coords"] == "none"
    assert base == snapshot
