"""坐标变换的比例尺合理性门禁。

**这是用户报告「模型轴线和结构位置不对、大量与图纸不匹配」的根因。**

实测证据:

| 层 | 来源图 | 变换比例分母 | 结果 |
|---|---|---:|---|
| F3 | `S-1-20-002C` | **115960** | 构件 X 范围到 250 米 |
| F3 | `S-1-20-103C` | **无变换** | 位置只能靠估 |

两者拼在一起,同层两图的构件中心差 **83 米**;F2 更差到 **103 米**。

全项目 **35 张**变换的比例超出国标区间,最离谱的
`A-10-07.1C` 比例分母 **3358662**(1:335 万)——构件会被扔到几百公里外。
**而这 35 张的 `confidence` 全是 1.00。**

根因两条:

1. `transform_from_geometry` 只检查 `scale <= 0`,**没有上限**;
2. `confidence = 带标签轴线数 / 轴线总数`,衡量的是**轴号识别质量**,
   与**比例尺对不对**毫无关系 —— 所以比例错到 1:335 万,置信度仍满分。

**国标依据**:GB/T 50001 **§6.0.4 表 6.0.4「绘图所用的比例」**,
常用比例 1:1 ~ 1:2000,可用比例延伸到 1:5000 量级。
1pt = 25.4/72 mm = 0.3528mm ⇒ `scale_m_pt = 分母 × 0.0003528`。

**宁可没有变换,也不能有错变换** —— 没有变换时下游会降级估位置,
而错变换会把构件放到几百公里外,且带着满分置信度骗过所有下游。
"""
from __future__ import annotations

import pytest

from services.drawing_transform import (
    MAX_SCALE_M_PT, MIN_SCALE_M_PT, PT_TO_MM, STANDARD_SCALE_DENOMINATORS,
    is_scale_plausible, scale_denominator,
)


@pytest.mark.unit
def test_pt_to_mm_is_the_typographic_point():
    """1pt = 25.4/72 mm。这个常量错了,所有比例判定都会跟着错。"""
    assert PT_TO_MM == pytest.approx(25.4 / 72)


@pytest.mark.unit
@pytest.mark.parametrize("denominator", [1, 50, 100, 150, 200, 500, 1000, 2000])
def test_standard_denominators_are_plausible(denominator):
    """§6.0.4 表 6.0.4 的常用比例必须全部通过。"""
    scale = denominator * PT_TO_MM / 1000.0
    assert is_scale_plausible(scale), f"1:{denominator} 是国标常用比例"


@pytest.mark.unit
@pytest.mark.parametrize("denominator,label", [
    (115960, "F3 的 S-1-20-002C —— 同层错位 83 米"),
    (3358662, "A-10-07.1C —— 1:335 万,构件会被扔到几百公里外"),
    (849328, "A-02-01A"),
    (654423, "S-0-11-001C"),
])
def test_measured_absurd_scales_are_rejected(denominator, label):
    """**实测的离谱比例必须被挡住**。"""
    scale = denominator * PT_TO_MM / 1000.0
    assert not is_scale_plausible(scale), label


@pytest.mark.unit
def test_zero_and_negative_are_rejected():
    assert not is_scale_plausible(0.0)
    assert not is_scale_plausible(-0.05)


@pytest.mark.unit
def test_too_small_is_rejected():
    """比 1:1 还小(放大图)在建筑图上不成立 —— 详图最大也就 1:1。"""
    assert not is_scale_plausible(MIN_SCALE_M_PT / 10)


@pytest.mark.unit
def test_bounds_bracket_the_standard_table():
    """门禁区间必须**包住**国标常用比例表,不能把合法比例挡在外面。"""
    for denominator in STANDARD_SCALE_DENOMINATORS:
        scale = denominator * PT_TO_MM / 1000.0
        assert MIN_SCALE_M_PT <= scale <= MAX_SCALE_M_PT, denominator


@pytest.mark.unit
@pytest.mark.parametrize("denominator", [1, 50, 100, 150, 200])
def test_scale_denominator_round_trips(denominator):
    scale = denominator * PT_TO_MM / 1000.0
    assert scale_denominator(scale) == pytest.approx(denominator, rel=1e-6)


# ── 端到端:算不出合理比例就不落变换 ──────────────────────────

class _FakeGeom:
    def __init__(self, page_h: float = 2384.0) -> None:
        self.lines: list = []
        self.texts: list = []
        self.page_w = 3370.0
        self.page_h = page_h


@pytest.mark.unit
def test_absurd_scale_yields_no_transform(monkeypatch):
    """**宁可没有变换,也不能有错变换。**

    没有变换时下游会降级估位置;错变换会把构件放到几百公里外,
    而且带着满分 confidence 骗过所有下游。
    """
    from services import drawing_transform as dt

    monkeypatch.setattr(
        "core.model3d.element_recognizer._detect_axes",
        lambda *a, **k: ([("1", 100.0)], [("A", 200.0)], None))
    monkeypatch.setattr(
        "core.model3d.element_recognizer._detect_scale",
        lambda *a, **k: (40.91, False))                      # 实测 S-1-20-002C 的值
    monkeypatch.setattr(
        "core.model3d.element_recognizer._origin_pt", lambda *a, **k: (0.0, 0.0))
    assert dt.transform_from_geometry(_FakeGeom()) is None


@pytest.mark.unit
def test_plausible_scale_still_yields_a_transform(monkeypatch):
    from services import drawing_transform as dt

    monkeypatch.setattr(
        "core.model3d.element_recognizer._detect_axes",
        lambda *a, **k: ([("1", 100.0)], [("A", 200.0)], None))
    monkeypatch.setattr(
        "core.model3d.element_recognizer._detect_scale",
        lambda *a, **k: (0.05292, False))                    # 约 1:150
    monkeypatch.setattr(
        "core.model3d.element_recognizer._origin_pt", lambda *a, **k: (10.0, 20.0))
    got = dt.transform_from_geometry(_FakeGeom())
    assert got is not None
    # 落库前会**吸附到 §6.0.4 的离散值**,所以比的是规整后的 1:150
    assert scale_denominator(got.scale_m_pt) == pytest.approx(150, rel=1e-6)


@pytest.mark.unit
def test_confidence_reflects_scale_standardness_not_just_labels(monkeypatch):
    """confidence 必须体现**比例尺是否标准**。

    旧实现是 `带标签轴线数 / 轴线总数` —— 那衡量的是轴号识别质量,
    与比例尺对错无关,于是 35 张离谱变换全都拿到 1.00。
    """
    from services import drawing_transform as dt

    monkeypatch.setattr(
        "core.model3d.element_recognizer._detect_axes",
        lambda *a, **k: ([("1", 100.0)], [("A", 200.0)], None))
    monkeypatch.setattr(
        "core.model3d.element_recognizer._origin_pt", lambda *a, **k: (0.0, 0.0))

    monkeypatch.setattr("core.model3d.element_recognizer._detect_scale",
                        lambda *a, **k: (150 * PT_TO_MM / 1000.0, False))
    standard = dt.transform_from_geometry(_FakeGeom())

    # **必须选一个不会被吸附的分母**:137 距 150 只有 8.7%，会被吸附成标准值,
    # 那就测不出非标准的 confidence 了。120 距 100 与距 150 都是 20%，
    # 超出 10% 吸附容差,会原样保留。
    monkeypatch.setattr("core.model3d.element_recognizer._detect_scale",
                        lambda *a, **k: (120 * PT_TO_MM / 1000.0, False))
    odd = dt.transform_from_geometry(_FakeGeom())

    assert standard is not None and odd is not None
    assert standard.confidence > odd.confidence


# ── 吸附到国标离散比例 ──────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.parametrize("measured,expected", [
    (45.6, 50), (46.4, 50), (9.1, 10), (9.4, 10),
    (905.9, 1000), (1075.4, 1000), (187.3, 200), (932.1, 1000),
])
def test_near_standard_scales_snap_to_the_standard_value(measured, expected):
    """**§6.0.4 表 6.0.4 的比例是离散规定值** —— 实测 45.6 只能意味着真值 50。

    实测全项目 1429 条变换里:

    | 偏差 | 张数 | 平均偏差 |
    |---|---:|---:|
    | ≤2%（已是标准） | 1264 | 0.0% |
    | **≤10%（可吸附）** | **113** | **4.9%** |
    | ≤30% | 37 | 14.4% |
    | >30% | 15 | 89.5% |

    4.9% 的比例误差,在 100 米建筑上就是 **4.9 米位置误差**。
    """
    from services.drawing_transform import snap_scale_to_standard

    scale = measured * PT_TO_MM / 1000.0
    snapped = snap_scale_to_standard(scale)
    assert scale_denominator(snapped) == pytest.approx(expected, rel=1e-6)


@pytest.mark.unit
@pytest.mark.parametrize("measured", [120, 700, 1300])
def test_far_from_standard_is_left_alone(measured):
    """偏差过大不吸附 —— 硬凑一个标准值只会把错误固化。"""
    from services.drawing_transform import snap_scale_to_standard

    scale = measured * PT_TO_MM / 1000.0
    assert snap_scale_to_standard(scale) == pytest.approx(scale)


@pytest.mark.unit
def test_snapping_never_crosses_to_a_neighbouring_standard():
    """吸附容差不得超过**相邻标准值最小间距的一半**。

    实测最密处:`1:5→1:6`、`1:25→1:30`、`1:50→1:60`、`1:250→1:300`、
    `1:500→1:600` 都是 **20%** ⇒ 安全上限恰为 **10%**。

    等于一半是安全的:吸附总是取**最近**的标准值,实测值落在两个标准值
    正中间时到两边距离相等,吸到哪个都合理。超过一半才会出现
    「明明更靠近 A 却吸到了 B」。
    """
    from services.drawing_transform import (
        SNAP_TOLERANCE, STANDARD_SCALE_DENOMINATORS,
    )

    ordered = sorted(STANDARD_SCALE_DENOMINATORS)
    gaps = [(b - a) / a for a, b in zip(ordered, ordered[1:])]
    assert SNAP_TOLERANCE <= min(gaps) / 2, "容差过大会跨越到相邻标准值"


@pytest.mark.unit
def test_exact_standard_is_unchanged():
    from services.drawing_transform import snap_scale_to_standard

    scale = 150 * PT_TO_MM / 1000.0
    assert snap_scale_to_standard(scale) == pytest.approx(scale)


@pytest.mark.unit
def test_transform_from_geometry_snaps(monkeypatch):
    """端到端:算出 45.6 的分母,落库时应当是 50。"""
    from services import drawing_transform as dt

    monkeypatch.setattr(
        "core.model3d.element_recognizer._detect_axes",
        lambda *a, **k: ([("1", 100.0)], [("A", 200.0)], None))
    monkeypatch.setattr(
        "core.model3d.element_recognizer._detect_scale",
        lambda *a, **k: (45.6 * PT_TO_MM / 1000.0, False))
    monkeypatch.setattr(
        "core.model3d.element_recognizer._origin_pt", lambda *a, **k: (0.0, 0.0))
    got = dt.transform_from_geometry(_FakeGeom())
    assert got is not None
    assert dt.scale_denominator(got.scale_m_pt) == pytest.approx(50, rel=1e-6)
    assert got.confidence == pytest.approx(1.0), "吸附后应视为标准比例"
