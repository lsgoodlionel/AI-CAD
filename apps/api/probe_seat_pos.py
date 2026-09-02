"""补扫判据依据所在的那几张图，让 `probe_seat_shape.py` 可复现。

全库扫描按 id 均分，这几张图要等很久才轮到；单独扫一遍落进同一个目录
（读取端按 did 去重，不会重复计数）。
"""
import asyncio, json, os
import databases as dbl
from core.config import settings
from core.model3d.element_recognizer import recognize
from core.model3d.geometry_extractor import extract_pdf_geometry
from core.storage import get_file_bytes

TITLES = ["建筑-竣工图--二层平面图(五)", "建筑-竣工图--三层平面图(三)",
          "结构-竣工图--南区（大、中歌剧厅）一层结构平面图（四）",
          "结构-竣工图--南区（大、中歌剧厅）地下一层结构平面图（四）"]
OUT = "/tmp/seat_scan/shard_pos.json"


async def main():
    db = dbl.Database(settings.database_url, min_size=1, max_size=2)
    await db.connect()
    out = json.load(open(OUT)) if os.path.exists(OUT) else []
    have = {r["title"] for r in out}
    for t in TITLES:
        if t in have:
            continue
        row = await db.fetch_one(
            "SELECT d.id::text AS id, d.title, d.discipline, d.file_key, t.scale_m_pt "
            "FROM drawings d LEFT JOIN drawing_transform t ON t.drawing_id = d.id "
            "WHERE d.title = :t LIMIT 1", {"t": t})
        if not row:
            print("找不到", t); continue
        geom = extract_pdf_geometry(get_file_bytes(row["file_key"]))
        fe = recognize(geom, row["discipline"], row["id"], drawing_title=row["title"],
                       scale_override=float(row["scale_m_pt"])
                       if row["scale_m_pt"] else None)
        # 判据已接进识别器，落盘要的是**改动前**的全体候选 = 留下的 + 被剔除的
        cols = []
        for c in list(fe.columns) + list(fe.dense_arrays):
            o = c.get("outline") or []
            if len(o) < 3:
                continue
            xs = [p[0] for p in o]; ys = [p[1] for p in o]
            cols.append([round((min(xs) + max(xs)) / 2, 3),
                         round((min(ys) + max(ys)) / 2, 3),
                         round(max(xs) - min(xs), 3), round(max(ys) - min(ys), 3)])
        out.append({"did": row["id"], "title": row["title"],
                    "discipline": row["discipline"], "cols": cols, "err": None})
        print(f"{t[:34]:36s} 候选 {len(cols)}")
    json.dump(out, open(OUT, "w"))
    await db.disconnect()

asyncio.run(main())
