"""假柱候选 · 叠框核验：把闸要删的候选画出来，眼睛判。

红框 = 闸要删的，绿框 = 保留的。判据以环境变量传入，与 `probe_fp_eval.py`
用的是同一套定义（`FP_GATE`），保证「统计里删的」与「图上红的」是一回事。

**不导入 torch**；`doc.close()` 必须显式调用（几十张大图后会在 glibc 层崩）。
整页出图（不裁瓦片）——本轮要看的是**候选落在图面哪个区域**
（剖面图/图框标题栏/大样区），裁掉上下文就看不出来了。
"""
import asyncio, gc, json, os
import databases as dbl, fitz
from PIL import Image, ImageDraw
from core.config import settings
from core.model3d.element_recognizer import recognize
from core.model3d.geometry_extractor import extract_pdf_geometry
from core.model3d.yolo_export import meters_to_page
from core.storage import get_file_bytes
from services.drawing_view_classifier import classify_view_type

TITLES = json.loads(os.environ.get("FP_TITLES", "[]"))
GATE = os.environ.get("FP_GATE", "view")   # view | none
OUT, DPI = "/tmp/fp_check", int(os.environ.get("FP_DPI", "110"))
os.makedirs(OUT, exist_ok=True)


async def main():
    db = dbl.Database(settings.database_url, min_size=1, max_size=2)
    await db.connect()
    for n, title in enumerate(TITLES):
        row = await db.fetch_one(
            "SELECT id::text AS id, title, drawing_no, discipline, file_key "
            "FROM drawings WHERE title = :t LIMIT 1", {"t": title})
        if not row:
            print("找不到", title); continue
        vt = classify_view_type({"title": row["title"] or "",
                                 "drawing_no": row["drawing_no"] or ""})
        data = get_file_bytes(row["file_key"])
        geom = extract_pdf_geometry(data)
        tr = await db.fetch_one(
            "SELECT scale_m_pt FROM drawing_transform WHERE drawing_id=:d",
            {"d": row["id"]})
        fe = recognize(geom, row["discipline"], row["id"],
                       drawing_title=row["title"],
                       scale_override=float(tr["scale_m_pt"]) if tr else None)
        nax = (len(fe.axes.get("x") or []), len(fe.axes.get("y") or []))
        # 闸：图种 ∈ 剖面/立面 → 全删；详图且无轴网 → 全删
        gated = GATE == "view" and (
            vt.view_type in ("section", "elevation")
            or (vt.view_type == "detail" and (nax[0] < 2 or nax[1] < 2)))
        if not fe.scale or not fe.columns:
            print(f"无候选 {vt.view_type:9s} {title[:40]}"); continue
        k = DPI / 72.0
        boxes = []
        for c in fe.columns:
            o = c.get("outline") or []
            if len(o) < 3:
                continue
            px = [meters_to_page(mx, my, fe.scale, fe.origin_pt, fe.page_h)
                  for mx, my in o]
            xs = [p[0] * k for p in px]; ys = [p[1] * k for p in px]
            boxes.append((min(xs), min(ys), max(xs), max(ys)))
        print(f"{vt.view_type:9s} 轴网{nax}  候选 {len(boxes):4d} "
              f"{'删全部' if gated else '保留'}  {title[:38]}")
        doc = fitz.open(stream=data, filetype="pdf")
        page = doc[0]
        pix = page.get_pixmap(dpi=DPI)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        dr = ImageDraw.Draw(img)
        for bx0, by0, bx1, by1 in boxes:
            dr.rectangle([bx0, by0, bx1, by1],
                         outline=(220, 0, 0) if gated else (0, 160, 0), width=2)
        img.save(f"{OUT}/{n:02d}_{vt.view_type}_{'cut' if gated else 'keep'}.png")
        doc.close(); del doc, page, pix, img, geom, data
        gc.collect()
    await db.disconnect()

asyncio.run(main())
