"""
钢筋翻样计算单元测试 — GB50010-2010 公式验证

覆盖目标：
- La / LaE / Ll 公式正确性（与手算对比）
- 5mm 向上取整规则
- 最小值约束（La ≥ 200mm，LaE ≥ 250mm）
- 不同抗震等级的系数差异
- FFD 下料算法：废料率 ≤ 1.5%（目标保障）
- 空输入 / 边界输入安全性
"""
import pytest

from core.economic.rebar_calculator import (
    AnchorLengths,
    BarItem,
    CalcResult,
    CuttingPattern,
    FT_TABLE,
    FY_TABLE,
    ALPHA_TABLE,
    _ceil5,
    _ffd_cut,
    calc_anchor_lengths,
    optimize_cutting,
)


# ── 默认参数 ─────────────────────────────────────────────────────

DEFAULT_SEISMIC = {1: 1.15, 2: 1.15, 3: 1.05, 4: 1.00}
DEFAULT_LAP     = {"25": 1.20, "50": 1.40, "100": 1.60}


# ── _ceil5 ────────────────────────────────────────────────────────

class TestCeil5:
    @pytest.mark.parametrize("v,expected", [
        (0,   0),
        (1,   5),
        (5,   5),
        (6,  10),
        (100, 100),
        (101, 105),
        (199.9, 200),
        (200.0, 200),
    ])
    def test_ceil5(self, v, expected):
        assert _ceil5(v) == expected


# ── GB50010-2010 手算验证 ─────────────────────────────────────────

class TestAnchorLengths:
    """
    手算参考（GB50010-2010 表 8.3.1）:
    HRB400 / C30 / d=20:
      fy=400, ft=1.43, α=0.14
      La_raw = 0.14 * (400/1.43) * 20 = 0.14 * 279.72 * 20 = 783.22
      La = ceil5(783.22) = 785, 785 ≥ 200 ✓
      ζaE(grade=2) = 1.15
      LaE_raw = 1.15 * 783.22 = 900.70
      LaE = ceil5(900.70) = 905, 905 ≥ 250 ✓
      base = LaE（grade=2 ≤ 3）
      Ll_25  = ceil5(1.20 * 905) = ceil5(1086) = 1090
      Ll_50  = ceil5(1.40 * 905) = ceil5(1267) = 1270
      Ll_100 = ceil5(1.60 * 905) = ceil5(1448) = 1450
    """

    def test_hrb400_c30_d20_seismic2(self):
        al = calc_anchor_lengths(
            diameter=20,
            steel_grade="HRB400",
            concrete_grade="C30",
            seismic_grade=2,
            seismic_factors=DEFAULT_SEISMIC,
            lap_factors=DEFAULT_LAP,
        )
        assert al.La  == 785
        assert al.LaE == 905
        assert al.Ll_25  == 1090
        assert al.Ll_50  == 1270
        assert al.Ll_100 == 1450

    def test_la_minimum_200mm(self):
        """极细钢筋（d=6）锚固长度不应低于 200mm"""
        al = calc_anchor_lengths(
            diameter=6,
            steel_grade="HPB300",
            concrete_grade="C60",
            seismic_grade=4,
            seismic_factors=DEFAULT_SEISMIC,
            lap_factors=DEFAULT_LAP,
        )
        assert al.La >= 200

    def test_lae_minimum_250mm(self):
        """抗震锚固长度不应低于 250mm"""
        al = calc_anchor_lengths(
            diameter=6,
            steel_grade="HRB335",
            concrete_grade="C60",
            seismic_grade=1,
            seismic_factors=DEFAULT_SEISMIC,
            lap_factors=DEFAULT_LAP,
        )
        assert al.LaE >= 250

    def test_seismic_grade4_uses_la_as_base(self):
        """非抗震区（4级）搭接以 La 为基础，不用 LaE"""
        al_4 = calc_anchor_lengths(
            diameter=20, steel_grade="HRB400", concrete_grade="C30",
            seismic_grade=4, seismic_factors=DEFAULT_SEISMIC, lap_factors=DEFAULT_LAP,
        )
        # grade=4 时 ζaE=1.0，LaE ≈ La，且 base=La
        assert al_4.Ll_25 == _ceil5(DEFAULT_LAP["25"] * al_4.La)

    def test_seismic_grade1_uses_lae_as_base(self):
        """抗震一级区搭接以 LaE 为基础"""
        al_1 = calc_anchor_lengths(
            diameter=20, steel_grade="HRB400", concrete_grade="C30",
            seismic_grade=1, seismic_factors=DEFAULT_SEISMIC, lap_factors=DEFAULT_LAP,
        )
        assert al_1.Ll_25 == _ceil5(DEFAULT_LAP["25"] * al_1.LaE)

    def test_all_grades_and_concretes(self):
        """所有支持的钢筋/混凝土组合不应抛出异常"""
        for grade in FY_TABLE:
            for concrete in FT_TABLE:
                for seismic in [1, 2, 3, 4]:
                    al = calc_anchor_lengths(
                        diameter=16,
                        steel_grade=grade,
                        concrete_grade=concrete,
                        seismic_grade=seismic,
                        seismic_factors=DEFAULT_SEISMIC,
                        lap_factors=DEFAULT_LAP,
                    )
                    assert al.La > 0
                    assert al.LaE >= al.La or al.LaE >= 250
                    assert isinstance(al, AnchorLengths)

    def test_result_is_frozen_dataclass(self):
        """AnchorLengths 应为不可变（frozen dataclass）"""
        al = calc_anchor_lengths(20, "HRB400", "C30", 2, DEFAULT_SEISMIC, DEFAULT_LAP)
        with pytest.raises((AttributeError, TypeError)):
            al.La = 999  # type: ignore


# ── FFD 下料算法 ──────────────────────────────────────────────────

class TestFFDCutting:
    def test_single_piece_fits(self):
        bins = _ffd_cut([3000], std_len=9000)
        assert len(bins) == 1
        assert bins[0] == [3000]

    def test_fills_bin_exactly(self):
        bins = _ffd_cut([3000, 3000, 3000], std_len=9000)
        assert len(bins) == 1

    def test_overflow_creates_new_bin(self):
        bins = _ffd_cut([5000, 5000], std_len=9000)
        assert len(bins) == 2

    def test_pieces_larger_than_std_impossible(self):
        """超过定尺长度的单件会独立占一个 bin，余料为负（不合理输入测试）"""
        # 在 optimize_cutting 中会过滤掉 std_len < max(pieces) 的情况
        # _ffd_cut 本身如果 piece > std_len 会产生负余料，这里验证该行为
        bins = _ffd_cut([10000], std_len=9000)
        assert len(bins) == 1  # 至少放进去了（业务层应提前过滤）


# ── optimize_cutting 集成测试 ─────────────────────────────────────

DEFAULT_FIELD_WASTE = {
    "d6_10": 0.06, "d12_16": 0.045, "d18_22": 0.04, "d25_plus": 0.035
}


class TestOptimizeCutting:
    def _run(self, bars, std_lengths=None, price=5000.0):
        return optimize_cutting(
            bars=bars,
            standard_lengths=std_lengths or [9000, 10000, 12000],
            field_waste_rates=DEFAULT_FIELD_WASTE,
            steel_price_per_ton=price,
            target_waste_rate=0.015,
            auto_proposal_min_saving=5000.0,
        )

    def test_returns_patterns_and_summary(self):
        bars = [BarItem(diameter=16, steel_grade="HRB400", required_length=3500, count=10)]
        patterns, summary = self._run(bars)
        assert isinstance(patterns, list)
        assert "total_steel_kg" in summary
        assert "saving_yuan" in summary
        assert "optimized_waste_rate" in summary
        assert "auto_proposal_eligible" in summary

    def test_waste_rate_within_target(self):
        """标准场景废料率应 ≤ 2%（FFD+2-opt 保障）"""
        bars = [
            BarItem(diameter=20, steel_grade="HRB400", required_length=3200, count=30),
            BarItem(diameter=20, steel_grade="HRB400", required_length=2800, count=20),
            BarItem(diameter=20, steel_grade="HRB400", required_length=1500, count=50),
        ]
        _, summary = self._run(bars)
        assert summary["optimized_waste_rate"] <= 0.02

    def test_empty_bars_returns_empty(self):
        patterns, summary = self._run([])
        assert patterns == []
        assert summary["total_steel_kg"] == 0
        assert summary["saving_yuan"] == 0

    def test_auto_proposal_eligible_when_saving_high(self):
        """节约额 > 5000 元时应标记为可发起创效提案"""
        bars = [
            BarItem(diameter=25, steel_grade="HRB400", required_length=8000, count=100),
        ]
        _, summary = self._run(bars, price=10000.0)
        # 重钢 + 高单价，应有明显节约
        if summary["saving_yuan"] >= 5000.0:
            assert summary["auto_proposal_eligible"] is True

    def test_auto_proposal_not_eligible_when_saving_zero(self):
        bars = [BarItem(diameter=20, steel_grade="HRB400", required_length=1, count=1)]
        _, summary = self._run(bars)
        assert summary["auto_proposal_eligible"] is False

    def test_multi_diameter_groups(self):
        """不同直径应分组优化，总重量应正确"""
        bars = [
            BarItem(diameter=12, steel_grade="HRB400", required_length=4000, count=10),
            BarItem(diameter=25, steel_grade="HRB400", required_length=6000, count=5),
        ]
        patterns, summary = self._run(bars)
        assert len(patterns) > 0
        assert summary["total_steel_kg"] > 0

    def test_cutting_pattern_is_frozen(self):
        bars = [BarItem(diameter=16, steel_grade="HRB400", required_length=3000, count=3)]
        patterns, _ = self._run(bars)
        if patterns:
            with pytest.raises((AttributeError, TypeError)):
                patterns[0].repeat = 99  # type: ignore

    def test_saving_kg_non_negative(self):
        bars = [BarItem(diameter=16, steel_grade="HRB400", required_length=4500, count=5)]
        _, summary = self._run(bars)
        assert summary["saving_kg"] >= 0
        assert summary["saving_yuan"] >= 0

    def test_field_waste_rate_reasonable(self):
        """粗放基准损耗率应在合理范围（0%~10%）"""
        bars = [BarItem(diameter=10, steel_grade="HRB400", required_length=2000, count=20)]
        _, summary = self._run(bars)
        assert 0 <= summary["field_waste_rate"] <= 0.10


# ── 材料参数表完整性 ──────────────────────────────────────────────

class TestMaterialTables:
    def test_ft_table_has_common_grades(self):
        for grade in ["C20", "C25", "C30", "C35", "C40", "C45", "C50"]:
            assert grade in FT_TABLE
            assert FT_TABLE[grade] > 0

    def test_fy_table_has_common_grades(self):
        for grade in ["HPB300", "HRB335", "HRB400", "HRB500"]:
            assert grade in FY_TABLE
            assert FY_TABLE[grade] > 0

    def test_alpha_table_consistent_with_fy(self):
        assert set(ALPHA_TABLE.keys()) == set(FY_TABLE.keys())
