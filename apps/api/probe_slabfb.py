import asyncio, json, collections, databases as dbl
from core.config import settings
from core.model3d.element_recognizer import recognize
from core.model3d.geometry_extractor import extract_pdf_geometry
from core.storage import get_file_bytes
FB = ("column_envelope", "axis_envelope")
async def m():
    db = dbl.Database(settings.database_url); await db.connect()
    why = collections.Counter(); titles = collections.Counter()
    for pid in ("77777777-7777-7777-7777-777777777777",
                "9188e163-c684-415e-a4ec-08f208273eff"):
        r = await db.fetch_one("SELECT scene FROM project_models WHERE project_id=:p "
                               "ORDER BY version DESC LIMIT 1", {"p": pid})
        if not r: continue
        sc = r["scene"] if isinstance(r["scene"], dict) else json.loads(r["scene"])
        for f in sc.get("floors", []):
            for s in (f.get("elements") or {}).get("slabs") or []:
                if str(s.get("basis")) not in FB or s.get("scale_suspect"): continue
                did = str(s.get("src"))
                row = await db.fetch_one("SELECT title,discipline,file_key FROM drawings "
                                         "WHERE id::text=:d", {"d": did})
                if not row: why["图纸记录不存在"] += 1; continue
                titles[str(row["title"])[:34]] += 1
                try:
                    geom = extract_pdf_geometry(get_file_bytes(row["file_key"]))
                except Exception as e:
                    why[f"几何解析失败:{type(e).__name__}"] += 1; continue
                tr = await db.fetch_one("SELECT scale_m_pt FROM drawing_transform "
                                        "WHERE drawing_id=:d", {"d": did})
                try:
                    fe = recognize(geom, row["discipline"], did, drawing_title=row["title"],
                                   scale_override=float(tr["scale_m_pt"]) if tr else None)
                except Exception as e:
                    why[f"识别失败:{type(e).__name__}"] += 1; continue
                why["scale 为空" if not fe.scale else "可渲染"] += 1
    print("兜底板逐块诊断:", dict(why.most_common()))
    print("所在图纸:", dict(titles.most_common(6)))
    await db.disconnect()
asyncio.run(m())
