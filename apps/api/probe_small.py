"""存量场景里的超小柱，用**现在的代码**重跑还在不在？"""
import asyncio, collections, json
import databases as dbl
from core.config import settings
from core.model3d.element_recognizer import recognize
from core.model3d.geometry_extractor import extract_pdf_geometry
from core.storage import get_file_bytes

async def main():
    db = dbl.Database(settings.database_url); await db.connect()
    dids = []
    for l in open("/tmp/gpt_size/manifest.tsv").read().splitlines()[1:]:
        tag, proj, did, ti, w, h = l.split("\t")
        if min(float(w), float(h)) < 100:
            dids.append(did)
    dids = sorted(set(dids))
    print(f"含超小柱的图 {len(dids)} 张，用现在的代码重跑：")
    tot = small = 0
    for did in dids:
        row = await db.fetch_one("SELECT title,discipline,file_key FROM drawings "
                                 "WHERE id::text=:d", {"d": did})
        if not row:
            continue
        try:
            geom = extract_pdf_geometry(get_file_bytes(row["file_key"]))
            tr = await db.fetch_one("SELECT scale_m_pt FROM drawing_transform "
                                    "WHERE drawing_id=:d", {"d": did})
            fe = recognize(geom, row["discipline"], did, drawing_title=row["title"],
                           scale_override=float(tr["scale_m_pt"]) if tr else None)
        except Exception as e:
            print("  跳过", type(e).__name__); continue
        n = s = 0
        for c in fe.columns:
            o = c.get("outline") or []
            if len(o) < 3:
                continue
            xs = [p[0] for p in o]; ys = [p[1] for p in o]
            n += 1
            if min(max(xs) - min(xs), max(ys) - min(ys)) < 0.1:
                s += 1
        tot += n; small += s
        print(f"   {str(row['title'])[:28]:30s} 柱 {n:4d}  短边<100mm {s:4d}")
    print(f"\n合计 {tot} 根，超小 {small} = {small/max(tot,1):.1%}")
    await db.disconnect()
asyncio.run(main())
