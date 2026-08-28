"""存量场景 vs 当前代码：柱数量差多少 —— 模型该不该重建。"""
import asyncio, collections, json, random
import databases as dbl
from core.config import settings
from core.model3d.element_recognizer import recognize
from core.model3d.geometry_extractor import extract_pdf_geometry
from core.storage import get_file_bytes
random.seed(7)

async def main():
    db = dbl.Database(settings.database_url); await db.connect()
    for pid, nm in (("77777777-7777-7777-7777-777777777777", "metro"),
                    ("9188e163-c684-415e-a4ec-08f208273eff", "sgoh")):
        r = await db.fetch_one("SELECT scene FROM project_models WHERE project_id=:p "
                               "ORDER BY version DESC LIMIT 1", {"p": pid})
        if not r:
            continue
        sc = r["scene"] if isinstance(r["scene"], dict) else json.loads(r["scene"])
        by = collections.Counter()
        for f in sc.get("floors", []):
            for c in (f.get("elements") or {}).get("columns") or []:
                by[str(c.get("src"))] += 1
        dids = random.sample(sorted(by), min(25, len(by)))
        old = new = 0
        for did in dids:
            row = await db.fetch_one("SELECT title,discipline,file_key FROM drawings "
                                     "WHERE id::text=:d", {"d": did})
            if not row:
                continue
            try:
                geom = extract_pdf_geometry(get_file_bytes(row["file_key"]))
                tr = await db.fetch_one("SELECT scale_m_pt FROM drawing_transform "
                                        "WHERE drawing_id=:d", {"d": did})
                fe = recognize(geom, row["discipline"], did,
                               drawing_title=row["title"],
                               scale_override=float(tr["scale_m_pt"]) if tr else None)
            except Exception:
                continue
            old += by[did]; new += len(fe.columns)
        print(f"{nm}: 抽 {len(dids)} 张图 —— 存量场景 {old} 根柱，"
              f"当前代码 {new} 根，**差 {new-old:+d} = {(new-old)/max(old,1):+.0%}**")
    await db.disconnect()
asyncio.run(main())
