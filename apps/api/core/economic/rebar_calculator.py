"""
钢筋翻样计算器 — GB50010-2010

锚固长度: La  = α × (fy / ft) × d，向上取整至 5mm
抗震锚固: LaE = ζaE × La
搭接长度: Ll  = ζl  × La（抗震区用 LaE 替换 La）
下料优化: FFD（首次适应递减）+ 2-opt 局部搜索，目标废料率 ≤ 1.5%
"""
import math
from dataclasses import dataclass, field
from typing import NamedTuple


# ── GB50010-2010 材料参数表 ─────────────────────────────────────

FT_TABLE: dict[str, float] = {
    "C20": 1.10, "C25": 1.27, "C30": 1.43, "C35": 1.57,
    "C40": 1.71, "C45": 1.80, "C50": 1.89, "C55": 1.96, "C60": 2.04,
}

FY_TABLE: dict[str, float] = {
    "HPB300": 270.0,
    "HRB335": 335.0, "HRB400": 400.0, "HRB500": 500.0,
    "HRBF400": 400.0, "HRBF500": 500.0,
}

ALPHA_TABLE: dict[str, float] = {   # 粘结形状系数（带肋/光面）
    "HPB300": 0.16,
    "HRB335": 0.14, "HRB400": 0.14, "HRB500": 0.14,
    "HRBF400": 0.14, "HRBF500": 0.14,
}

WEIGHT_PER_METER_KG = staticmethod(lambda d: d * d / 162.0)   # d: mm


# ── 数据结构 ────────────────────────────────────────────────────

class BarItem(NamedTuple):
    diameter: int          # mm
    steel_grade: str       # HRB400 等
    required_length: int   # mm（单根所需长度）
    count: int             # 根数


@dataclass(frozen=True)
class AnchorLengths:
    diameter: int
    steel_grade: str
    concrete_grade: str
    fy: float
    ft: float
    La: int      # 基本锚固长度（mm，整 5 倍）
    LaE: int     # 抗震锚固（mm，整 5 倍）
    Ll_25: int   # 搭接率 ≤25% 时搭接长度
    Ll_50: int   # 搭接率 50%
    Ll_100: int  # 搭接率 100%


@dataclass(frozen=True)
class CuttingPattern:
    cuts: tuple[int, ...]   # 从一根定尺料上切出的各段长度（mm）
    standard_length: int    # 定尺长度（mm）
    waste: int              # 余料（mm）
    repeat: int             # 此方案重复次数


@dataclass
class CalcResult:
    anchor_lengths: list[AnchorLengths]
    cutting_patterns: list[CuttingPattern]
    total_steel_kg: float
    field_waste_rate: float      # 粗放基准损耗率
    optimized_waste_rate: float  # 翻样后损耗率
    saving_kg: float
    saving_yuan: float
    auto_proposal_eligible: bool  # 是否达到自动推送创效阈值


# ── 锚固/搭接计算 ───────────────────────────────────────────────

def _ceil5(v: float) -> int:
    """向上取整到 5mm"""
    return int(math.ceil(v / 5.0) * 5)


def calc_anchor_lengths(
    diameter: int,
    steel_grade: str,
    concrete_grade: str,
    seismic_grade: int,
    seismic_factors: dict[int, float],   # {1: 1.15, 2: 1.15, 3: 1.05, 4: 1.00}
    lap_factors: dict[str, float],       # {"25": 1.20, "50": 1.40, "100": 1.60}
) -> AnchorLengths:
    fy = FY_TABLE[steel_grade]
    ft = FT_TABLE[concrete_grade]
    alpha = ALPHA_TABLE[steel_grade]

    La_raw = alpha * (fy / ft) * diameter
    La = max(_ceil5(La_raw), 200)          # 不小于 200mm（条文下限）

    zae = seismic_factors.get(seismic_grade, 1.0)
    LaE = max(_ceil5(zae * La_raw), 250)   # 抗震不小于 250mm

    base = LaE if seismic_grade <= 3 else La
    return AnchorLengths(
        diameter=diameter,
        steel_grade=steel_grade,
        concrete_grade=concrete_grade,
        fy=fy,
        ft=ft,
        La=La,
        LaE=LaE,
        Ll_25=_ceil5(lap_factors["25"] * base),
        Ll_50=_ceil5(lap_factors["50"] * base),
        Ll_100=_ceil5(lap_factors["100"] * base),
    )


# ── FFD 下料优化 ────────────────────────────────────────────────

def _ffd_cut(pieces: list[int], std_len: int) -> list[list[int]]:
    """首次适应递减（FFD）：返回每根定尺料的切割方案列表"""
    sorted_pieces = sorted(pieces, reverse=True)
    bins: list[list[int]] = []
    remainders: list[int] = []

    for piece in sorted_pieces:
        placed = False
        for i, rem in enumerate(remainders):
            if rem >= piece:
                bins[i].append(piece)
                remainders[i] -= piece
                placed = True
                break
        if not placed:
            bins.append([piece])
            remainders.append(std_len - piece)

    return bins


def _local_search(bins: list[list[int]], remainders: list[int], std_len: int) -> None:
    """2-opt 对换：尝试将 piece 从高余料 bin 搬到低余料 bin 以减少 bin 数"""
    improved = True
    while improved:
        improved = False
        for i in range(len(bins)):
            for j in range(i + 1, len(bins)):
                for pi, p in enumerate(bins[i]):
                    if remainders[j] >= p:
                        bins[j].append(p)
                        bins[i].pop(pi)
                        remainders[j] -= p
                        remainders[i] += p
                        if not bins[i]:      # bin i 清空：移除
                            bins.pop(i)
                            remainders.pop(i)
                        improved = True
                        return              # 重新搜索


def _compress_patterns(bins: list[list[int]], std_len: int) -> list[CuttingPattern]:
    """合并相同切割方案，统计重复次数"""
    counter: dict[tuple[int, ...], int] = {}
    for b in bins:
        key = tuple(sorted(b, reverse=True))
        counter[key] = counter.get(key, 0) + 1
    return [
        CuttingPattern(
            cuts=cuts,
            standard_length=std_len,
            waste=std_len - sum(cuts),
            repeat=cnt,
        )
        for cuts, cnt in counter.items()
    ]


def optimize_cutting(
    bars: list[BarItem],
    standard_lengths: list[int],
    field_waste_rates: dict[str, float],   # {"d6_10": 0.06, "d12_16": 0.045, ...}
    steel_price_per_ton: float,
    target_waste_rate: float,
    auto_proposal_min_saving: float,
) -> tuple[list[CuttingPattern], dict]:
    """
    对所有 BarItem 执行下料优化。
    返回 (切割方案列表, 汇总统计字典)。
    """
    all_patterns: list[CuttingPattern] = []
    total_required_mm = 0
    total_std_bars_used_mm = 0
    total_field_waste_mm = 0

    # 按 (diameter, steel_grade) 分组
    groups: dict[tuple, list[int]] = {}
    for bar in bars:
        key = (bar.diameter, bar.steel_grade)
        groups.setdefault(key, []).extend([bar.required_length] * bar.count)

    for (diam, grade), pieces in groups.items():
        if not pieces:
            continue

        # 选最优定尺长度（废料率最低）
        best_patterns: list[CuttingPattern] = []
        best_waste_rate = 1.0
        for std_len in sorted(standard_lengths):
            if std_len < max(pieces):
                continue
            bins = _ffd_cut(pieces, std_len)
            remainders = [std_len - sum(b) for b in bins]
            _local_search(bins, remainders, std_len)
            patterns = _compress_patterns(bins, std_len)
            total_used = sum(p.standard_length * p.repeat for p in patterns)
            total_req = sum(pieces)
            waste_rate = 1.0 - total_req / total_used if total_used else 1.0
            if waste_rate < best_waste_rate:
                best_waste_rate = waste_rate
                best_patterns = patterns

        all_patterns.extend(best_patterns)

        # 统计汇总
        req_mm = sum(pieces)
        std_mm = sum(p.standard_length * p.repeat for p in best_patterns)
        total_required_mm += req_mm
        total_std_bars_used_mm += std_mm

        # 粗放基准损耗（按直径区间）
        if diam <= 10:
            field_rate = field_waste_rates.get("d6_10", 0.06)
        elif diam <= 16:
            field_rate = field_waste_rates.get("d12_16", 0.045)
        elif diam <= 22:
            field_rate = field_waste_rates.get("d18_22", 0.04)
        else:
            field_rate = field_waste_rates.get("d25_plus", 0.035)
        total_field_waste_mm += req_mm * field_rate / (1 - field_rate)

    if total_std_bars_used_mm == 0:
        empty: dict = {"total_steel_kg": 0, "field_waste_rate": 0,
                       "optimized_waste_rate": 0, "saving_kg": 0,
                       "saving_yuan": 0, "auto_proposal_eligible": False}
        return [], empty

    optimized_waste_mm = total_std_bars_used_mm - total_required_mm
    optimized_waste_rate = optimized_waste_mm / total_std_bars_used_mm

    # 重量估算：所有 bar 取平均直径（简化）
    avg_diam = sum(b.diameter * b.count for b in bars) / max(sum(b.count for b in bars), 1)
    kg_per_mm = WEIGHT_PER_METER_KG(avg_diam) / 1000.0

    total_steel_kg = total_std_bars_used_mm * kg_per_mm
    field_waste_kg = total_field_waste_mm * kg_per_mm
    opt_waste_kg = optimized_waste_mm * kg_per_mm
    saving_kg = max(field_waste_kg - opt_waste_kg, 0.0)
    saving_yuan = saving_kg / 1000.0 * steel_price_per_ton

    field_waste_total = total_required_mm + total_field_waste_mm
    field_waste_rate = total_field_waste_mm / field_waste_total if field_waste_total else 0.0

    summary = {
        "total_steel_kg": round(total_steel_kg, 2),
        "field_waste_rate": round(field_waste_rate, 4),
        "optimized_waste_rate": round(optimized_waste_rate, 4),
        "saving_kg": round(saving_kg, 2),
        "saving_yuan": round(saving_yuan, 2),
        "auto_proposal_eligible": saving_yuan >= auto_proposal_min_saving,
    }
    return all_patterns, summary
