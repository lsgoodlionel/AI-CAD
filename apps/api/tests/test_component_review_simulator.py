"""人审飞轮模拟器单测 —— 决策规则基于可验证证据,非随机。"""
from services.component_review_simulator import decide, simulate_batch


def _sq(size, cx=0.0, cy=0.0):
    h = size / 2
    return [[cx - h, cy - h], [cx + h, cy - h], [cx + h, cy + h], [cx - h, cy + h]]


def test_confirm_when_grid_located_and_multi_observed():
    """轴网定位 + 多观测互证 → 确认。"""
    d = decide({"grid_ref": "C-3", "obs_count": 3, "outline_m": _sq(0.6)})
    assert d["action"] == "confirm"
    assert "C-3" in d["reason"]


def test_reject_too_small_section():
    d = decide({"grid_ref": "C-3", "obs_count": 5, "outline_m": _sq(0.05)})
    assert d["action"] == "reject"
    assert "过小" in d["reason"]


def test_reject_too_large_section():
    d = decide({"grid_ref": "C-3", "obs_count": 5, "outline_m": _sq(8.0)})
    assert d["action"] == "reject"
    assert "过大" in d["reason"]


def test_defer_when_evidence_insufficient():
    """单观测 + 无完整轴网 → 不自动裁决,留给真人(模拟器的诚实边界)。"""
    d = decide({"grid_ref": None, "obs_count": 1, "outline_m": _sq(0.6)})
    assert d["action"] is None


def test_defer_partial_grid_single_observation():
    d = decide({"grid_ref": "C-?", "obs_count": 1, "outline_m": _sq(0.6)})
    assert d["action"] is None


def test_reject_takes_priority_over_confirm():
    """尺寸异常优先否定,即使证据"充分"——几何不可能就是不可能。"""
    d = decide({"grid_ref": "C-3", "obs_count": 9, "outline_m": _sq(0.02)})
    assert d["action"] == "reject"


def test_simulate_batch_stats():
    insts = [
        {"id": "1", "grid_ref": "C-3", "obs_count": 3, "outline_m": _sq(0.6)},   # confirm
        {"id": "2", "grid_ref": "C-3", "obs_count": 3, "outline_m": _sq(0.03)},  # reject
        {"id": "3", "grid_ref": None, "obs_count": 1, "outline_m": _sq(0.6)},    # defer
    ]
    out = simulate_batch(insts)
    assert out["stats"] == {"confirm": 1, "reject": 1, "deferred": 1}
    assert len(out["decisions"]) == 2
    assert out["decisions"][0][1] == "confirm"


def test_no_outline_still_confirmable_by_evidence():
    """无轮廓(尺寸未知)不阻断:仍可凭轴网+多观测确认。"""
    d = decide({"grid_ref": "A-1", "obs_count": 4, "outline_m": None})
    assert d["action"] == "confirm"
