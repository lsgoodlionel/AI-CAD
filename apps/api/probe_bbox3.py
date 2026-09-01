"""排除替代解释：整页有没有文本层。

若整页无文本层，`get_text(clip=)` 在任何位置都取不到东西 ——
那测的是「没有文本层」，不是「bbox 错」。只在**整页有文本层**的图上判定。
"""
import asyncio, collections, json
import databases as dbl, fitz
from core.config import settings
from core.storage import get_file_bytes

async def main():
    db = dbl.Database(settings.database_url); await db.connect()
    rows = await db.fetch_all("""
        SELECT e.content, e.location_json, e.extractor, d.file_key, d.id
        FROM drawing_extracted_info e JOIN drawings d ON d.id = e.drawing_id
        WHERE e.is_active AND e.category='elevation' AND e.location_json IS NOT NULL
        LIMIT 200""")
    st = collections.Counter()
    pages: dict[str, object] = {}
    for r in rows:
        did = str(r["id"])
        if did not in pages:
            try:
                pages[did] = fitz.open(stream=get_file_bytes(r["file_key"]),
                                       filetype="pdf")[0]
            except Exception:
                pages[did] = None
        page = pages[did]
        if page is None:
            st["打不开"] += 1; continue
        full = page.get_text("text").strip()
        if not full:
            st["整页无文本层（判不了）"] += 1; continue
        loc = r["location_json"]
        loc = json.loads(loc) if isinstance(loc, str) else loc
        bb = (loc or {}).get("bbox")
        if not bb or len(bb) != 4:
            st["无 bbox"] += 1; continue
        rect = fitz.Rect(min(bb[0], bb[2]) - 8, min(bb[1], bb[3]) - 8,
                         max(bb[0], bb[2]) + 8, max(bb[1], bb[3]) + 8)
        st["bbox 里有字" if page.get_text("text", clip=rect).strip()
           else "**整页有文本层，但 bbox 里是空的**"] += 1
    tot = st["bbox 里有字"] + st["**整页有文本层，但 bbox 里是空的**"]
    for k, v in st.most_common():
        print(f"   {k:34s} {v:4d}")
    if tot:
        bad = st["**整页有文本层，但 bbox 里是空的**"]
        print(f"\n   可判定的 {tot} 条里，**bbox 落空 {bad} = {bad/tot:.0%}**")
    await db.disconnect()
asyncio.run(main())
