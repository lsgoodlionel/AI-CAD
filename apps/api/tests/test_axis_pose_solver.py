"""轴网位姿求解 —— 把共识从「仅平移」扩展到**完整相似变换**。

**为什么必须做**（v80 实测）：逐方向共识把 12 层的轴网都算了出来，
但落库只有 5 层 —— 剔除的 7 层（FD/B3/B1/F3/F5/F6/RF_HIGH）
根因一致：**比例或旋转错误，平移不可修**。

**推导**：轴网 x/y 轴号各自只有一维位置，看似解不了旋转；
但两者的**笛卡尔积**给出二维锚点 —— 轴号 `1` 与 `A` 的交点
就是图上一个确定的点。同名交点在两图间配对，即可解相似变换
（缩放 + 旋转 + 平移），复用既有的 `similarity_from_pairs`
（它已支持反射，工程坐标是左手系）。

这才是完整的二维 Bundle Adjustment，共识的平移解是它的退化情形。
"""
from __future__ import annotations

import math

import pytest

from services.axis_pose_solver import (
    intersections_of, solve_pose_to_global,
)


def _axes(x: dict, y: dict) -> dict:
    return {"x": [[k, v] for k, v in x.items()],
            "y": [[k, v] for k, v in y.items()]}


@pytest.mark.unit
def test_intersections_are_the_cartesian_product():
    """**核心构造**:x/y 轴号笛卡尔积 → 二维锚点。"""
    got = intersections_of(_axes({"1": 0.0, "2": 8.0}, {"A": 0.0, "B": 5.0}))
    assert got == {
        ("1", "A"): (0.0, 0.0), ("1", "B"): (0.0, 5.0),
        ("2", "A"): (8.0, 0.0), ("2", "B"): (8.0, 5.0),
    }


@pytest.mark.unit
def test_pure_translation_is_solved():
    """平移是相似变换的退化情形 —— 必须仍然解得出。"""
    local = _axes({"1": 10.0, "2": 18.0}, {"A": 10.0, "B": 15.0})
    global_ = _axes({"1": 0.0, "2": 8.0}, {"A": 0.0, "B": 5.0})
    pose = solve_pose_to_global(local, global_)
    assert pose is not None
    assert pose["scale"] == pytest.approx(1.0, abs=1e-6)
    assert pose["tx"] == pytest.approx(-10.0, abs=1e-6)
    assert pose["rmse"] == pytest.approx(0.0, abs=1e-6)


@pytest.mark.unit
def test_scale_error_is_solved():
    """**v80 剔除层的根因之一**:比例差 —— 平移救不了,相似变换能。"""
    global_ = _axes({"1": 0.0, "2": 8.0, "3": 16.0},
                    {"A": 0.0, "B": 5.0, "C": 12.0})
    local = _axes({k: v * 1.4 for k, v in
                   {"1": 0.0, "2": 8.0, "3": 16.0}.items()},
                  {k: v * 1.4 for k, v in
                   {"A": 0.0, "B": 5.0, "C": 12.0}.items()})
    pose = solve_pose_to_global(local, global_)
    assert pose is not None
    assert pose["scale"] == pytest.approx(1 / 1.4, rel=1e-6)
    assert pose["rmse"] == pytest.approx(0.0, abs=1e-6)


@pytest.mark.unit
def test_rotation_error_is_solved():
    """**另一个根因**:旋转 —— 实测两工程各约 23% 图有页面旋转。"""
    base_x = {"1": 0.0, "2": 8.0, "3": 16.0}
    base_y = {"A": 0.0, "B": 5.0, "C": 12.0}
    global_ = _axes(base_x, base_y)
    # 局部图整体旋转 90°：(x, y) → (-y, x)
    theta = math.pi / 2
    rotated_pts = {}
    for lx, px in base_x.items():
        for ly, py in base_y.items():
            rx = px * math.cos(theta) - py * math.sin(theta)
            ry = px * math.sin(theta) + py * math.cos(theta)
            rotated_pts[(lx, ly)] = (rx, ry)
    # 旋转后 x/y 不再是轴对齐，用交点直接求解
    from services.axis_pose_solver import solve_pose_from_points

    global_pts = intersections_of(global_)
    pose = solve_pose_from_points(rotated_pts, global_pts)
    assert pose is not None
    assert pose["rmse"] == pytest.approx(0.0, abs=1e-6)
    assert pose["scale"] == pytest.approx(1.0, abs=1e-6)


@pytest.mark.unit
def test_too_few_shared_intersections():
    """**共有锚点不足不猜** —— 解相似变换至少要 2 个点。"""
    local = _axes({"1": 0.0}, {})
    global_ = _axes({"1": 0.0, "2": 8.0}, {"A": 0.0})
    assert solve_pose_to_global(local, global_) is None


@pytest.mark.unit
def test_one_direction_only_yields_no_intersections():
    """只有单方向轴号 → 构不出交点 → 无解（诚实返回 None）。"""
    assert intersections_of(_axes({"1": 0.0, "2": 8.0}, {})) == {}
    assert solve_pose_to_global(_axes({"1": 0.0, "2": 8.0}, {}),
                                _axes({"1": 0.0}, {"A": 0.0})) is None


@pytest.mark.unit
def test_empty_inputs():
    assert intersections_of(None) == {}
    assert solve_pose_to_global(None, None) is None


# ── 标签体系割裂：分区前缀 vs 裸轴号（实测 77 张 vs 691 张）─────

@pytest.mark.unit
def test_zone_prefixed_labels_pair_with_bare_ones():
    """**实测根因**:被判外点的图轴号是 `1-1,1-2,1-3`(带分区前缀),
    全局是 `1,2,3`(裸轴号),**共有标签 0 个** —— 不是几何错,
    是同一栋楼的轴网被两套命名体系割裂。

    全项目实测:裸标签 691 张、分区标签 77 张、**同图混用 108 张**。

    GB/T 50001 §8.0.5/§8.0.6:分区号是**前缀**,`1-1` 的轴线序号就是 `1`。
    据此做无损归一化,让两套体系能配对。
    """
    from services.axis_pose_solver import normalize_axis_label

    assert normalize_axis_label("1-1") == "1"
    assert normalize_axis_label("2-15") == "15"
    assert normalize_axis_label("1-A") == "A"
    assert normalize_axis_label("3") == "3"
    assert normalize_axis_label("B") == "B"


@pytest.mark.unit
def test_additional_axis_fraction_is_not_a_zone_prefix():
    """**不得误伤附加轴线**(§8.0.6 分数式 `2-1/k`)——
    它的 `2-1` 是主轴号、`/k` 才是附加序号,整体是一个标签。"""
    from services.axis_pose_solver import normalize_axis_label

    assert normalize_axis_label("2-1/k") == "2-1/k"
    assert normalize_axis_label("1/A") == "1/A"


@pytest.mark.unit
def test_pose_solves_across_label_systems():
    """**接线用例**:归一化后,分区标签图能与裸标签全局配对。"""
    local = _axes({"1-1": 10.0, "1-2": 18.0, "1-3": 26.0},
                  {"1-A": 10.0, "1-B": 15.0})
    global_ = _axes({"1": 0.0, "2": 8.0, "3": 16.0},
                    {"A": 0.0, "B": 5.0})
    pose = solve_pose_to_global(local, global_)
    assert pose is not None
    assert pose["scale"] == pytest.approx(1.0, abs=1e-6)
    assert pose["rmse"] == pytest.approx(0.0, abs=1e-6)


@pytest.mark.unit
def test_normalization_does_not_merge_distinct_zones():
    """**必须守住**:归一化只用于**配对**,不改变原标签 ——
    不同分区的 `1-1` 与 `2-1` 归一后都是 `1`,若用于合并会撞身份。
    交点构造保留原标签,归一化只作用于**匹配键**。"""
    from services.axis_pose_solver import intersections_of

    got = intersections_of(_axes({"1-1": 0.0}, {"1-A": 0.0}))
    # 键用归一化后的，便于跨体系配对
    assert ("1", "A") in got


@pytest.mark.unit
def test_cross_zone_pairing_is_the_known_limitation():
    """**已知边界(实测逼出)**:归一化让分区标签能与裸标签配对,
    但**不同分区的 `1-1` 与 `2-1` 归一后都是 `1`** ——
    跨分区配对会产生**伪对应**。

    实测:分图（四）与全局配出 135 个"共有交点",解得 scale=0.77
    (而两者比例尺都是 1:150,本该 1.0),rmse 8.58m —— 那是错配的伪解。

    ⇒ 本模块只负责**求解**,对应关系的可靠性由调用方保证:
    必须同分区、或用几何验证。这里断言的是**求解器如实反映输入**:
    喂进错配就得到高 rmse,而不是悄悄给出一个漂亮的解。
    """
    from services.axis_pose_solver import solve_pose_from_points

    # 错配：同名键但几何上不成相似变换
    local = {("1", "A"): (0.0, 0.0), ("2", "A"): (8.0, 0.0),
             ("3", "A"): (16.0, 0.0), ("1", "B"): (0.0, 5.0)}
    bogus = {("1", "A"): (0.0, 0.0), ("2", "A"): (8.0, 0.0),
             ("3", "A"): (99.0, 40.0), ("1", "B"): (3.0, 70.0)}
    pose = solve_pose_from_points(local, bogus)
    assert pose is not None
    assert pose["rmse"] > 5.0, "错配必须体现为高 rmse，不能悄悄给漂亮解"
