"""更稳的判据：档案 bbox 框住的地方，页面上到底有没有字？

不依赖字符串匹配（OCR 文字与 PDF 文本层常不一致），只问：
把 bbox 当页面坐标去取文字，取不取得到东西。
"""
import asyncio, collections, json
import databases as dbl, fitz
from core.config import settings
from core.storage import get_file_bytes

async def main():
    db = dbl.Database(settings.database_url); await db.connect()
    stat = collections.Counter()
    for cat in ("elevation", "room_name", "axis", "dimension"):
        rows = await db.fetch_all("""
            SELECT e.content, e.location_json, d.file_key
            FROM drawing_extracted_info e JOIN drawings d ON d.id = e.drawing_id
            WHERE e.is_active AND e.category = :c AND e.location_json IS NOT NULL
            LIMIT 120""", {"c": cat})
        hit = miss = skip = 0
        for r in rows:
            loc = r["location_json"]
            loc = json.loads(loc) if isinstance(loc, str) else loc
            bb = (loc or {}).get("bbox")
            if not bb or len(bb) != 4:
                skip += 1; continue
            try:
                page = fitz.open(stream=get_file_bytes(r["file_key"]),
                                 filetype="pdf")[0]
                # 放宽 8pt，避免边界擦肩
                rect = fitz.Rect(min(bb[0], bb[2]) - 8, min(bb[1], bb[3]) - 8,
                                 max(bb[0], bb[2]) + 8, max(bb[1], bb[3]) + 8)
                txt = page.get_text("text", clip=rect).strip()
            except Exception:
                skip += 1; continue
            if txt:
                hit += 1
            else:
                miss += 1
        tot = hit + miss
        if tot:
            print(f"{cat:10s} 检查 {tot:3d} 条：bbox 里**有字** {hit:3d} = {hit/tot:4.0%} "
                  f"· **空的** {miss:3d} = {miss/tot:4.0%}")
    await db.disconnect()
asyncio.run(main())
