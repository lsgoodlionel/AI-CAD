"""RANSAC 鲁棒位姿 —— **标签给候选，几何来裁决**。

**为什么必须做**（上一轮实测）：标签归一化让分区体系（`1-1`）与
裸体系（`1`）能配对，交点从 0 → 135；但**跨分区错配**混在其中 ——
分图（四）的 `1-1…1-15` 归一成 `1…15`，而全局的 `1…36` 可能来自
其他分区，解出 scale=0.77（两图实际都是 1:150，本该 1.0）、rmse 8.58m。

**空间智能的做法**：不信标签，信几何。同一结构的对应点必然服从
同一个相似变换；跨分区的伪对应服从不了。⇒ RANSAC：
枚举最小样本（2 对点）拟合，统计内点，取内点最多的模型。

**确定性**：用**全枚举**而非随机采样 —— 本轮反复吃过
「结果依赖输入顺序 / 不可复现」的亏，同样的数据必须得到同样的解。
"""
from __future__ import annotations

import pytest

from services.pose_ransac import solve_pose_ransac


def _pts(items: dict) -> dict:
    return {k: (float(v[0]), float(v[1])) for k, v in items.items()}


@pytest.mark.unit
def test_clean_correspondence_solves_exactly():
    """无外点时应精确求解（退化为普通最小二乘）。"""
    local = _pts({("1", "A"): (0, 0), ("2", "A"): (8, 0),
                  ("1", "B"): (0, 5), ("2", "B"): (8, 5)})
    glob = _pts({k: (v[0] + 10, v[1] + 20) for k, v in local.items()})
    got = solve_pose_ransac(local, glob)
    assert got is not None
    assert got["scale"] == pytest.approx(1.0, abs=1e-6)
    assert got["rmse"] == pytest.approx(0.0, abs=1e-6)
    assert got["inliers"] == 4


@pytest.mark.unit
def test_majority_outliers_are_rejected():
    """**核心用例**:三对正确 + 三对跨分区错配 → 只用正确的那三对。

    这正是实测形态:135 个「共有交点」里混着错配。
    """
    good_local = {("1", "A"): (0, 0), ("2", "A"): (8, 0), ("3", "A"): (16, 0)}
    good_global = {k: (v[0] + 100, v[1] + 200) for k, v in good_local.items()}
    bad_local = {("7", "A"): (0, 0), ("8", "A"): (8, 0), ("9", "A"): (16, 0)}
    bad_global = {("7", "A"): (3, 55), ("8", "A"): (77, 9), ("9", "A"): (40, 91)}
    got = solve_pose_ransac(_pts({**good_local, **bad_local}),
                            _pts({**good_global, **bad_global}))
    assert got is not None
    assert got["inliers"] == 3
    assert got["scale"] == pytest.approx(1.0, abs=1e-6)
    assert got["tx"] == pytest.approx(100.0, abs=1e-6)


@pytest.mark.unit
def test_scale_and_rotation_recovered_under_noise():
    """含外点时仍要解出真实的缩放与旋转。"""
    import math

    base = {("1", "A"): (0, 0), ("2", "A"): (10, 0),
            ("1", "B"): (0, 6), ("2", "B"): (10, 6)}
    theta, scale = math.radians(30), 2.0
    glob = {}
    for k, (x, y) in base.items():
        glob[k] = (scale * (x * math.cos(theta) - y * math.sin(theta)) + 5,
                   scale * (x * math.sin(theta) + y * math.cos(theta)) - 3)
    noise = {("9", "Z"): (1, 1)}
    glob_noise = {("9", "Z"): (500, -500)}
    got = solve_pose_ransac(_pts({**base, **noise}),
                            _pts({**glob, **glob_noise}))
    assert got is not None
    assert got["scale"] == pytest.approx(2.0, rel=1e-6)
    assert got["rotation_deg"] % 360 == pytest.approx(30.0, abs=1e-4)
    assert got["inliers"] == 4


@pytest.mark.unit
def test_no_consistent_subset_returns_none():
    """**全是错配就诚实返回 None** —— 不硬凑一个解。"""
    local = _pts({("1", "A"): (0, 0), ("2", "A"): (8, 0),
                  ("3", "A"): (16, 0), ("4", "A"): (24, 0)})
    glob = _pts({("1", "A"): (0, 0), ("2", "A"): (90, 7),
                 ("3", "A"): (13, 88), ("4", "A"): (-40, 25)})
    assert solve_pose_ransac(local, glob, min_inliers=3) is None


@pytest.mark.unit
def test_deterministic_across_key_order():
    """**同样的数据必须得到同样的解** —— 全枚举而非随机采样。"""
    local = _pts({("1", "A"): (0, 0), ("2", "A"): (8, 0),
                  ("3", "A"): (16, 0), ("1", "B"): (0, 5)})
    glob = _pts({k: (v[0] + 7, v[1] + 3) for k, v in local.items()})
    first = solve_pose_ransac(local, glob)
    shuffled = dict(reversed(list(local.items())))
    second = solve_pose_ransac(shuffled, glob)
    assert first == second


@pytest.mark.unit
def test_too_few_points():
    assert solve_pose_ransac({}, {}) is None
    assert solve_pose_ransac(_pts({("1", "A"): (0, 0)}),
                             _pts({("1", "A"): (0, 0)})) is None


# ── 内点率门限（实测逼出：14/135 = 10% 不足以采纳）──────────────

@pytest.mark.unit
def test_low_inlier_ratio_is_reported_not_adopted():
    """**实测**:外点图用 RANSAC 得 rmse 0.297m(比标签配对的 8.58m 好 29 倍),
    但内点仅 **14/135 = 10%**,且 scale=0.824 而两图比例尺都记为 1:150。

    低内点率下伪解风险高,而**无法从数据区分**「比例尺记录错」与
    「对应仍错」⇒ 不自动采纳,如实报出让人判断(降级必须可见)。
    """
    from services.pose_ransac import solve_pose_ransac

    # 4 对一致 + 16 对随机噪声 → 内点率 20%
    local = {(str(i), "A"): (float(i), 0.0) for i in range(20)}
    glob = {(str(i), "A"): (float(i) + 5.0, 0.0) for i in range(4)}
    glob.update({(str(i), "A"): (float(i * 37 % 91), float(i * 53 % 71))
                 for i in range(4, 20)})
    got = solve_pose_ransac(local, glob)
    assert got is not None
    assert got["inlier_ratio"] == pytest.approx(4 / 20, abs=0.01)
    assert not got["confident"], "内点率 20% 不该标为可信"


@pytest.mark.unit
def test_high_inlier_ratio_is_confident():
    """内点率高 → 标为可信,可直接采纳。"""
    from services.pose_ransac import solve_pose_ransac

    local = {(str(i), "A"): (float(i), 0.0) for i in range(10)}
    glob = {k: (v[0] + 5.0, v[1]) for k, v in local.items()}
    got = solve_pose_ransac(local, glob)
    assert got["inlier_ratio"] == pytest.approx(1.0)
    assert got["confident"]


# ── scale 是比内点率更强的信号（裸标签图归组实测）──────────────

@pytest.mark.unit
def test_near_unit_scale_with_low_ratio_is_still_confident():
    """**实测**:裸标签图归入分区 2,内点仅 **33%** 但 **scale=0.988**、
    另一张 11% 而 **scale=1.000**;而此前的伪解 scale=**0.824**。

    **图纸是等比绘制的** —— 同一工程的图之间 scale 应接近 1,
    1~2% 是测量噪声级别。内点率低往往只说明**覆盖不全**
    (这几张图只覆盖分区的一部分),不等于错配。

    ⇒ 判据改为**组合**:scale 接近 1 且内点数够,即可信;
    单看内点率会把真匹配误杀。
    """
    from services.pose_ransac import solve_pose_ransac

    # 8 对一致（scale=1）+ 16 对噪声 → 内点率 33%
    local = {(str(i), "A"): (float(i * 3), 0.0) for i in range(24)}
    glob = {(str(i), "A"): (float(i * 3) + 5.0, 0.0) for i in range(8)}
    glob.update({(str(i), "A"): (float(i * 41 % 97), float(i * 59 % 83))
                 for i in range(8, 24)})
    got = solve_pose_ransac(local, glob)
    assert got["scale"] == pytest.approx(1.0, abs=0.02)
    assert got["inlier_ratio"] < 0.5
    assert got["confident"], "scale≈1 且内点数够，应判为可信"


@pytest.mark.unit
def test_off_scale_stays_unconfident_even_with_more_inliers():
    """**比例明显偏离仍不可信** —— 那正是伪解的指纹(实测 0.824)。"""
    from services.pose_ransac import solve_pose_ransac

    # 内点率必须**不足**，才测得到 scale 判据（全一致会走内点率通道）
    local = {(str(i), "A"): (float(i * 3), 0.0) for i in range(24)}
    glob = {(str(i), "A"): (float(i * 3) * 0.82 + 5.0, 0.0) for i in range(10)}
    glob.update({(str(i), "A"): (float(i * 41 % 97), float(i * 59 % 83))
                 for i in range(10, 24)})
    got = solve_pose_ransac(local, glob)
    assert got["scale"] == pytest.approx(0.82, abs=0.01)
    assert got["inlier_ratio"] < 0.5
    assert not got["confident"], "比例偏离 18% 不该判为可信"


@pytest.mark.unit
def test_too_few_inliers_never_confident():
    """**绝对数下限** —— 3 个点凑出的 scale≈1 不足为凭。"""
    from services.pose_ransac import solve_pose_ransac

    local = {(str(i), "A"): (float(i * 3), 0.0) for i in range(20)}
    glob = {(str(i), "A"): (float(i * 3) + 5.0, 0.0) for i in range(3)}
    glob.update({(str(i), "A"): (float(i * 41 % 97), float(i * 59 % 83))
                 for i in range(3, 20)})
    got = solve_pose_ransac(local, glob)
    if got is not None:
        assert not got["confident"]
