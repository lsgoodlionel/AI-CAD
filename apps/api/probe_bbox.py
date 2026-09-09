"""档案 bbox 与页面坐标对不对得上 —— 用 PyMuPDF 自己搜同一段文字作比对。"""
import asyncio, json
import databases as dbl, fitz
from core.config import settings
from core.storage import get_file_bytes

async def main():
    db = dbl.Database(settings.database_url); await db.connect()
    rows = await db.fetch_all("""
        SELECT e.content, e.location_json, e.extractor, d.file_key
        FROM drawing_extracted_info e JOIN drawings d ON d.id = e.drawing_id
        WHERE e.is_active AND e.category='elevation' AND e.location_json IS NOT NULL
          AND length(e.content) BETWEEN 4 AND 12
        LIMIT 60""")
    ok = off = notfound = 0
    samples = []
    for r in rows:
        loc = r["location_json"]
        loc = json.loads(loc) if isinstance(loc, str) else loc
        bb = (loc or {}).get("bbox")
        if not bb:
            continue
        try:
            page = fitz.open(stream=get_file_bytes(r["file_key"]), filetype="pdf")[0]
            hits = page.search_for(str(r["content"]).strip())
        except Exception:
            continue
        if not hits:
            notfound += 1
            continue
        bx = ((bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2)
        best = min(((abs(h.x0 + h.x1) / 2 - bx[0]) ** 2
                    + ((h.y0 + h.y1) / 2 - bx[1]) ** 2) ** 0.5 for h in hits)
        if best < 20:
            ok += 1
        else:
            off += 1
            if len(samples) < 6:
                h = hits[0]
                samples.append((str(r["content"])[:14], str(r["extractor"]),
                                f"档案({bx[0]:.0f},{bx[1]:.0f})",
                                f"实际({(h.x0+h.x1)/2:.0f},{(h.y0+h.y1)/2:.0f})",
                                f"差{best:.0f}pt"))
    tot = ok + off
    print(f"能在页面里搜到同一段文字的 {tot} 条（另有 {notfound} 条搜不到，多为 OCR 版）")
    if tot:
        print(f"   档案 bbox 与实际位置相差 <20pt 的 {ok} = {ok/tot:.0%}")
        print(f"   相差 ≥20pt 的 {off} = {off/tot:.0%}")
    for s in samples:
        print("   ", " ".join(s))
    await db.disconnect()
asyncio.run(main())
