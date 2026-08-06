"""图框记忆的应用编排:框选一次 → 更新本图 → 同步刷新同版式未读到的图纸。

分工:
- `title_block_discipline` —— 按标签找值(能覆盖大多数图)
- `title_block_template`   —— 人工框选的区域记忆(标签没被 OCR 出来时的兜底)
- 本模块                    —— 把两者串成「人工一次、批量收敛」的闭环

**为什么图号不自动覆盖文件名**:实测图框图号与文件名解析值只有 **86% 一致**,
不一致的多是 OCR 字符级糊字(`P-31-23` → `D-31-23`、`.01C` → `.010`)。
文件名是导入时的确定信息,更可信。故图号只在**文件名没给出**时才从图框补,
其余情况只作为**校验候选**记录,不静默覆盖。
"""
from __future__ import annotations

from typing import Any

from services.title_block_template import (
    aspect_bucket, dedupe_templates, find_templates, normalize_region_text,
    texts_in_region, value_from_region,
)

#: 每张图最多试几个模板。区域重识别每次数秒,试满 8 个模板就是几十秒一张。
MAX_TEMPLATES_PER_DRAWING = 3

#: 字段 → 落库列
FIELD_COLUMN = {
    "discipline": "discipline_label",
    "drawing_no": "drawing_no",
    "title": "title",
}

_PAGE_SQL = "SELECT file_key FROM drawings WHERE id = :id AND project_id = :pid"

_ITEMS_SQL = """
SELECT content, location_json FROM drawing_extracted_info
WHERE drawing_id = :id AND is_active AND location_json IS NOT NULL
"""

_UNREAD_SQL = """
SELECT d.id, d.file_key FROM drawings d
WHERE d.project_id = :project_id AND d.discipline_label IS NULL
"""


def _items(rows: list) -> list[tuple[str, list[float]]]:
    from services.title_block_discipline import _bbox
    out = []
    for r in rows:
        b = _bbox(r["location_json"])
        if b:
            out.append((r["content"] or "", b))
    return out


async def page_size(file_key: str | None) -> tuple[float, float] | None:
    """PDF 首页尺寸(pt)。非 PDF/读不到 → None。"""
    if not file_key or not str(file_key).lower().endswith(".pdf"):
        return None
    try:
        import asyncio

        import fitz

        from core.storage import get_file_bytes
        data = await asyncio.get_event_loop().run_in_executor(
            None, get_file_bytes, file_key)
        with fitz.open(stream=data, filetype="pdf") as doc:
            if doc.page_count < 1:
                return None
            rect = doc[0].rect
            return float(rect.width), float(rect.height)
    except Exception:  # noqa: BLE001 — 读不到尺寸则不套模板(宁缺勿错)
        return None


async def read_by_region(
    db: Any, *, project_id: str, drawing_id: str, field: str,
    region: tuple[float, float, float, float],
    override: str | None = None,
) -> dict:
    """按人工框选的区域读一张图的字段值并写库。

    三级取值,前一级失败才走下一级:
    1. **档案层文本** —— 已有 OCR,零成本;
    2. **区域重识别** —— 该格的字没被全图 OCR 认出来时,裁小图 300dpi 重认
       (实测 39 张有「专业」标签却读不出值,正是这种情况);
    3. **人工给值**(override)—— 重识别也糊了(实测「建筑」被认成「建 个人」)时,
       把原文回给人,人选定即为权威值。

    返回 {value, raw_text, page_aspect}:value 为 None 但 raw_text 有内容时,
    前端应让人确认——**不猜**。
    """
    row = await db.fetch_one(_PAGE_SQL, {"id": drawing_id, "pid": project_id})
    if row is None:
        return {"value": None, "raw_text": "", "page_aspect": None,
                "error": "DRAWING_NOT_FOUND"}
    size = await page_size(row["file_key"])
    if size is None:
        return {"value": None, "raw_text": "", "page_aspect": None,
                "error": "PAGE_SIZE_UNAVAILABLE"}
    aspect = aspect_bucket(*size)

    if override:
        await _write_field(db, drawing_id, field, override)
        return {"value": override, "raw_text": "", "page_aspect": aspect, "error": None}

    from services.learned_rules import fetch_rules
    learned = await fetch_rules(db, project_id)

    items = _items(await db.fetch_all(_ITEMS_SQL, {"id": drawing_id}))
    raw = texts_in_region(items, region, size[1])
    value = normalize_region_text(raw, field, learned)

    if value is None:
        raw2 = await _ocr_region(row["file_key"], region)
        if raw2:
            raw = raw2
            value = normalize_region_text(raw2, field, learned)

    if value:
        await _write_field(db, drawing_id, field, value)
    return {"value": value, "raw_text": raw, "page_aspect": aspect, "error": None}


async def _ocr_region(
    file_key: str | None, region: tuple[float, float, float, float],
) -> str:
    """裁区域高分辨率重识别(IO,失败返回空串)。"""
    if not file_key or not str(file_key).lower().endswith(".pdf"):
        return ""
    try:
        import asyncio

        from core.storage import get_file_bytes

        from services.title_block_template import ocr_region
        loop = asyncio.get_event_loop()
        data = await loop.run_in_executor(None, get_file_bytes, file_key)
        return await loop.run_in_executor(None, lambda: ocr_region(data, region))
    except Exception:  # noqa: BLE001 — 重识别失败降级
        return ""


async def _write_field(db: Any, drawing_id: str, field: str, value: str) -> None:
    """字段落库。专业同时按映射修正粗粒度枚举(规则引擎按它选型)。"""
    column = FIELD_COLUMN.get(field)
    if not column:
        return
    if field == "discipline":
        from services.title_block_discipline import coarse_discipline
        await db.execute(
            "UPDATE drawings SET discipline_label=:v, discipline=:c, updated_at=now() "
            "WHERE id=:id",
            {"v": value, "c": coarse_discipline(value) or "general", "id": drawing_id})
        return
    await db.execute(
        f"UPDATE drawings SET {column}=:v, updated_at=now() WHERE id=:id",
        {"v": value, "id": drawing_id})


#: 批量套用时允许做区域重识别的图纸数上限。
#: 重识别每张 ~1s(裁图 300dpi + OCR),不设上限会让一次请求跑几分钟。
DEFAULT_OCR_BUDGET = 120


async def apply_templates(
    db: Any, project_id: str, field: str = "discipline", *,
    limit: int = 500, dry_run: bool = False,
    ocr_budget: int = DEFAULT_OCR_BUDGET,
) -> dict:
    """把记忆库里的区域模板套到本项目**尚未读到该字段**的图纸上。

    逐图按其页面宽高比取同版式模板,按命中次数从高到低试;第一个读出合法值即采用。

    **档案文本读不到时会做区域重识别**——这是关键:这些图纸「未读到专业」的原因
    往往正是那格的字没被全图 OCR 认出来,只查档案必然一张也补不上(实测 updated=0)。
    重识别昂贵,故用 `ocr_budget` 限量,并如实报出预算用完还剩多少张没试。
    """
    from services.learned_rules import fetch_rules
    learned = await fetch_rules(db, project_id)

    rows = await db.fetch_all(_UNREAD_SQL, {"project_id": project_id})
    targets = list(rows)[:limit]
    template_cache: dict[float | None, list[dict]] = {}
    hits: dict[str, int] = {}
    updated = 0
    no_template = 0
    ocr_used = 0
    ocr_skipped = 0

    for target in targets:
        size = await page_size(target["file_key"])
        if size is None:
            continue
        aspect = aspect_bucket(*size)
        if aspect not in template_cache:
            found = await find_templates(
                db, project_id=project_id, field=field, page_aspect=aspect)
            template_cache[aspect] = dedupe_templates(found)[
                :MAX_TEMPLATES_PER_DRAWING]
        templates = template_cache[aspect]
        if not templates:
            no_template += 1
            continue

        items = _items(await db.fetch_all(_ITEMS_SQL, {"id": str(target["id"])}))
        matched: tuple[str, str] | None = None       # (value, template_id)
        pending_ocr: list[dict] = []

        for tpl in templates:                         # 先走零成本的档案文本
            region = (float(tpl["x1"]), float(tpl["y1"]),
                      float(tpl["x2"]), float(tpl["y2"]))
            value = normalize_region_text(
                texts_in_region(items, region, size[1]), field, learned)
            if value:
                matched = (value, str(tpl["id"]))
                break
            pending_ocr.append(tpl)

        if matched is None and pending_ocr:
            if ocr_used >= ocr_budget:
                ocr_skipped += 1
            else:
                ocr_used += 1
                for tpl in pending_ocr:               # 档案没有 → 区域重识别
                    region = (float(tpl["x1"]), float(tpl["y1"]),
                              float(tpl["x2"]), float(tpl["y2"]))
                    raw = await _ocr_region(target["file_key"], region)
                    value = normalize_region_text(raw, field, learned) if raw else None
                    if value:
                        matched = (value, str(tpl["id"]))
                        break

        if matched is None:
            continue
        value, tpl_id = matched
        if not dry_run:
            await _write_field(db, str(target["id"]), field, value)
        hits[tpl_id] = hits.get(tpl_id, 0) + 1
        updated += 1

    if not dry_run:
        from services.title_block_template import bump_hits
        for tpl_id, n in hits.items():
            await bump_hits(db, tpl_id, n)

    return {"candidates": len(targets), "updated": updated,
            "no_template": no_template, "templates_used": len(hits),
            "ocr_used": ocr_used, "ocr_skipped": ocr_skipped}
