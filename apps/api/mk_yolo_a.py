"""A 阶段：抽瓦片 + 跑规则引擎，落盘。**不导入 torch** ——
PyMuPDF 与 torch 同进程会段错误（aarch64 老毛病）。

规则引擎 vs YOLO v5 —— 分歧处谁对。

Phase C 的 M1 结论依赖「学习模型是否强过纯规则」，而 v5 训练完从没判过。
本批只判**两者分歧**的框：规则认为有柱而模型不认（rule_only）、
模型认为有柱而规则不认（model_only）。谁对，一格一格判。

**对照组构造上就干净**：两者都认的框（both）—— 若判读连这些也说不是柱，
说明看不清，当场就知道。

判据照抄 `CRITERIA.md#columns`（上一轮的教训：判据不固定，数字就不可比）。
"""
import asyncio, collections, gc, os, random
import databases as databases_lib, fitz
from PIL import Image, ImageDraw, ImageFont
from core.config import settings
from core.model3d.element_recognizer import recognize
from core.model3d.geometry_extractor import extract_pdf_geometry
from core.model3d.gold.batch_codes import make_codes
from core.model3d.yolo_export import meters_to_page
from core.storage import get_file_bytes

WEIGHTS = "/tmp/yolo_out/cad_v5/weights/best.pt"
TILE, CONF, IOU_SAME = 640, 0.25, 0.30
OUT, DPI = "/tmp/gpt_yolo", 150
PER_GROUP = 20
os.makedirs(OUT, exist_ok=True)
random.seed(20260919)


def _iou(a, b):
    ax0, ay0, ax1, ay1 = a; bx0, by0, bx1, by1 = b
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    inter = (ix1 - ix0) * (iy1 - iy0)
    ua = (ax1 - ax0) * (ay1 - ay0) + (bx1 - bx0) * (by1 - by0) - inter
    return inter / ua if ua > 0 else 0.0




async def main():
    db = databases_lib.Database(settings.database_url); await db.connect()
    rows = await db.fetch_all(
        "SELECT d.id, d.title, d.discipline, d.file_key FROM drawings d "
        "WHERE d.title LIKE '%平面图%' AND d.discipline IN ('structure','architecture') "
        "AND d.title NOT LIKE '%详图%' AND d.title NOT LIKE '%大样%'")
    rows = list(rows); random.shuffle(rows)
    os.makedirs(f"{OUT}/tiles", exist_ok=True)
    meta = []
    for row in rows:
        if len(meta) >= 45:
            break
        did = str(row["id"])
        try:
            geom = extract_pdf_geometry(get_file_bytes(row["file_key"]))
            tr = await db.fetch_one("SELECT scale_m_pt FROM drawing_transform "
                                    "WHERE drawing_id=:d", {"d": did})
            fe = recognize(geom, row["discipline"], did, drawing_title=row["title"],
                           scale_override=float(tr["scale_m_pt"]) if tr else None)
            doc = fitz.open(stream=get_file_bytes(row["file_key"]), filetype="pdf")
            page = doc[0]
        except Exception:
            continue
        if not fe.scale or not fe.columns:
            continue
        k = DPI / 72.0
        rule = []
        for c in fe.columns:
            o = c.get("outline") or []
            if len(o) < 3:
                continue
            px = [meters_to_page(mx, my, fe.scale, fe.origin_pt, fe.page_h) for mx, my in o]
            xs = [p[0] * k for p in px]; ys = [p[1] * k for p in px]
            rule.append((min(xs), min(ys), max(xs), max(ys)))
        if not rule:
            continue
        anchor = random.choice(rule)
        cx, cy = (anchor[0] + anchor[2]) / 2, (anchor[1] + anchor[3]) / 2
        pw, ph = page.rect.width * k, page.rect.height * k
        x0 = max(0, min(cx - TILE / 2, pw - TILE)); y0 = max(0, min(cy - TILE / 2, ph - TILE))
        clip = fitz.Rect(x0 / k, y0 / k, (x0 + TILE) / k, (y0 + TILE) / k)
        try:
            pix = page.get_pixmap(dpi=DPI, clip=clip)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        except Exception:
            continue
        ex = img.convert("L").getextrema()
        if ex[1] - ex[0] < 30:
            continue
        rb = [[a2 - x0, b2 - y0, c2 - x0, d2 - y0] for a2, b2, c2, d2 in rule
              if a2 >= x0 and c2 <= x0 + TILE and b2 >= y0 and d2 <= y0 + TILE]
        name = f"t{len(meta):03d}.png"
        img.save(f"{OUT}/tiles/{name}")
        # **显式释放**：不关 doc 时进程在解析几十张大图后会在 glibc 层崩
        try:
            doc.close()
        except Exception:
            pass
        gc.collect()
        meta.append({"tile": name, "drawing_id": did,
                     "title": str(row["title"] or ""), "rule": rb})
        # **每块都落盘**：进程在解析大量 PDF 后会在 glibc 层崩（aarch64 老毛病），
        # 一次性写在末尾的话，崩了就全丢。
        import json as _j
        _j.dump(meta, open(f"{OUT}/stage_a.json", "w"), ensure_ascii=False)
    print(f"A 阶段：落盘 {len(meta)} 块瓦片，规则框合计 "
          f"{sum(len(m['rule']) for m in meta)}")
    await db.disconnect()

asyncio.run(main())
