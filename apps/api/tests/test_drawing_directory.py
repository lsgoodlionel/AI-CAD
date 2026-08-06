"""图纸目录解析与默认排序单测。"""
import pytest

from services.drawing_directory import (
    RANK_DIRECTORY, RANK_LISTED, RANK_UNLISTED, assign_order, build_title_index,
    extract_entries, is_directory_sheet, match_entry, natural_key, normalize_title,
    rebuild_directory,
)


def _b(x, y, w=120.0, h=9.0):
    return [x, y, x + w, y + h]


# 照实测版式:图名列表头 DRAWINGNAME 在 x≈266,右邻版本列 VERSIONA 在 x≈746
DIRECTORY_ITEMS = [
    ("DRAWINGNo.", _b(198, 198, 60)), ("DRAWINGNAME", _b(266, 198, 80)),
    ("VERSIONA", _b(746, 198, 60)),
    ("给排水施工设计说明", _b(266, 212)),
    ("排水系统原理图", _b(266, 492)),
    ("消火栓系统原理图", _b(266, 643)), ("(二)", _b(400, 645, 20)),
    ("编制人：", _b(888, 253)),          # 右侧签名栏,不在图名列内
]


# ── 目录图判别 ──────────────────────────────────────────────────

def test_is_directory_sheet():
    assert is_directory_sheet("大歌剧院-给排水目录-施工图")
    assert is_directory_sheet("SHEET DRAWING LIST")
    assert not is_directory_sheet("三层平面图")
    assert not is_directory_sheet(None)
    # 含「目录」但明显是普通图纸的不算(否则会被顶到列表最前)
    assert not is_directory_sheet("目录索引平面图")


# ── 图号自然序 ──────────────────────────────────────────────────

def test_natural_key_orders_numeric_segments_by_value():
    nos = ["A-40-41", "A-10-13.4", "A-3-1", "S-0-20-202.01C"]
    assert sorted(nos, key=natural_key) == [
        "A-3-1", "A-10-13.4", "A-40-41", "S-0-20-202.01C"]


def test_natural_key_sinks_missing_drawing_no():
    assert natural_key(None) > natural_key("Z-99")


# ── 条目抽取 ────────────────────────────────────────────────────

def test_extract_entries_reads_name_column_top_down():
    assert extract_entries(DIRECTORY_ITEMS) == [
        "给排水施工设计说明", "排水系统原理图", "消火栓系统原理图(二)"]


def test_extract_entries_merges_same_row_fragments():
    """续行碎片「(二)」须拼回本行,而不是变成独立条目。"""
    got = extract_entries(DIRECTORY_ITEMS)
    assert "消火栓系统原理图(二)" in got
    assert "(二)" not in got


def test_extract_entries_excludes_other_columns():
    # 右侧签名栏「编制人：」在版本列之外,不能进目录
    assert not any("编制人" in e for e in extract_entries(DIRECTORY_ITEMS))


def test_extract_entries_returns_empty_without_header():
    assert extract_entries([("排水系统原理图", _b(266, 492))]) == []


def test_extract_entries_drops_too_short_fragments():
    items = [("DRAWINGNAME", _b(266, 198, 80)), ("（三）", _b(266, 300, 20))]
    assert extract_entries(items) == []


# ── 条目 ↔ 图纸匹配 ─────────────────────────────────────────────

def test_match_entry_uses_suffix_because_titles_carry_prefixes():
    """实测库里图名带前缀「给排水-竣工图--」,目录只写后半段。"""
    index = build_title_index([
        {"id": "d1", "title": "给排水-竣工图--自动喷水灭火系统原理图(二)"},
        {"id": "d2", "title": "建筑-竣工图--三层平面图"},
    ])
    assert match_entry("自动喷水灭火系统原理图（二）", index) == "d1"


def test_match_entry_prefers_shortest_title_among_candidates():
    index = build_title_index([
        {"id": "long", "title": "给排水-竣工图--补充说明--排水系统原理图"},
        {"id": "short", "title": "给排水--排水系统原理图"},
    ])
    assert match_entry("排水系统原理图", index) == "short"


def test_match_entry_returns_none_when_no_candidate():
    index = build_title_index([{"id": "d1", "title": "三层平面图"}])
    assert match_entry("不存在的图名XYZ", index) is None
    assert match_entry("", index) is None


def test_normalize_title_strips_punctuation_variants():
    assert normalize_title("消火栓系统原理图 （二)") == normalize_title("消火栓系统原理图(二)")


# ── 三层排序 ────────────────────────────────────────────────────

def test_assign_order_three_tiers():
    drawings = [
        {"id": "cat", "title": "给排水目录", "drawing_no": "P-00", "discipline": "mep"},
        {"id": "a", "title": "给排水--排水系统原理图", "drawing_no": "P-02", "discipline": "mep"},
        {"id": "b", "title": "给排水--雨水系统原理图", "drawing_no": "P-01", "discipline": "mep"},
        {"id": "z", "title": "无人提及的图", "drawing_no": "X-99", "discipline": "mep"},
    ]
    orders = {o["id"]: o for o in assign_order(
        drawings, [("cat", ["排水系统原理图", "雨水系统原理图"])])}
    assert orders["cat"]["sort_rank"] == RANK_DIRECTORY
    assert orders["a"]["sort_rank"] == RANK_LISTED
    assert orders["z"]["sort_rank"] == RANK_UNLISTED
    # 目录里排水在前、雨水在后 → 即使图号 P-02 > P-01,也按目录顺序
    assert orders["a"]["directory_seq"] < orders["b"]["directory_seq"]
    assert orders["a"]["directory_sheet_id"] == "cat"


def test_assign_order_unlisted_fall_back_to_discipline_and_natural_no():
    drawings = [
        {"id": "a", "title": "图一", "drawing_no": "A-40-41", "discipline_label": "建筑"},
        {"id": "b", "title": "图二", "drawing_no": "A-10-13.4", "discipline_label": "建筑"},
    ]
    orders = sorted(assign_order(drawings, []), key=lambda o: o["sort_key"])
    assert [o["id"] for o in orders] == ["b", "a"]      # A-10 在 A-40 之前


def test_assign_order_sorts_directory_sheets_by_natural_title_order():
    """「图纸目录2」必须排在「图纸目录12」之前(字典序会反过来)。"""
    drawings = [
        {"id": "s12", "title": "建筑-竣工图--图纸目录12", "discipline_label": "建筑"},
        {"id": "s2", "title": "建筑-竣工图--图纸目录2", "discipline_label": "建筑"},
    ]
    orders = sorted(assign_order(drawings, []), key=lambda o: o["sort_key"])
    assert [o["id"] for o in orders] == ["s2", "s12"]


def test_assign_order_never_lists_a_sheet_under_itself():
    drawings = [{"id": "cat", "title": "目录", "drawing_no": "P-00"}]
    orders = assign_order(drawings, [("cat", ["目录"])])
    assert orders[0]["sort_rank"] == RANK_DIRECTORY


# ── 重建 ────────────────────────────────────────────────────────

class _FakeDb:
    def __init__(self, drawings, items):
        self.drawings, self.items = drawings, items
        self.updates: list[dict] = []

    async def fetch_all(self, sql, params):
        return self.items if "drawing_extracted_info" in sql else self.drawings

    async def execute(self, sql, params):
        self.updates.append(params)


@pytest.mark.asyncio
async def test_rebuild_directory_reports_coverage_honestly():
    db = _FakeDb(
        drawings=[
            {"id": "cat", "title": "给排水目录", "drawing_no": "P-00", "discipline": "mep"},
            {"id": "a", "title": "给排水--排水系统原理图", "drawing_no": "P-02", "discipline": "mep"},
            {"id": "z", "title": "未收录的图", "drawing_no": "P-09", "discipline": "mep"},
        ],
        items=[
            {"drawing_id": "cat", "content": "DRAWINGNAME", "location_json": {"bbox": _b(266, 198, 80)}},
            {"drawing_id": "cat", "content": "VERSIONA", "location_json": {"bbox": _b(746, 198, 60)}},
            {"drawing_id": "cat", "content": "排水系统原理图", "location_json": {"bbox": _b(266, 212)}},
            {"drawing_id": "cat", "content": "查无此图的条目", "location_json": {"bbox": _b(266, 260)}},
        ],
    )
    res = await rebuild_directory(db, "p1")
    assert res["sheets"] == 1 and res["entries"] == 2
    assert res["linked"] == 1
    assert res["unlinked_entries"] == 1        # OCR 出来但对不上图纸的,如实报出
    assert res["unlisted_drawings"] == 1
    assert len(db.updates) == 3


@pytest.mark.asyncio
async def test_rebuild_directory_dry_run_does_not_write():
    db = _FakeDb(drawings=[{"id": "a", "title": "图", "drawing_no": "P-1"}], items=[])
    res = await rebuild_directory(db, "p1", dry_run=True)
    assert res["sheets"] == 0 and db.updates == []
