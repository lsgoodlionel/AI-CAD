"""Phase H 真实度:构件截面模数化对齐单测。纯函数。"""
from services.component_dimension import (
    module_compliance,
    snap_dimension,
    snap_instances,
    snap_outline,
)


# ── snap_dimension ─────────────────────────────────────────────

def test_snap_to_standard_section():
    """实测抖动值应吸附到标准截面:0.51→0.50, 0.58→0.60, 0.99→1.00。"""
    assert snap_dimension(0.51) == (0.5, True)
    assert snap_dimension(0.58) == (0.6, True)
    assert snap_dimension(0.99) == (1.0, True)
    assert snap_dimension(0.81) == (0.8, True)


def test_exact_standard_unchanged_but_marked():
    v, ok = snap_dimension(0.60)
    assert v == 0.6 and ok is True


def test_large_deviation_not_snapped():
    """超容差不吸附——保留真实异形/误检原貌,不掩盖真相。"""
    v, ok = snap_dimension(1.14, tolerance_m=0.01)   # 距 1.15 有 10mm,距 1.10 有 40mm
    assert ok is False and v == 1.14


def test_snap_falls_back_to_module_step():
    """标准截面未覆盖(如 2.45)→ 退到 50mm 模数。"""
    v, ok = snap_dimension(2.46)
    assert ok is True and abs(v - 2.45) < 1e-6


def test_invalid_dimension():
    assert snap_dimension(0) == (0, False)
    assert snap_dimension(-1) == (-1, False)


# ── snap_outline ───────────────────────────────────────────────

def test_snap_outline_keeps_center_and_snaps_size():
    # 0.51×0.51 方柱,中心 (10,10) → 应变 0.50×0.50,中心不变
    outline = [[9.745, 9.745], [10.255, 9.745], [10.255, 10.255], [9.745, 10.255]]
    snapped, ok = snap_outline(outline)
    assert ok is True
    xs = [p[0] for p in snapped]
    ys = [p[1] for p in snapped]
    assert abs((max(xs) - min(xs)) - 0.5) < 1e-3      # 宽吸附到 0.50
    assert abs((max(xs) + min(xs)) / 2 - 10.0) < 1e-3  # 中心不变
    assert abs((max(ys) + min(ys)) / 2 - 10.0) < 1e-3


def test_snap_outline_too_few_points():
    assert snap_outline([[0, 0], [1, 1]]) == ([[0, 0], [1, 1]], False)
    assert snap_outline(None) == (None, False)


def test_snap_instances_counts_and_marks():
    insts = [
        {"outline_m": [[0, 0], [0.51, 0], [0.51, 0.51], [0, 0.51]]},   # 吸附
        {"outline_m": None},                                            # 跳过
    ]
    stat = snap_instances(insts)
    assert stat == {"snapped": 1, "total": 2}
    assert insts[0]["dimension_snapped"] is True
    assert "dimension_snapped" not in insts[1]


# ── module_compliance ──────────────────────────────────────────

def test_module_compliance_metrics():
    m = module_compliance([0.5, 0.6, 0.8])       # 全部合模数
    assert m["over_10mm_pct"] == 0.0
    assert m["distinct"] == 3
    m2 = module_compliance([0.51, 0.58, 0.73])   # 全部偏离 >10mm
    assert m2["over_10mm_pct"] == 100.0


def test_module_compliance_empty():
    m = module_compliance([])
    assert m["n"] == 0 and m["distinct"] == 0


def test_snap_scene_columns_only_touches_columns():
    """scene 级:只模数化柱截面,不动板轮廓/墙走向(避免破坏真实形状)。"""
    from services.component_dimension import snap_scene_columns
    slab_outline = [[0, 0], [37.3, 0], [37.3, 21.7], [0, 21.7]]
    floors = [{"elements": {
        "columns": [{"outline": [[0, 0], [0.51, 0], [0.51, 0.51], [0, 0.51]]}],
        "slabs": [{"outline": [list(p) for p in slab_outline]}],
        "walls": [{"path": [[0, 0], [12.34, 0]]}],
    }}]
    stat = snap_scene_columns(floors)
    assert stat == {"snapped": 1, "total": 1}
    els = floors[0]["elements"]
    assert els["columns"][0]["dimension_snapped"] is True
    assert els["slabs"][0]["outline"] == slab_outline      # 板轮廓不动
    assert els["walls"][0]["path"] == [[0, 0], [12.34, 0]]  # 墙走向不动
