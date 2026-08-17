"""人工手描轴线记忆单测(免得同图/同版式重复标注)。"""
import pytest

from services.axis_line_memory import (
    is_duplicate, line_position, merge_candidates,
)


def _v(x, y1=0.0, y2=1.0):
    """竖向线(位置 = x)"""
    return {"direction": "x", "x1_norm": x, "y1_norm": y1, "x2_norm": x, "y2_norm": y2}


def _h(y, x1=0.0, x2=1.0):
    """横向线(位置 = y)"""
    return {"direction": "y", "x1_norm": x1, "y1_norm": y, "x2_norm": x2, "y2_norm": y}


# ── 位置与去重 ──────────────────────────────────────────────────

def test_line_position_uses_the_axis_that_defines_the_line():
    assert line_position(_v(0.3)) == 0.3
    assert line_position(_h(0.7)) == 0.7


def test_is_duplicate_matches_close_same_direction_lines():
    assert is_duplicate(_v(0.3), [_v(0.302)])
    assert not is_duplicate(_v(0.3), [_v(0.5)])


def test_is_duplicate_never_matches_across_directions():
    """竖线与横线位置数值可能巧合相等,不能因此判重。"""
    assert not is_duplicate(_v(0.3), [_h(0.3)])


def test_is_duplicate_on_empty_list():
    assert not is_duplicate(_v(0.3), [])


# ── 合并候选 ────────────────────────────────────────────────────

def test_merge_candidates_appends_only_new_memory_lines():
    detected = [_v(0.2), _v(0.5)]
    remembered = [_v(0.201), _v(0.8)]      # 前者与检出重复,后者是新的
    merged = merge_candidates(detected, remembered)
    assert len(merged) == 3
    assert [m["x1_norm"] for m in merged] == [0.2, 0.5, 0.8]


def test_merge_candidates_marks_memory_origin():
    merged = merge_candidates([_v(0.2)], [_v(0.9)])
    assert merged[0].get("from_memory") is None      # 自动检出的不带标记
    assert merged[1]["from_memory"] is True          # 记忆来的带标记


def test_merge_candidates_does_not_mutate_inputs():
    detected = [_v(0.2)]
    remembered = [_v(0.9)]
    merge_candidates(detected, remembered)
    assert len(detected) == 1
    assert "from_memory" not in remembered[0]


def test_merge_candidates_dedupes_within_memory_itself():
    """记忆里两条几乎重合的线,只该补进一条。"""
    merged = merge_candidates([], [_v(0.9), _v(0.9005)])
    assert len(merged) == 1


# ── 仓储 ────────────────────────────────────────────────────────

class _FakeDb:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.inserts: list[dict] = []
        self.execs: list[dict] = []

    async def fetch_all(self, sql, params):
        return self.rows

    async def fetch_one(self, sql, params):
        self.inserts.append(params)
        return {"id": "m1"}

    async def execute(self, sql, params):
        self.execs.append(params)


def _row(direction, x1, y1, x2, y2, same=True):
    return {"id": "m0", "direction": direction, "x1_norm": x1, "y1_norm": y1,
            "x2_norm": x2, "y2_norm": y2, "same_drawing": same}


@pytest.mark.asyncio
async def test_remember_line_inserts_new_position():
    from services.axis_line_memory import remember_line

    db = _FakeDb(rows=[])
    got = await remember_line(
        db, project_id="p1", drawing_id="d1", line=_v(0.4),
        page_aspect=1.41, created_by="u1")
    assert got == "m1" and len(db.inserts) == 1
    assert db.inserts[0]["direction"] == "x"


@pytest.mark.asyncio
async def test_remember_line_skips_when_already_remembered():
    """已有近似记忆就不重复堆积,否则同一条线会攒出几十条。"""
    from services.axis_line_memory import remember_line

    db = _FakeDb(rows=[_row("x", 0.4, 0, 0.4, 1)])
    got = await remember_line(
        db, project_id="p1", drawing_id="d1", line=_v(0.4015),
        page_aspect=1.41, created_by="u1")
    assert got is None and db.inserts == []


@pytest.mark.asyncio
async def test_fetch_memory_normalizes_rows():
    from services.axis_line_memory import fetch_memory

    db = _FakeDb(rows=[_row("y", 0, 0.6, 1, 0.6, same=False)])
    got = await fetch_memory(db, project_id="p1", drawing_id="d1", page_aspect=1.41)
    assert got == [{"id": "m0", "direction": "y", "x1_norm": 0.0, "y1_norm": 0.6,
                    "x2_norm": 1.0, "y2_norm": 0.6, "same_drawing": False}]


@pytest.mark.asyncio
async def test_bump_hit_records_usage():
    from services.axis_line_memory import bump_hit

    db = _FakeDb()
    await bump_hit(db, "m1")
    assert db.execs == [{"id": "m1"}]
