"""全局轴网共识 —— 把「不一致就丢弃」换成「联合求解」。

**当前架构的问题**（本轮实测）：每张图独立算变换，互不一致时就**丢弃**
（B1 层「轴网聚合采纳 4/12 张」、51 条轴线里 **32 条同名冲突**）。

**空间智能的做法**：同一场景的多个观测必须解释为**同一个 3D 结构**，
不一致不是丢弃的理由，而是**优化的目标**。轴网正是建筑的刚性骨架 ——
同一栋楼所有平面图共享同一套轴网，这给出天然的约束网络：

    共享轴号 165 个 | 图对约束 1,172,624 条 | 涉及 697 张图

**但有个致命陷阱**：轴号 `1` 出现在 **520 张图**上，
它们**不一定是同一根轴线** —— 不同单体/分区各有自己的 1 号轴。
盲目联合优化会把不同楼强行对齐。所以求解**必须按单体分组**。

本模块只做第一步（共识求解），不做完整 Bundle Adjustment：
对每个轴号取所有观测的**鲁棒中位数**作为全局位置，
再算出每图相对全局的偏移。中位数对外点天然稳健，
不必先做 RANSAC。
"""
from __future__ import annotations

import pytest

from services.global_axis_consensus import (
    align_offset, solve_global_axes,
)


@pytest.mark.unit
def test_consensus_of_consistent_observations():
    """**核心用例**:三张图对同一轴网的观测一致 → 共识即该值。"""
    obs = {
        "d1": {"1": 0.0, "2": 8.0},
        "d2": {"1": 0.0, "2": 8.0},
        "d3": {"1": 0.0, "2": 8.0},
    }
    assert solve_global_axes(obs) == {"1": 0.0, "2": 8.0}


@pytest.mark.unit
def test_median_resists_one_bad_drawing():
    """**中位数抗外点** —— 一张图变换算错,不该带偏全局。"""
    obs = {
        "d1": {"1": 0.0}, "d2": {"1": 0.0}, "d3": {"1": 0.0},
        "bad": {"1": 999.0},                       # 变换错误的图
    }
    assert solve_global_axes(obs)["1"] == 0.0


@pytest.mark.unit
def test_single_observation_is_kept():
    """只有一张图见过的轴号照样收 —— **孤证也是证据**,只是没有共识可校。"""
    assert solve_global_axes({"d1": {"9": 42.0}}) == {"9": 42.0}


@pytest.mark.unit
def test_align_offset_is_the_robust_shift():
    """每图相对全局的偏移 = 各轴号残差的中位数。"""
    global_axes = {"1": 0.0, "2": 8.0, "3": 16.0}
    drawing = {"1": 2.0, "2": 10.0, "3": 18.0}     # 整体平移 +2
    assert align_offset(drawing, global_axes) == pytest.approx(-2.0)


@pytest.mark.unit
def test_align_ignores_labels_not_in_global():
    """本图独有的轴号不参与求偏移（没有对照）。"""
    assert align_offset({"X": 5.0}, {"1": 0.0}) is None


@pytest.mark.unit
def test_align_returns_none_without_common_labels():
    assert align_offset({}, {"1": 0.0}) is None
    assert align_offset({"1": 0.0}, {}) is None


@pytest.mark.unit
def test_residual_reports_disagreement():
    """**残差要报出来** —— 它是「这张图与全局差多少」的度量,
    也是判断该图变换是否可信的依据。"""
    from services.global_axis_consensus import alignment_residual

    global_axes = {"1": 0.0, "2": 8.0}
    good = alignment_residual({"1": 0.1, "2": 8.1}, global_axes)
    bad = alignment_residual({"1": 0.0, "2": 80.0}, global_axes)
    assert good < bad
    assert good == pytest.approx(0.0, abs=0.05)


@pytest.mark.unit
def test_empty_input():
    assert solve_global_axes({}) == {}
    assert solve_global_axes(None) == {}


# ── 完整形态:两遍求解 + 残差门限(接入聚合前的最后一块)──────────

def _scene(x=None, y=None):
    return {"x": [[k, v] for k, v in (x or {}).items()],
            "y": [[k, v] for k, v in (y or {}).items()]}


@pytest.mark.unit
def test_scene_consensus_adopts_consistent_drawings():
    """**核心用例**:三张一致的图 → 全部采纳,轴网为并集。"""
    from services.global_axis_consensus import solve_scene_consensus

    got = solve_scene_consensus([
        ("d1", _scene(x={"1": 0.0, "2": 8.0})),
        ("d2", _scene(x={"2": 8.0, "3": 16.0})),
        ("d3", _scene(x={"3": 16.0, "4": 24.0})),
    ])
    assert got.outliers == []
    assert {e[0] for e in got.axes["x"]} == {"1", "2", "3", "4"}
    assert all(abs(dx) < 1e-6 and abs(dy) < 1e-6
               for dx, dy in got.shifts.values())


@pytest.mark.unit
def test_pure_translation_drawing_is_repaired_not_dropped():
    """**升级的核心**:整体平移 30 米的图,旧逻辑(最大一致组)只能丢弃,
    共识求解把它**对齐后收回** —— 丢掉的从来不是噪声,是没被调和的观测。"""
    from services.global_axis_consensus import solve_scene_consensus

    base = {"1": 0.0, "2": 8.0, "3": 16.0}
    shifted = {k: v + 30.0 for k, v in base.items()}
    got = solve_scene_consensus([
        ("g1", _scene(x=base)), ("g2", _scene(x=base)),
        ("g3", _scene(x=base)), ("moved", _scene(x=shifted)),
    ])
    assert got.outliers == []
    assert got.shifts["moved"][0] == pytest.approx(-30.0)
    # 对齐后位置回到基准,不是折中值
    assert dict(got.axes["x"]) == pytest.approx({"1": 0.0, "2": 8.0, "3": 16.0})


@pytest.mark.unit
def test_scrambled_drawing_is_gated_out():
    """**平移解释不了的图才是外点**(比例/旋转错):残差过门限 → 排除,
    其轴号不进全局(否则同名冲突卷土重来),但要**列名可查**。"""
    from services.global_axis_consensus import solve_scene_consensus

    base = {"1": 0.0, "2": 8.0, "3": 16.0}
    scrambled = {"1": 0.0, "2": 50.0, "3": 3.0}      # 平移救不了
    got = solve_scene_consensus([
        ("g1", _scene(x=base)), ("g2", _scene(x=base)),
        ("g3", _scene(x=base)), ("bad", _scene(x=scrambled)),
    ])
    assert got.outliers == ["bad"]
    assert dict(got.axes["x"]) == pytest.approx(base)
    assert "bad" not in got.shifts


@pytest.mark.unit
def test_unique_coverage_drawing_is_kept():
    """只覆盖独有区域的图(无共有轴号)照收 —— **互补不是矛盾**。"""
    from services.global_axis_consensus import solve_scene_consensus

    got = solve_scene_consensus([
        ("g1", _scene(x={"1": 0.0, "2": 8.0})),
        ("solo", _scene(x={"9": 90.0})),
    ])
    assert got.outliers == []
    assert dict(got.axes["x"])["9"] == 90.0


@pytest.mark.unit
def test_y_direction_solved_independently():
    """x/y 独立求解 —— 一图的 x 向可信不代表 y 向可信。"""
    from services.global_axis_consensus import solve_scene_consensus

    got = solve_scene_consensus([
        ("d1", _scene(x={"1": 0.0}, y={"A": 0.0, "B": 8.0})),
        ("d2", _scene(x={"1": 0.0}, y={"A": 5.0, "B": 13.0})),   # y 整体 +5
        ("d3", _scene(y={"A": 0.0, "B": 8.0})),
    ])
    assert got.outliers == []
    assert got.shifts["d2"][1] == pytest.approx(-5.0)
    assert dict(got.axes["y"])["A"] == pytest.approx(0.0)


@pytest.mark.unit
def test_scene_consensus_empty():
    from services.global_axis_consensus import solve_scene_consensus

    got = solve_scene_consensus([])
    assert got.axes == {"x": [], "y": []} and got.outliers == []
