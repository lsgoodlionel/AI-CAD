"""顶点数闸的叠框核验：红=顶点数 ≥ 阈值（判据要删），绿=保留。

判据是逐个候选的，所以红绿画在同一张图上 —— 误伤一眼可见。
"""
import asyncio, gc, json, os
import databases as dbl, fitz
from PIL import Image, ImageDraw
from core.config import settings
from core.model3d.yolo_export import meters_to_page
from core.storage import get_file_bytes
from core.model3d.geometry_extractor import extract_pdf_geometry
from core.model3d import element_recognizer as ER
from probe_fp_scan import columns_only, _VTX

# 叠框核验要看**改动前**的候选，所以先把形状闸开到最大；红绿由本脚本自己判。
ER.MAX_COLUMN_OUTLINE_POINTS = 10 ** 9

TITLES = json.loads(os.environ.get("FP_TITLES", "[]"))
LIMIT = int(os.environ.get("FP_VTX", "16"))
OUT, DPI = "/tmp/fp_check", int(os.environ.get("FP_DPI", "110"))
os.makedirs(OUT, exist_ok=True)


async def main():
    db = dbl.Database(settings.database_url, min_size=1, max_size=2)
    await db.connect()
    for n, title in enumerate(TITLES):
        row = await db.fetch_one(
            "SELECT d.id::text AS id, d.title, d.discipline, d.file_key, "
            "t.scale_m_pt FROM drawings d "
            "LEFT JOIN drawing_transform t ON t.drawing_id=d.id "
            "WHERE d.title=:t LIMIT 1", {"t": title})
        if not row:
            print("找不到", title); continue
        data = get_file_bytes(row["file_key"])
        geom = extract_pdf_geometry(data)
        fe = columns_only(geom, row["discipline"], row["id"], row["title"],
                          float(row["scale_m_pt"]) if row["scale_m_pt"] else None)
        if not fe.scale or not fe.columns:
            print("无候选", title); continue
        k = DPI / 72.0
        boxes = []
        for c in fe.columns:
            o = c.get("outline") or []
            if len(o) < 3:
                continue
            px = [meters_to_page(mx, my, fe.scale, fe.origin_pt, fe.page_h)
                  for mx, my in o]
            xs = [p[0] for p in px]; ys = [p[1] for p in px]
            key = (round(min(xs), 1), round(min(ys), 1),
                   round(max(xs), 1), round(max(ys), 1))
            vtx = _VTX.get(key, 0)
            boxes.append((min(xs) * k, min(ys) * k, max(xs) * k, max(ys) * k, vtx))
        cut = [b for b in boxes if b[4] >= LIMIT]
        print(f"{str(title)[:34]:36s} 候选 {len(boxes):4d} 顶点≥{LIMIT} 删 {len(cut):4d}",
              flush=True)
        doc = fitz.open(stream=data, filetype="pdf")
        page = doc[0]
        pix = page.get_pixmap(dpi=DPI)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        dr = ImageDraw.Draw(img)
        for x0, y0, x1, y1, vtx in boxes:
            dr.rectangle([x0, y0, x1, y1],
                         outline=(220, 0, 0) if vtx >= LIMIT else (0, 160, 0),
                         width=2)
        img.save(f"{OUT}/v{n:02d}.png")
        doc.close(); del doc, page, pix, img, geom, data
        gc.collect()
    await db.disconnect()

asyncio.run(main())
