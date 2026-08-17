"""Phase H7 验收指标单测 —— compute_metrics 纯函数。"""
from services.component_metrics import compute_metrics


def _summary():
    return {
        "total": 100, "with_z": 90, "with_grid": 20,
        "confirmed": 30, "conflict": 25, "auto": 45,
        "by_type": {"column": 60, "wall": 40},
    }


def test_rates_are_true_values():
    m = compute_metrics(_summary(), {})
    assert m["vertical_reality_rate"] == 0.9          # 90/100
    assert m["grid_location_rate"] == 0.2             # 20/100(位置代理)
    assert m["review"]["confirmed_rate"] == 0.3


def test_review_action_totals():
    m = compute_metrics(_summary(), {"confirm": 10, "reject": 3, "reclass": 2})
    assert m["review_actions"]["total"] == 15


def test_count_accuracy_with_bom():
    m = compute_metrics(_summary(), {}, bom={"column": 66, "wall": 40})
    # column: |60-66|/66 = 0.0909 → 准确率 0.9091
    assert m["count_accuracy"]["column"] == round(1 - 6 / 66, 4)
    assert m["count_accuracy"]["wall"] == 1.0


def test_position_error_is_honestly_noted_not_faked():
    m = compute_metrics(_summary(), {})
    assert "position_error_note" in m
    assert "不可测" in m["position_error_note"]


def test_zero_total_no_divide_by_zero():
    m = compute_metrics({"total": 0}, {})
    assert m["vertical_reality_rate"] == 0.0
    assert m["grid_location_rate"] == 0.0
