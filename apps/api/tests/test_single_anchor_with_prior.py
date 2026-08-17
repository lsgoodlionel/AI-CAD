"""只有 1 个坐标标注的图，也能靠先验定位（J1 覆盖率的真瓶颈）。

**实测锚源画像**（上海大歌剧院，2311 张图）：

| 锚点数 | 图数 | 现状 |
|---|---:|---|
| **仅 1 个** | **10** | 相似变换 4 自由度、1 点只给 2 个方程 ⇒ **全被拒** |
| 2 个 | 1 | 恰定可解，但残差恒 0 无法验证 ⇒ 也被拒 |
| ≥3 个 | 2 | 可用 |

全项目**只有 13 张图有坐标标注**，其中 10 张只标了一处 ——
这是图纸信息量的客观限制，不是算法问题。但那 10 张各自握着一个
**真实的**世界坐标点，现在被完全浪费。

**补法**：比例与旋转从已有可信锚图继承（同一工程的图纸朝向一致是
工程常识，Phase I 实测该项目为 70.29°），1 个点只用来定平移。

**风险与控制**：朝向若不一致会摆错。所以
① 先验必须来自残差合格的锚图；② 结果标记 `prior_derived` 供下游降级；
③ 无先验时**不猜**，仍返回 None。
"""
from __future__ import annotations

import pytest

from services.drawing_anchor import solve_world_transform

PRIOR = {"scale": 1000.0, "rotation_deg": 70.29, "tx": 0.0, "ty": 0.0,
         "reflect": True, "rmse_m": 0.006}


def _pt(x, y, wx, wy):
    return {"x_norm": x, "y_norm": y, "world_x": wx, "world_y": wy}


@pytest.mark.unit
def test_single_point_without_prior_is_still_refused():
    """**无先验时不猜** —— 1 个点定不出 4 个自由度。"""
    assert solve_world_transform([_pt(0.5, 0.5, -6200.0, -6300.0)]) is None


@pytest.mark.unit
def test_single_point_with_prior_solves_translation():
    """**核心用例**：借先验的比例与旋转，1 个点定平移。"""
    got = solve_world_transform([_pt(0.5, 0.5, -6200.0, -6300.0)], prior=PRIOR)
    assert got is not None
    assert got["scale"] == pytest.approx(PRIOR["scale"])
    assert got["rotation_deg"] == pytest.approx(PRIOR["rotation_deg"])


@pytest.mark.unit
def test_prior_derived_result_is_flagged():
    """**降级必须可见**：借来的朝向不是本图实测的，下游要能分辨。"""
    got = solve_world_transform([_pt(0.5, 0.5, -6200.0, -6300.0)], prior=PRIOR)
    assert got["prior_derived"] is True


@pytest.mark.unit
def test_the_single_point_maps_exactly():
    """那一个点是**真实**世界坐标，解出的变换必须让它精确落位。"""
    from services.drawing_anchor import apply_similarity

    got = solve_world_transform([_pt(0.3, 0.7, -6200.0, -6300.0)], prior=PRIOR)
    x, y = apply_similarity((0.3, 0.7), got)
    assert x == pytest.approx(-6200.0, abs=0.01)
    assert y == pytest.approx(-6300.0, abs=0.01)


@pytest.mark.unit
def test_untrustworthy_prior_is_not_used():
    """先验必须来自**残差合格**的锚图 —— 拿疑似错的朝向去推，只会错得更远。"""
    bad = {**PRIOR, "rmse_m": 5.0, "suspect": True}
    assert solve_world_transform([_pt(0.5, 0.5, -6200.0, -6300.0)], prior=bad) is None


@pytest.mark.unit
def test_enough_points_ignore_the_prior():
    """本图自己够解就不借 —— 实测的朝向永远优于继承的。"""
    pts = [_pt(0.0, 0.0, 0.0, 0.0), _pt(1.0, 0.0, 100.0, 0.0),
           _pt(0.0, 1.0, 0.0, 100.0)]
    got = solve_world_transform(pts, prior=PRIOR)
    assert not got.get("prior_derived")
    assert got["rotation_deg"] != pytest.approx(PRIOR["rotation_deg"])
