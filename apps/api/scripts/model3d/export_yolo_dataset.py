"""导出 YOLO 训练集 + 生成带框核验图。

坐标链：scene 里的构件是**米** → 用 `drawing_transform` 反算回页面点
→ 按渲染 DPI 转像素。任何一环错了框就会偏，所以核验图是必须的。
"""
import asyncio, json, os, sys
from collections import defaultdict
import databases as databases_lib
from core.config import settings
from dependencies import DatabaseAdapter
from core.model3d.yolo_export import CLASS_NAMES, class_id, outline_to_yolo_box

DPI = 100
OUT = "/tmp/yolo_ds3"
# **只用闭合轮廓类**：管线/梁是折线，轴对齐包围盒对它们无意义——
# 核验图实测品红框（管线）横跨大片区域，框住的 99% 是别的东西。
K = ("columns", "walls", "slabs")
import re
PLAN = re.compile(r"平面")
# **按图种过滤**：核验图实测第一张就是电气防雷接地平面图，
# 结构构件训练集不该收它
BAD = re.compile(r"防雷|接地|照明|插座|弱电|消防报警|喷淋|通风|空调|给排水|电气")
VERIFY_N = int(os.environ.get("VERIFY_N", "3"))

async def main():
    raw = databases_lib.Database(settings.database_url); await raw.connect()
    db = DatabaseAdapter(raw)
    import fitz
    from PIL import Image, ImageDraw
    from core.storage import get_file_bytes
    os.makedirs(f"{OUT}/images", exist_ok=True)
    os.makedirs(f"{OUT}/labels", exist_ok=True)
    os.makedirs(f"{OUT}/verify", exist_ok=True)

    total_img = total_box = 0
    verified = 0
    for pid, tag in (("9188e163-c684-415e-a4ec-08f208273eff", "sgoh"),
                     ("77777777-7777-7777-7777-777777777777", "metro")):
        r = await db.fetch_one("SELECT scene FROM project_models WHERE project_id=$1 "
                               "ORDER BY version DESC LIMIT 1", pid)
        sc = r["scene"] if isinstance(r["scene"], dict) else json.loads(r["scene"])
        by_src = defaultdict(list)
        for f in sc.get("floors", []):
            for kind in K:
                for e in (f.get("elements") or {}).get(kind) or []:
                    if e.get("scale_suspect"): continue
                    by_src[str(e.get("src"))].append(
                        {"kind": kind, "pts": e.get("outline") or e.get("path") or []})
        tf = {str(x["drawing_id"]): dict(x) for x in await db.fetch_all(
            "SELECT t.drawing_id, t.scale_m_pt, t.origin_x, t.origin_y, t.page_h "
            "FROM drawing_transform t JOIN drawings d ON d.id=t.drawing_id "
            "WHERE d.project_id=$1", pid)}
        meta = {str(x["id"]): (x["file_key"], x["title"] or "", x["discipline"] or "")
                for x in await db.fetch_all(
                    "SELECT id, file_key, title, discipline FROM drawings "
                    "WHERE project_id=$1", pid)}
        keys = {k: v[0] for k, v in meta.items()}

        for did, elems in by_src.items():
            t = tf.get(did); fk = keys.get(did)
            _fk, title, disc = meta.get(did, ("", "", ""))
            if not t or not fk or len(elems) < 10: continue
            if not PLAN.search(title) or BAD.search(title): continue
            if disc not in ("structure", "architecture", "decoration"): continue
            try:
                page = fitz.open(stream=get_file_bytes(fk), filetype="pdf")[0]
                pix = page.get_pixmap(dpi=DPI)
            except Exception:
                continue
            # **直接调识别器**：构件坐标不走 `drawing_transform`，
            # 用那张表反算页面坐标会整体错位（实测真柱一个没框上）。
            from core.model3d.geometry_extractor import extract_pdf_geometry
            from core.model3d.element_recognizer import recognize
            from core.model3d.yolo_export import meters_to_page
            try:
                geom = extract_pdf_geometry(get_file_bytes(fk))
                fe = recognize(geom, disc, did, drawing_title=title)
            except Exception:
                continue
            sc_m = float(getattr(fe, "scale", 0) or 0)
            if sc_m <= 0: continue
            ox, oy = getattr(fe, "origin_pt", (0.0, 0.0))
            ph = float(getattr(fe, "page_h", 0) or page.rect.height)
            k = DPI / 72.0
            def to_px(p):
                xp, yp = meters_to_page(p[0], p[1], sc_m, (ox, oy), ph)
                return (xp * k, yp * k)
            elems = ([{"kind": "columns", "pts": c.get("outline") or []} for c in fe.columns]
                     + [{"kind": "walls", "pts": w.get("path") or []} for w in fe.walls]
                     + [{"kind": "slabs", "pts": s.get("outline") or []} for s in fe.slabs])
            lines = []
            boxes = []
            for el in elems:
                cid = class_id(el["kind"])
                if cid is None: continue
                px_pts = [to_px(p) for p in el["pts"]
                          if isinstance(p, (list, tuple)) and len(p) >= 2]
                box = outline_to_yolo_box(px_pts, pix.width, pix.height)
                if box is None: continue
                lines.append(f"{cid} " + " ".join(f"{v:.6f}" for v in box))
                boxes.append((cid, box))
            if len(lines) < 10: continue
            name = f"{tag}_{did[:8]}"
            pix.save(f"{OUT}/images/{name}.png")
            open(f"{OUT}/labels/{name}.txt", "w").write("\n".join(lines))
            total_img += 1; total_box += len(lines)
            if verified < VERIFY_N:
                img = Image.open(f"{OUT}/images/{name}.png").convert("RGB")
                d = ImageDraw.Draw(img)
                colors = ["#e00", "#0a0", "#00e", "#e80", "#a0a", "#0aa"]
                for cid, (cx, cy, w, h) in boxes:
                    x0 = (cx - w/2) * img.width; y0 = (cy - h/2) * img.height
                    x1 = (cx + w/2) * img.width; y1 = (cy + h/2) * img.height
                    d.rectangle([x0, y0, x1, y1], outline=colors[cid % 6], width=2)
                img.save(f"{OUT}/verify/{name}_boxes.png")
                verified += 1
    print(f"导出 {total_img} 张图 / {total_box} 个框 → {OUT}", flush=True)
    print(f"核验图 {verified} 张 → {OUT}/verify", flush=True)
    await raw.disconnect()

asyncio.run(main())
