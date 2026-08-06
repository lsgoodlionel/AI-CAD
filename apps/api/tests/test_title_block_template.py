"""图框字段区域记忆库单测。"""
import pytest

from services.title_block_template import (
    aspect_bucket, normalize_region, texts_in_region, value_from_region,
)

PAGE_H = 1000.0


def _b(x, y, w=40.0, h=9.0):
    """页面点坐标 bbox。"""
    return [x, y, x + w, y + h]


# ── 版式分桶 ────────────────────────────────────────────────────

def test_aspect_bucket_groups_same_sheet_format():
    assert aspect_bucket(1414, 1000) == aspect_bucket(1414.2, 1000.1)
    assert aspect_bucket(1414, 1000) != aspect_bucket(1000, 1414)


def test_aspect_bucket_rejects_degenerate_pages():
    assert aspect_bucket(0, 1000) is None
    assert aspect_bucket(1000, 0) is None


# ── 框选规范化 ──────────────────────────────────────────────────

def test_normalize_region_orders_corners_any_drag_direction():
    assert normalize_region(0.8, 0.6, 0.2, 0.1) == (0.2, 0.1, 0.8, 0.6)
    assert normalize_region(0.2, 0.1, 0.8, 0.6) == (0.2, 0.1, 0.8, 0.6)


def test_normalize_region_rejects_zero_area():
    assert normalize_region(0.5, 0.5, 0.5, 0.9) is None      # 退化成线
    assert normalize_region(0.5, 0.5, 0.5, 0.5) is None      # 退化成点


# ── 区域取文本 ──────────────────────────────────────────────────

ITEMS = [
    ("专业", _b(2026, 1459, 15)), ("给排水", _b(2074, 1459)),
    ("图号", _b(2026, 1477, 15)), ("IP-41-30", _b(2065, 1477)),
    ("上海大歌剧院", _b(1900, 1360, 90)),      # 区域外
]
REGION = (2.0, 1.45, 2.15, 1.47)              # 归一化(÷1000)后的专业行


def test_texts_in_region_picks_only_inside():
    got = texts_in_region(ITEMS, REGION, PAGE_H)
    assert "给排水" in got and "专业" in got
    assert "上海大歌剧院" not in got and "IP-41-30" not in got


def test_texts_in_region_separates_tokens_and_rows():
    """分隔符不能省:否则「图号」和「IP-41-30」粘成一个词,标签剔不掉。"""
    assert texts_in_region(ITEMS, REGION, PAGE_H) == "专业 给排水"
    region = (2.0, 1.45, 2.15, 1.49)          # 同时框住专业行与图号行
    assert "\n" in texts_in_region(ITEMS, region, PAGE_H)


def test_texts_in_region_empty_when_nothing_inside_or_bad_page():
    assert texts_in_region(ITEMS, (0.0, 0.0, 0.01, 0.01), PAGE_H) == ""
    assert texts_in_region(ITEMS, REGION, 0) == ""


# ── 区域 → 字段值 ───────────────────────────────────────────────

def test_value_from_region_reads_discipline_and_strips_label():
    """框选难免连标签一起框进来,「专业」二字须剔除。"""
    assert value_from_region(ITEMS, REGION, PAGE_H, "discipline") == "给排水"


def test_value_from_region_reads_drawing_no():
    region = (2.0, 1.472, 2.15, 1.49)
    assert value_from_region(ITEMS, region, PAGE_H, "drawing_no") == "IP-41-30"


def test_value_from_region_rejects_value_failing_field_rules():
    # 专业词表外 → None(宁缺勿错)
    items = [("阶段", _b(100, 100, 15)), ("施工图设计", _b(150, 100))]
    region = (0.09, 0.09, 0.25, 0.12)
    assert value_from_region(items, region, PAGE_H, "discipline") is None
    # 图号不能含汉字
    assert value_from_region(items, region, PAGE_H, "drawing_no") is None


def test_value_from_region_returns_none_on_empty_region_or_unknown_field():
    assert value_from_region(ITEMS, (0.0, 0.0, 0.01, 0.01), PAGE_H, "discipline") is None
    assert value_from_region(ITEMS, REGION, PAGE_H, "不支持的字段") is None


def test_value_from_region_title_keeps_readable_text():
    items = [("五层卫生间详图", _b(100, 100, 90))]
    assert value_from_region(items, (0.09, 0.09, 0.20, 0.12), PAGE_H, "title") \
        == "五层卫生间详图"


# ── 仓储 ────────────────────────────────────────────────────────

class _FakeDb:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.calls: list[tuple[str, dict]] = []

    async def fetch_one(self, sql, params):
        self.calls.append(("one", params))
        return {"id": "t1"}

    async def fetch_all(self, sql, params):
        self.calls.append(("all", params))
        return self.rows

    async def execute(self, sql, params):
        self.calls.append(("exec", params))


@pytest.mark.asyncio
async def test_save_and_find_and_bump():
    from services.title_block_template import bump_hits, find_templates, save_template

    db = _FakeDb(rows=[{"id": "t1", "project_id": "p1", "field": "discipline",
                        "x1": 0.1, "y1": 0.2, "x2": 0.3, "y2": 0.25,
                        "page_aspect": 1.41, "hit_count": 3}])
    assert await save_template(
        db, project_id="p1", field="discipline", region=(0.1, 0.2, 0.3, 0.25),
        page_aspect=1.41, source_drawing_id="d1", created_by="u1") == "t1"
    found = await find_templates(db, project_id="p1", field="discipline", page_aspect=1.41)
    assert found[0]["hit_count"] == 3
    await bump_hits(db, "t1", 7)
    assert db.calls[-1][1]["n"] == 7


@pytest.mark.asyncio
async def test_bump_hits_ignores_non_positive():
    from services.title_block_template import bump_hits
    db = _FakeDb()
    await bump_hits(db, "t1", 0)
    assert db.calls == []


# ── 区域有效性与模板去重(批量套用的性能与稳定性)──────────────────

def test_normalize_region_threshold_scales_with_page_size():
    """实测 5084×2412pt 的图上「给排水」那格归一化只有 0.0046×0.0030——
    固定归一化门槛会把合法框选判成空,必须按物理尺寸(pt)判。"""
    from services.title_block_template import normalize_region

    big_page = 2412.0          # 超大图页高
    # 该图上一格字段的真实归一化尺寸(物理 7.2pt 高),必须放行
    assert normalize_region(1.0, 0.400, 1.0046, 0.4030, page_h_pt=big_page) is not None
    # 同一归一化尺寸放到 A4 页(595pt)上物理只有 1.8pt——比字还小,应判无效。
    # 这正是「按物理尺寸判」的意义:同一个数字在不同图幅上含义完全不同
    assert normalize_region(0.5, 0.5, 0.5046, 0.5030, page_h_pt=595.0) is None
    # A4 上一格字段的真实归一化尺寸(约 8pt 高)照样放行
    assert normalize_region(0.5, 0.5, 0.55, 0.514, page_h_pt=595.0) is not None


def test_normalize_region_still_rejects_degenerate_boxes():
    """只点一下没拖动 → 零面积,裁出来 OCR 后端会崩。"""
    from services.title_block_template import normalize_region

    assert normalize_region(0.5, 0.5, 0.5, 0.5, page_h_pt=842.0) is None
    assert normalize_region(0.5, 0.5, 0.5, 0.9, page_h_pt=842.0) is None   # 退化成线
    # 不足 3pt 的框(842pt 页上约 0.0036)判无效
    assert normalize_region(0.5, 0.5, 0.5020, 0.5020, page_h_pt=842.0) is None


def test_normalize_region_without_page_height_uses_loose_fallback():
    """拿不到页高时门槛取得足够松,宁可放行也不误杀。"""
    from services.title_block_template import normalize_region

    assert normalize_region(0.5, 0.5, 0.505, 0.503) is not None
    assert normalize_region(0.5, 0.5, 0.5001, 0.5001) is None


def test_dedupe_templates_keeps_first_of_each_cluster():
    """人反复框同一格会攒出近重复模板,逐个试会把耗时放大数倍。"""
    from services.title_block_template import dedupe_templates

    tpls = [
        {"id": "a", "x1": 1.078, "y1": 0.095, "x2": 1.341, "y2": 0.118},
        {"id": "b", "x1": 1.077, "y1": 0.097, "x2": 1.341, "y2": 0.120},   # 近重复
        {"id": "c", "x1": 1.078, "y1": 0.097, "x2": 1.358, "y2": 0.116},   # 近重复
        {"id": "d", "x1": 2.046, "y1": 0.966, "x2": 2.078, "y2": 0.974},   # 另一处
    ]
    kept = dedupe_templates(tpls)
    assert [k["id"] for k in kept] == ["a", "d"]


def test_dedupe_templates_preserves_input_order_priority():
    from services.title_block_template import dedupe_templates

    tpls = [{"id": "high", "x1": 0.1, "y1": 0.1, "x2": 0.3, "y2": 0.2},
            {"id": "low", "x1": 0.105, "y1": 0.105, "x2": 0.3, "y2": 0.2}]
    assert dedupe_templates(tpls)[0]["id"] == "high"
