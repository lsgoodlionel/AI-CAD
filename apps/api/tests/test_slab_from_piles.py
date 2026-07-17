"""
E3-4 桩包络补板单测:pile 增强后无板 → 用柱/桩包络补一块底板。
"""
from services.model_elements import ensure_slab_from_columns


def _col(cx, cy, r=0.3):
    return {"outline": [[cx-r, cy-r], [cx+r, cy-r], [cx+r, cy+r], [cx-r, cy+r]]}


def test_generates_slab_when_none_and_many_columns():
    cols = [_col(x*3.0, y*3.0) for x in range(4) for y in range(4)]  # 16 柱, ~9x9m
    slabs = ensure_slab_from_columns(cols, existing_slabs=[])
    assert len(slabs) == 1
    assert slabs[0]["src"] == "piles-envelope"
    # 包络覆盖柱范围
    xs = [p[0] for p in slabs[0]["outline"]]
    assert max(xs) - min(xs) > 8


def test_keeps_existing_slabs_untouched():
    cols = [_col(x*3.0, 0) for x in range(6)]
    existing = [{"outline": [[0, 0], [1, 0], [1, 1], [0, 1]], "thickness": 0.15}]
    slabs = ensure_slab_from_columns(cols, existing_slabs=existing)
    assert slabs == existing  # 已有板则不补


def test_no_slab_when_too_few_columns():
    slabs = ensure_slab_from_columns([_col(0, 0), _col(1, 0)], existing_slabs=[])
    assert slabs == []
