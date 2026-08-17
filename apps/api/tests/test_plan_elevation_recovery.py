"""方向1 单测:平面图标注 → 楼层真实标高(众数投票 + 单调性约束)。纯函数。"""
from services.plan_elevation_recovery import (
    enforce_monotonic,
    recover_plan_elevations,
    vote_floor_elevation,
)


# ── 众数投票 ───────────────────────────────────────────────────

def test_vote_picks_majority_across_drawings():
    """多张图共同标注的标高胜出(楼面标高),局部标高(窗顶)票少被淘汰。"""
    by_drawing = {
        "d1": [-4.2, 2.1],       # -4.2 楼面, 2.1 局部
        "d2": [-4.2, 9.9],
        "d3": [-4.2],
    }
    elev, support = vote_floor_elevation(by_drawing)
    assert elev == -4.2
    assert support == 3


def test_single_drawing_repeats_count_once():
    """同图重复标注只算一票——防单图刷票。"""
    by_drawing = {"d1": [7.5, 7.5, 7.5, 7.5]}
    elev, support = vote_floor_elevation(by_drawing, min_support=2)
    assert elev is None and support == 0        # 仅 1 张图支持 → 不可信


def test_below_min_support_rejected():
    assert vote_floor_elevation({"d1": [1.0], "d2": [2.0]}, min_support=2) == (None, 0)


def test_out_of_range_values_filtered():
    """超合理范围(如尺寸误识 5000)被过滤。"""
    by_drawing = {"d1": [5000.0, 3.6], "d2": [5000.0, 3.6]}
    elev, _ = vote_floor_elevation(by_drawing)
    assert elev == 3.6


def test_tie_prefers_lower_value():
    """平票取更小值:楼面标高通常低于窗顶/女儿墙等局部标高。"""
    elev, _ = vote_floor_elevation({"d1": [0.0, 3.0], "d2": [0.0, 3.0]})
    assert elev == 0.0


def test_rounding_absorbs_ocr_jitter():
    """10mm 归并抵消 OCR 抖动:-4.198/-4.202 视为同值。"""
    elev, support = vote_floor_elevation({"d1": [-4.198], "d2": [-4.202]})
    assert elev == -4.2 and support == 2


# ── 单调性约束 ─────────────────────────────────────────────────

def test_monotonic_keeps_increasing_sequence():
    levels = [("B1", -1, -4.2, 3), ("F1", 1, 0.0, 5), ("F2", 2, 4.5, 4)]
    kept = enforce_monotonic(levels)
    assert set(kept) == {"B1", "F1", "F2"}


def test_monotonic_drops_violating_low_support():
    """F2 标高(1.0)低于 F1(0.0)?不——1.0>0.0 合法;这里 F2=−1.0 违反且支持度低 → 剔除。"""
    levels = [("F1", 1, 0.0, 5), ("F2", 2, -1.0, 1)]
    kept = enforce_monotonic(levels)
    assert "F1" in kept and "F2" not in kept


def test_monotonic_prefers_higher_support_on_conflict():
    """冲突时保留支持度更高者:F1(支持1)被 F2(支持9)挤掉。"""
    levels = [("F1", 1, 10.0, 1), ("F2", 2, 4.5, 9)]
    kept = enforce_monotonic(levels)
    assert "F2" in kept and "F1" not in kept


def test_monotonic_ignores_none():
    assert enforce_monotonic([("F1", 1, None, 0)]) == {}


# ── 端到端 ─────────────────────────────────────────────────────

def test_recover_plan_elevations_end_to_end():
    per_floor = {
        "B1": {"order": -1, "drawings": {"a": [-4.2], "b": [-4.2, 1.5]}},
        "F1": {"order": 1, "drawings": {"c": [0.0], "d": [0.0]}},
        "F2": {"order": 2, "drawings": {"e": [4.5], "f": [4.5]}},
        "F3": {"order": 3, "drawings": {"g": [99.0]}},      # 单图 → 支持不足,不出现
    }
    out = recover_plan_elevations(per_floor)
    assert out["B1"]["elevation_m"] == -4.2
    assert out["F1"]["elevation_m"] == 0.0
    assert out["F2"]["elevation_m"] == 4.5
    assert "F3" not in out
    assert out["F1"]["z_source"] == "plan_annotation"
    assert out["B1"]["support"] == 2


def test_recover_empty():
    assert recover_plan_elevations({}) == {}


def test_discriminative_weight_suppresses_ubiquitous_baseline():
    """核心:±0.000 基准标高在所有层都高频 → 被 IDF 压制;各层特征标高胜出。
    (实测:不加权时每层众数都是 0.0,单调性把全部剔除,恢复率为 0)"""
    from services.plan_elevation_recovery import vote_with_discriminative_weight
    per_floor = {
        "B1": {"order": -1, "drawings": {"a": [0.0, -4.2], "b": [0.0, -4.2], "c": [0.0]}},
        "F1": {"order": 1, "drawings": {"d": [0.0], "e": [0.0], "f": [0.0]}},
        "F2": {"order": 2, "drawings": {"g": [0.0, 4.5], "h": [0.0, 4.5]}},
    }
    out = vote_with_discriminative_weight(per_floor)
    assert out["B1"][0] == -4.2      # 只在 B1 出现 → 高区分度胜出
    assert out["F2"][0] == 4.5       # 只在 F2 出现
    assert out["F1"][0] == 0.0       # F1 只有基准值可选


def test_recover_with_weighting_end_to_end():
    """加权 + 单调性:恢复出递增的真实标高序列。"""
    per_floor = {
        "B1": {"order": -1, "drawings": {"a": [0.0, -4.2], "b": [0.0, -4.2]}},
        "F1": {"order": 1, "drawings": {"c": [0.0, 0.0], "d": [0.0]}},
        "F2": {"order": 2, "drawings": {"e": [0.0, 4.5], "f": [0.0, 4.5]}},
    }
    out = recover_plan_elevations(per_floor)
    assert out["B1"]["elevation_m"] == -4.2
    assert out["F2"]["elevation_m"] == 4.5


def test_grade_candidates_flags_review_and_quality():
    """恢复值一律标 needs_review(IDF 会误压制正确的基准值,精度不足以自动覆盖)。"""
    from services.plan_elevation_recovery import grade_candidates
    cands = {
        "F1": {"elevation_m": 0.92, "support": 48, "z_source": "plan_annotation"},
        "F2": {"elevation_m": 4.96, "support": 37, "z_source": "plan_annotation"},
    }
    graded = grade_candidates(cands, baseline={"F1": 0.0, "F2": 4.5},
                              orders={"F1": 1, "F2": 2})
    assert all(c["needs_review"] is True for c in graded.values())
    assert graded["F1"]["deviation_m"] == 0.92
    assert graded["F1"]["story_height_ok"] is True      # 4.96-0.92=4.04m 合理
    assert 0 < graded["F1"]["confidence"] <= 1.0


def test_grade_penalises_unreasonable_story_height():
    from services.plan_elevation_recovery import grade_candidates
    cands = {"A": {"elevation_m": 0.0, "support": 20},
             "B": {"elevation_m": 30.0, "support": 20}}   # 层高 30m 不合理
    g = grade_candidates(cands, orders={"A": 1, "B": 2})
    assert g["A"]["story_height_ok"] is False
    assert g["A"]["confidence"] < g["B"]["confidence"]


def test_grade_penalises_large_deviation():
    from services.plan_elevation_recovery import grade_candidates
    g = grade_candidates({"A": {"elevation_m": 20.0, "support": 20}},
                         baseline={"A": 15.9}, orders={"A": 1})
    assert abs(g["A"]["deviation_m"] - 4.1) < 1e-6
    assert g["A"]["confidence"] <= 0.5     # 偏差 >2m 降级
