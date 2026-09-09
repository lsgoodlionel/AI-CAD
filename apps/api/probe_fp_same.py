"""等价性检查：`columns_only`（快路径）与 `recognize().columns` 必须逐个一致。"""
import asyncio
import databases as dbl
from core.config import settings
from core.model3d.element_recognizer import recognize
from core.model3d.geometry_extractor import extract_pdf_geometry
from core.storage import get_file_bytes
from probe_fp_scan import columns_only

async def main():
    db = dbl.Database(settings.database_url, min_size=1, max_size=2)
    await db.connect()
    rows = await db.fetch_all(
        "SELECT d.id::text AS id, d.title, d.discipline, d.file_key, t.scale_m_pt "
        "FROM drawings d LEFT JOIN drawing_transform t ON t.drawing_id=d.id "
        "ORDER BY d.id LIMIT 14 OFFSET 500")
    bad = 0
    for r in rows:
        geom = extract_pdf_geometry(get_file_bytes(r["file_key"]))
        so = float(r["scale_m_pt"]) if r["scale_m_pt"] else None
        a = recognize(geom, r["discipline"], r["id"], drawing_title=r["title"],
                      scale_override=so).columns
        b = columns_only(geom, r["discipline"], r["id"], r["title"], so).columns
        same = a == b
        bad += 0 if same else 1
        print(f"{'OK ' if same else 'DIFF'} {len(a):5d} vs {len(b):5d}  {str(r['title'])[:40]}",
              flush=True)
    print("不一致", bad, "/", len(rows))
    await db.disconnect()

asyncio.run(main())
