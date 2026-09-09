# 运行方式（容器无挂载，需 docker cp 注入；cad_api 被多会话共用，请自建容器）：
#   docker run -d --name cad_api_recall --network cad_default \
#     -e DATABASE_URL=postgresql://cad_user:cad_pass@postgres:5432/cad_db \
#     -e MINIO_ENDPOINT=minio:9000 -e MINIO_ACCESS_KEY=cad_minio \
#     -e MINIO_SECRET_KEY=cad_minio_pass -w /app cad-api:local sleep infinity
#   docker cp core/model3d cad_api_recall:/app/core/ && docker cp <本文件> cad_api_recall:/app/
#   docker exec cad_api_recall python <本文件名>
# PyMuPDF 与 torch 不能同进程；fitz doc 用完必须 close()。
"""召回核验：把「改比例后重新出现的柱候选」逐格渲染出来 —— 里面是不是真柱，眼睛判。

对照 `probe_delta_sheet.py` 的方法：同一张图识别两遍（当前比例 vs 落库比例），
取**新增**集合渲染。项目纪律：删除量能算，误伤/漏检只能看。
"""
import asyncio, os, random
import databases as dbl, fitz
from PIL import Image, ImageDraw, ImageFont
from core.config import settings
import core.model3d.element_recognizer as er
from core.model3d.geometry_extractor import extract_pdf_geometry
from core.model3d.yolo_export import meters_to_page
from core.storage import get_file_bytes

DID = os.environ["DID"]; OUT = os.environ.get("OUT", "/tmp/recall_sheet")
N = int(os.environ.get("N", "20")); DPI = 150
os.makedirs(OUT, exist_ok=True); random.seed(20260901)


async def main():
    db = dbl.Database(settings.database_url); await db.connect()
    r = await db.fetch_one("SELECT title,discipline,file_key FROM drawings WHERE id::text=:d", {"d": DID})
    geom = extract_pdf_geometry(get_file_bytes(r["file_key"]))
    tr = await db.fetch_one("SELECT scale_m_pt FROM drawing_transform WHERE drawing_id=:d", {"d": DID})
    stored = float(tr["scale_m_pt"])
    cur = er.recognize(geom, r["discipline"], DID, drawing_title=r["title"], scale_override=stored)
    # 绕开 `_detect_scale` 的 DXF 分支：直接把落库比例当作「读到的」
    _orig = er._detect_scale
    er._detect_scale = lambda *a, **k: (stored, False)
    try:
        fixed = er.recognize(geom, r["discipline"], DID, drawing_title=r["title"], scale_override=stored)
    finally:
        er._detect_scale = _orig
    print(f"{r['title']}\n  当前 scale={cur.scale} columns={len(cur.columns)}"
          f"\n  改用落库 scale={fixed.scale} columns={len(fixed.columns)}"
          f"  密排剔除={len(getattr(fixed,'dense_arrays',[]))}")
    doc = fitz.open(stream=get_file_bytes(r["file_key"]), filetype="pdf"); page = doc[0]
    cands = [c for c in fixed.columns if len(c.get("outline") or []) >= 3]
    random.shuffle(cands); cells = []
    for c in cands[:N]:
        o = c["outline"]
        pxy = [meters_to_page(mx, my, fixed.scale, fixed.origin_pt, fixed.page_h) for mx, my in o]
        pxs = [p[0] for p in pxy]; pys = [p[1] for p in pxy]
        bb = (min(pxs), min(pys), max(pxs), max(pys))
        k2 = DPI / 72.0; span = max(bb[2]-bb[0], bb[3]-bb[1]); half = max(span*6.0, 40.0)/2
        cx, cy = (bb[0]+bb[2])/2, (bb[1]+bb[3])/2; pr = page.rect
        gx = max(pr.x0, min(cx-half, pr.x1-2*half)); gy = max(pr.y0, min(cy-half, pr.y1-2*half))
        cl = fitz.Rect(gx, gy, min(gx+2*half, pr.x1), min(gy+2*half, pr.y1))
        if cl.width < 8 or cl.height < 8: continue
        px = page.get_pixmap(dpi=DPI, clip=cl)
        im = Image.frombytes("RGB", (px.width, px.height), px.samples)
        d = ImageDraw.Draw(im)
        d.rectangle([(bb[0]-cl.x0)*k2, (bb[1]-cl.y0)*k2, (bb[2]-cl.x0)*k2, (bb[3]-cl.y0)*k2],
                    outline=(255, 0, 0), width=3)
        xs = [p[0] for p in o]; ys = [p[1] for p in o]
        cells.append((f"{max(xs)-min(xs):.2f}x{max(ys)-min(ys):.2f}m", im))
    doc.close()
    print("渲染", len(cells), "格")
    if not cells: await db.disconnect(); return
    try: font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 19)
    except Exception: font = ImageFont.load_default()
    CELL, COLS = 330, 5; rn = (len(cells)+COLS-1)//COLS
    sh = Image.new("RGB", (CELL*COLS, (CELL+30)*rn), "white"); dd = ImageDraw.Draw(sh)
    for i, (lab, im) in enumerate(cells):
        ax, ay = (i % COLS)*CELL, (i//COLS)*(CELL+30)
        dd.text((ax+6, ay+5), f"{i+1}. {lab}", fill="black", font=font)
        sh.paste(im.resize((CELL, CELL)), (ax, ay+30))
        dd.rectangle([ax, ay, ax+CELL-1, ay+CELL+29], outline="#888")
    p = f"{OUT}/recovered.png"; sh.save(p); print(" ", p)
    await db.disconnect()
asyncio.run(main())
