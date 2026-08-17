"""图框记忆应用编排单测(框选一次 → 更新本图 → 批量套用同版式)。"""
import pytest

import services.title_block_apply as mod
from services.title_block_apply import FIELD_COLUMN, apply_templates, read_by_region


def _b(x, y, w=40.0, h=9.0):
    return [x, y, x + w, y + h]


class _FakeDb:
    """按 SQL 关键字分流的假库,记录写操作。"""

    def __init__(self, *, drawings=None, items=None, templates=None):
        self.drawings = drawings or {}
        self.items = items or {}
        self.templates = templates or []
        self.writes: list[dict] = []
        self.bumps: list[dict] = []

    async def fetch_one(self, sql, params):
        if "file_key FROM drawings" in sql:
            d = self.drawings.get(params["id"])
            return {"file_key": d} if d else None
        return {"id": "t-new"}

    async def fetch_all(self, sql, params):
        if "drawing_extracted_info" in sql:
            return self.items.get(params["id"], [])
        if "title_block_templates" in sql:
            return self.templates
        if "discipline_label IS NULL" in sql:
            return [{"id": k, "file_key": v} for k, v in self.drawings.items()]
        return []

    async def execute(self, sql, params):
        (self.bumps if "hit_count" in sql else self.writes).append(params)


def _row(content, bbox):
    return {"content": content, "location_json": {"bbox": bbox}}


@pytest.fixture
def fake_page(monkeypatch):
    """绕开 MinIO/PDF：页面固定 1414×1000(宽高比 1.41)。"""
    async def _size(file_key):
        return (1414.0, 1000.0) if file_key else None
    monkeypatch.setattr(mod, "page_size", _size)


# ── 框选读单张 ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_read_by_region_writes_discipline_and_coarse_enum(fake_page):
    db = _FakeDb(
        drawings={"d1": "a.pdf"},
        items={"d1": [_row("专业", _b(2026, 1459, 15)), _row("给排水", _b(2074, 1459))]},
    )
    res = await read_by_region(
        db, project_id="p1", drawing_id="d1", field="discipline",
        region=(2.0, 1.45, 2.15, 1.47))
    assert res["value"] == "给排水"
    assert res["page_aspect"] == 1.41
    assert db.writes[0]["v"] == "给排水" and db.writes[0]["c"] == "mep"


@pytest.mark.asyncio
async def test_read_by_region_does_not_write_when_value_invalid(fake_page):
    db = _FakeDb(drawings={"d1": "a.pdf"},
                 items={"d1": [_row("施工图设计", _b(2074, 1459))]})
    res = await read_by_region(
        db, project_id="p1", drawing_id="d1", field="discipline",
        region=(2.0, 1.45, 2.15, 1.47))
    assert res["value"] is None and db.writes == []


@pytest.mark.asyncio
async def test_read_by_region_reports_missing_drawing_and_page(fake_page):
    db = _FakeDb(drawings={})
    res = await read_by_region(db, project_id="p1", drawing_id="nope",
                               field="discipline", region=(0.1, 0.1, 0.2, 0.2))
    assert res["error"] == "DRAWING_NOT_FOUND"


# ── 批量套用 ────────────────────────────────────────────────────

TEMPLATE = {"id": "t1", "x1": 2.0, "y1": 1.45, "x2": 2.15, "y2": 1.47}


@pytest.mark.asyncio
async def test_apply_templates_fills_unread_drawings_and_bumps_hits(fake_page):
    db = _FakeDb(
        drawings={"d1": "a.pdf", "d2": "b.pdf"},
        items={
            "d1": [_row("给排水", _b(2074, 1459))],
            "d2": [_row("电气", _b(2074, 1459))],
        },
        templates=[TEMPLATE],
    )
    res = await apply_templates(db, "p1")
    assert res["candidates"] == 2 and res["updated"] == 2
    assert {w["v"] for w in db.writes} == {"给排水", "电气"}
    # 命中次数累加 → 记忆被强化,下次优先用
    assert db.bumps == [{"id": "t1", "n": 2}]


@pytest.mark.asyncio
async def test_apply_templates_skips_when_region_yields_nothing(fake_page):
    db = _FakeDb(drawings={"d1": "a.pdf"},
                 items={"d1": [_row("施工图设计", _b(2074, 1459))]},
                 templates=[TEMPLATE])
    res = await apply_templates(db, "p1")
    assert res["updated"] == 0 and db.writes == []


@pytest.mark.asyncio
async def test_apply_templates_reports_when_no_template_for_format(fake_page):
    db = _FakeDb(drawings={"d1": "a.pdf"}, items={"d1": []}, templates=[])
    res = await apply_templates(db, "p1")
    assert res["no_template"] == 1 and res["updated"] == 0


@pytest.mark.asyncio
async def test_apply_templates_dry_run_writes_nothing(fake_page):
    db = _FakeDb(drawings={"d1": "a.pdf"},
                 items={"d1": [_row("给排水", _b(2074, 1459))]},
                 templates=[TEMPLATE])
    res = await apply_templates(db, "p1", dry_run=True)
    assert res["updated"] == 1
    assert db.writes == [] and db.bumps == []


@pytest.mark.asyncio
async def test_apply_templates_respects_limit(fake_page):
    db = _FakeDb(drawings={f"d{i}": "x.pdf" for i in range(10)},
                 items={}, templates=[TEMPLATE])
    res = await apply_templates(db, "p1", limit=3)
    assert res["candidates"] == 3


def test_field_column_map_covers_supported_fields():
    assert set(FIELD_COLUMN) == {"discipline", "drawing_no", "title"}


# ── 档案读不到 → 区域重识别兜底(这是「同类图纸刷不动」的根因)──────

@pytest.mark.asyncio
async def test_apply_templates_falls_back_to_region_ocr(fake_page, monkeypatch):
    """未读到专业的图,往往正是那格没被全图 OCR 认出来;只查档案必然 0 命中。"""
    async def _ocr(file_key, region):
        return "给排水"
    monkeypatch.setattr(mod, "_ocr_region", _ocr)

    db = _FakeDb(drawings={"d1": "a.pdf"}, items={"d1": []}, templates=[TEMPLATE])
    res = await apply_templates(db, "p1")
    assert res["updated"] == 1 and res["ocr_used"] == 1
    assert db.writes[0]["v"] == "给排水"


@pytest.mark.asyncio
async def test_apply_templates_skips_ocr_when_archive_already_has_value(
    fake_page, monkeypatch,
):
    """档案里有值就别做重识别——重识别每张约 1s,白花。"""
    called: list[int] = []

    async def _ocr(file_key, region):
        called.append(1)
        return "电气"
    monkeypatch.setattr(mod, "_ocr_region", _ocr)

    db = _FakeDb(drawings={"d1": "a.pdf"},
                 items={"d1": [_row("给排水", _b(2074, 1459))]},
                 templates=[TEMPLATE])
    res = await apply_templates(db, "p1")
    assert res["updated"] == 1 and res["ocr_used"] == 0 and called == []


@pytest.mark.asyncio
async def test_apply_templates_respects_ocr_budget_and_reports_skipped(
    fake_page, monkeypatch,
):
    """预算用完要如实报出还剩多少张没试,而不是假装跑完了。"""
    async def _ocr(file_key, region):
        return "电气"
    monkeypatch.setattr(mod, "_ocr_region", _ocr)

    db = _FakeDb(drawings={f"d{i}": "x.pdf" for i in range(5)},
                 items={}, templates=[TEMPLATE])
    res = await apply_templates(db, "p1", ocr_budget=2)
    assert res["ocr_used"] == 2
    assert res["ocr_skipped"] == 3
    assert res["updated"] == 2
