"""图框「专业」栏读取单测。"""
import pytest

from services.title_block_discipline import (
    COARSE_MAP, backfill_project, coarse_discipline, discipline_from_items, find_field_value,
    is_field_label, normalize_discipline,
)


def _b(x, y, w=30.0, h=9.0):
    return [x, y, x + w, y + h]


# 照实测版式:标签「专业」在左,值在其右约 48pt 同一行;上下是「阶段」「图号」栏
REAL_TITLE_BLOCK = [
    ("阶段", _b(2026, 1442)), ("施工图设计", _b(2074, 1442)),
    ("专业", _b(2026, 1459, 15)), ("DISCIPLINE", _b(2031, 1470)),
    ("给排水", _b(2074, 1459)),
    ("图号", _b(2026, 1477)), ("IP-41-30", _b(2065, 1477)),
]


# ── 字段值定位 ──────────────────────────────────────────────────

def test_find_field_value_reads_value_right_of_label_same_row():
    assert find_field_value(REAL_TITLE_BLOCK, "专业") == "给排水"
    assert find_field_value(REAL_TITLE_BLOCK, "阶段") == "施工图设计"


def test_find_field_value_skips_neighbouring_field_labels():
    # 「DISCIPLINE」是同一栏的英文标签,不能当成专业值
    items = [("专业", _b(100, 100, 15)), ("DISCIPLINE", _b(120, 100)),
             ("电气", _b(160, 100))]
    assert find_field_value(items, "专业") == "电气"


def test_find_field_value_ignores_other_rows():
    items = [("专业", _b(100, 100, 15)), ("施工图设计", _b(150, 60)),
             ("结构", _b(150, 101))]
    assert find_field_value(items, "专业") == "结构"


def test_find_field_value_ignores_text_left_of_label():
    items = [("专业", _b(100, 100, 15)), ("比例1:50", _b(20, 100))]
    assert find_field_value(items, "专业") is None


def test_find_field_value_returns_none_without_label():
    assert find_field_value([("建筑", _b(10, 10))], "专业") is None


def test_is_field_label_covers_chinese_and_english():
    assert is_field_label("图号") and is_field_label("DISCIPLINE")
    assert is_field_label("专业：")
    assert not is_field_label("给排水")


def test_is_field_label_matches_ocr_garbled_english_labels():
    """实测糊字:DRAWING NO. → DRAING/DRAMING/DRABING NO.;DISCIPLINE → DISCIFLINE。"""
    for garbled in ("DRAING NO.", "DRAMING NO.", "DRABING NO.", "DRAW ING NO.",
                    "DISCIFLINE", "DISCIRLIE", "BISCIPLIE"):
        assert is_field_label(garbled), garbled


def test_is_field_label_does_not_swallow_real_drawing_numbers():
    """图号不能被当成标签,否则图号永远读不出来。"""
    for no in ("P-29-22", "ZNH-10-04", "S-0-10-004", "IP-41-23", "A-40-51.1"):
        assert not is_field_label(no), no


# ── 词表校验 ────────────────────────────────────────────────────

def test_normalize_discipline_maps_synonyms():
    assert normalize_discipline("给水排水") == "给排水"
    assert normalize_discipline("通风空调") == "暖通"
    assert normalize_discipline("围护") == "基坑围护"
    assert normalize_discipline("装饰") == "精装"


def test_normalize_discipline_rejects_out_of_vocab_and_noise():
    # 词表外一律判未知——宁可留空,也不写入错的专业
    assert normalize_discipline("施工图设计") is None
    assert normalize_discipline("JOB NO.") is None
    assert normalize_discipline("DISCIRLIE") is None      # OCR 乱码
    assert normalize_discipline("") is None
    assert normalize_discipline(None) is None
    assert normalize_discipline("这是一段很长的说明文字不可能是专业") is None


def test_normalize_discipline_handles_glued_ocr_text():
    assert normalize_discipline("专业给排水") == "给排水"


def test_coarse_discipline_maps_to_existing_enum():
    assert coarse_discipline("给排水") == "mep"
    assert coarse_discipline("基坑围护") == "structure"
    assert coarse_discipline("建筑") == "architecture"
    # 取值须落在 drawings.discipline 的 CHECK 约束内,否则写库失败
    assert coarse_discipline("幕墙") == "architecture"
    assert set(COARSE_MAP.values()) <= {
        "architecture", "structure", "mep", "decoration", "general"}
    assert coarse_discipline(None) is None


def test_find_field_value_skips_ocr_garbled_english_label():
    """实测:DISCIPLINE 被 OCR 糊成 DISILIE/DISCIPLIME,穷举写不完 → 按「必含汉字」跳过。"""
    items = [("专业", _b(2066, 1176, 10, 7)), ("DISILIE", _b(2068, 1180)),
             ("给排水", _b(2092, 1179))]
    # 模糊标签判定已能识出糊掉的 DISCIPLINE;require_cjk 是第二道保险
    assert find_field_value(items, "专业") == "给排水"
    assert find_field_value(items, "专业", require_cjk=True) == "给排水"


def test_discipline_from_items_survives_garbled_english_label():
    items = [("专业", _b(2036, 1159, 9, 6)), ("DISCIPLIME", _b(2038, 1163)),
             ("给排水", _b(2062, 1162))]
    assert discipline_from_items(items) == "给排水"


def test_discipline_from_items_end_to_end():
    assert discipline_from_items(REAL_TITLE_BLOCK) == "给排水"


# ── 回填 ────────────────────────────────────────────────────────

class _FakeDb:
    def __init__(self, rows):
        self.rows = rows
        self.updates: list[dict] = []

    async def fetch_all(self, sql, params):
        return self.rows

    async def execute(self, sql, params):
        self.updates.append(params)


def _row(did, content, bbox):
    return {"drawing_id": did, "content": content,
            "location_json": {"bbox": bbox}}


@pytest.mark.asyncio
async def test_backfill_writes_label_and_coarse_enum():
    db = _FakeDb([
        _row("d1", "专业", _b(100, 100, 15)), _row("d1", "基坑围护", _b(150, 100)),
        _row("d2", "专业", _b(100, 100, 15)), _row("d2", "给排水", _b(150, 100)),
    ])
    res = await backfill_project(db, "p1")
    assert res["found"] == 2 and res["updated"] == 2
    got = {u["id"]: (u["label"], u["coarse"]) for u in db.updates}
    assert got["d1"] == ("基坑围护", "structure")
    assert got["d2"] == ("给排水", "mep")
    assert res["distribution"] == {"基坑围护": 1, "给排水": 1}


@pytest.mark.asyncio
async def test_backfill_skips_drawings_without_readable_field():
    db = _FakeDb([_row("d1", "专业", _b(100, 100, 15)),
                  _row("d1", "施工图设计", _b(150, 100))])
    res = await backfill_project(db, "p1")
    assert res["scanned"] == 1 and res["found"] == 0
    assert db.updates == []


@pytest.mark.asyncio
async def test_backfill_dry_run_does_not_write():
    db = _FakeDb([_row("d1", "专业", _b(100, 100, 15)),
                  _row("d1", "电气", _b(150, 100))])
    res = await backfill_project(db, "p1", dry_run=True)
    assert res["found"] == 1 and res["updated"] == 0
    assert db.updates == []


@pytest.mark.asyncio
async def test_backfill_tolerates_string_json_and_missing_bbox():
    db = _FakeDb([
        {"drawing_id": "d1", "content": "专业", "location_json": '{"bbox":[100,100,115,109]}'},
        {"drawing_id": "d1", "content": "电气", "location_json": '{"bbox":[150,100,180,109]}'},
        {"drawing_id": "d1", "content": "噪声", "location_json": None},
        {"drawing_id": "d1", "content": "坏值", "location_json": "{不是json"},
    ])
    res = await backfill_project(db, "p1", dry_run=True)
    assert res["found"] == 1
