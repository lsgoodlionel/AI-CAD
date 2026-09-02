"""座椅误判 · 误伤核验：把被判据删掉的候选画出来，眼睛判。

红框 = 判据要删的，绿框 = 保留的。挑「结构平面图」优先 ——
那里删错就是把真柱删了，是本判据唯一的红线。

**不导入 torch**；`doc.close()` 必须显式调用（几十张大图后会在 glibc 层崩）。
"""
import asyncio, gc, os, sys
import databases as dbl, fitz
from PIL import Image, ImageDraw
from core.config import settings
from core.model3d.element_recognizer import recognize
from core.model3d.geometry_extractor import extract_pdf_geometry
from core.model3d.yolo_export import meters_to_page
from core.storage import get_file_bytes

import json as _json
TITLES = _json.loads(os.environ.get("SEAT_TITLES", "[]")) or [
    "建筑-竣工图--二层隔声隔振平面图（三）",
]
OUT, DPI, TILE = "/tmp/seat_check", 150, 900
os.makedirs(OUT, exist_ok=True)


async def main():
    db = dbl.Database(settings.database_url); await db.connect()
    for n, title in enumerate(TITLES):
        row = await db.fetch_one(
            "SELECT id::text AS id, title, discipline, file_key FROM drawings "
            "WHERE title = :t LIMIT 1", {"t": title})
        if not row:
            print("找不到", title); continue
        geom = extract_pdf_geometry(get_file_bytes(row["file_key"]))
        tr = await db.fetch_one(
            "SELECT scale_m_pt FROM drawing_transform WHERE drawing_id=:d",
            {"d": row["id"]})
        fe = recognize(geom, row["discipline"], row["id"],
                       drawing_title=row["title"],
                       scale_override=float(tr["scale_m_pt"]) if tr else None)
        # 红框 = 判据删掉的（`dense_arrays`），绿框 = 留下的柱
        cols = list(fe.columns) + list(fe.dense_arrays)
        flags = [False] * len(fe.columns) + [True] * len(fe.dense_arrays)
        if not fe.scale or not cols:
            print("无候选", title); continue
        k = DPI / 72.0
        boxes = []
        for c, f in zip(cols, flags):
            o = c.get("outline") or []
            if len(o) < 3:
                continue
            px = [meters_to_page(mx, my, fe.scale, fe.origin_pt, fe.page_h)
                  for mx, my in o]
            xs = [p[0] * k for p in px]; ys = [p[1] * k for p in px]
            boxes.append((min(xs), min(ys), max(xs), max(ys), f))
        cut = [b for b in boxes if b[4]]
        print(f"{title[:40]:42s} 候选 {len(boxes):4d} 删 {len(cut):4d}")
        if not cut:
            continue
        doc = fitz.open(stream=get_file_bytes(row["file_key"]), filetype="pdf")
        page = doc[0]
        cx = sum((b[0] + b[2]) / 2 for b in cut) / len(cut)
        cy = sum((b[1] + b[3]) / 2 for b in cut) / len(cut)
        pw, ph = page.rect.width * k, page.rect.height * k
        x0 = max(0, min(cx - TILE / 2, pw - TILE))
        y0 = max(0, min(cy - TILE / 2, ph - TILE))
        clip = fitz.Rect(x0 / k, y0 / k, (x0 + TILE) / k, (y0 + TILE) / k)
        pix = page.get_pixmap(dpi=DPI, clip=clip)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        dr = ImageDraw.Draw(img)
        for bx0, by0, bx1, by1, f in boxes:
            r = [bx0 - x0, by0 - y0, bx1 - x0, by1 - y0]
            if r[2] < 0 or r[3] < 0 or r[0] > TILE or r[1] > TILE:
                continue
            dr.rectangle(r, outline=(220, 0, 0) if f else (0, 160, 0), width=3)
        img.save(f"{OUT}/{n}_{'删' if cut else ''}.png")
        doc.close(); del doc, page, pix, img
        gc.collect()
    await db.disconnect()

asyncio.run(main())
