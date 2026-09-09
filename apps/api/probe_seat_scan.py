"""座椅误判 · 全库扫描 worker：导出每张图的柱候选坐标。

用法：python probe_seat_scan.py <shard_index> <shard_total>

**不导入 torch**（PyMuPDF 与 torch 同进程在 aarch64 段错误）。
每 40 张增量落盘 —— 一次跑几千张，中途崩掉不能从头再来。
`extract_pdf_geometry` 内部 finally 里 close 了 doc，这里再补 gc。
"""
import asyncio, gc, json, os, sys
import databases as dbl
from core.config import settings
from core.model3d.element_recognizer import recognize
from core.model3d.geometry_extractor import extract_pdf_geometry
from core.storage import get_file_bytes

IDX, TOT = int(sys.argv[1]), int(sys.argv[2])
OUT = f"/tmp/seat_scan/shard_{IDX}.json"
os.makedirs("/tmp/seat_scan", exist_ok=True)


async def main():
    # 最小连接池：8 个 worker × 默认 min_size=10 会撞 PG 的 max_connections。
    db = dbl.Database(settings.database_url, min_size=1, max_size=2)
    await db.connect()
    rows = await db.fetch_all(
        "SELECT d.id::text AS id, d.title, d.discipline, d.file_key, "
        "       t.scale_m_pt "
        "FROM drawings d LEFT JOIN drawing_transform t ON t.drawing_id = d.id "
        "ORDER BY d.id")
    mine = [r for i, r in enumerate(rows) if i % TOT == IDX]
    done = {}
    if os.path.exists(OUT):
        done = {r["did"]: r for r in json.load(open(OUT))}
    out = list(done.values())
    for k, row in enumerate(mine):
        if row["id"] in done:
            continue
        rec = {"did": row["id"], "title": row["title"],
               "discipline": row["discipline"], "cols": [], "err": None}
        try:
            geom = extract_pdf_geometry(get_file_bytes(row["file_key"]))
            fe = recognize(geom, row["discipline"], row["id"],
                           drawing_title=row["title"],
                           scale_override=float(row["scale_m_pt"])
                           if row["scale_m_pt"] else None)
            for c in fe.columns:
                o = c.get("outline") or []
                if len(o) < 3:
                    continue
                xs = [p[0] for p in o]; ys = [p[1] for p in o]
                rec["cols"].append([round((min(xs) + max(xs)) / 2, 3),
                                    round((min(ys) + max(ys)) / 2, 3),
                                    round(max(xs) - min(xs), 3),
                                    round(max(ys) - min(ys), 3)])
        except Exception as e:
            rec["err"] = type(e).__name__
        out.append(rec)
        del rec
        if k % 40 == 0:
            json.dump(out, open(OUT, "w"))
            gc.collect()
            print(f"[{IDX}] {k}/{len(mine)}", flush=True)
    json.dump(out, open(OUT, "w"))
    print(f"[{IDX}] DONE {len(out)}", flush=True)
    await db.disconnect()

asyncio.run(main())
