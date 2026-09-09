"""接触表：把柱候选**按实测形状分层**渲染出来，用眼睛核对形状判据的含义。

转储探针给出的是数字（三角 1.8% / 四角 94.7%），但数字回答不了
「四角那 94.7% 究竟是什么」—— 而这正是本轮的关键：档案里平面图平均
12.4 个标高条目、每图约 45 个柱候选，比值 27% 与金标准的标高误检率吻合，
可三角形只有 1.8%。两者对不上，说明**标高符号不是以三角形进来的**。
是什么形状进来的，只能看。

分层：按角点数（3 / 4 轴对齐 / 4 非轴对齐 / 其他）各取若干，
每格标注角点数、面积比、尺寸 —— 判读结论与实测量能当场对上。
"""
import asyncio, collections, gc, os, random, time
import databases as databases_lib, fitz
from PIL import Image, ImageDraw, ImageFont
from core.config import settings
from core.model3d.element_recognizer import recognize
from core.model3d.geometry_extractor import extract_pdf_geometry
from core.model3d.yolo_export import meters_to_page
from core.storage import get_file_bytes

OUT = os.environ.get("OUT", "/tmp/col_sheet")
N_DRAWINGS = int(os.environ.get("N_DRAWINGS", "60"))
BUDGET_SEC = float(os.environ.get("BUDGET_SEC", "600"))
PER_STRATUM = int(os.environ.get("PER_STRATUM", "10"))
DPI, EPS_M, COLLINEAR_SIN = 150, 1e-3, 0.01
os.makedirs(OUT, exist_ok=True)
random.seed(20260922)


def corners(outline, eps=EPS_M):
    pts = []
    for x, y in outline:
        if not any(abs(x - px) <= eps and abs(y - py) <= eps for px, py in pts):
            pts.append((float(x), float(y)))
    if len(pts) < 3:
        return pts
    keep, n = [], len(pts)
    for i in range(n):
        ax, ay = pts[(i - 1) % n]; bx, by = pts[i]; cx, cy = pts[(i + 1) % n]
        ux, uy = bx - ax, by - ay; vx, vy = cx - bx, cy - by
        lu = (ux * ux + uy * uy) ** 0.5; lv = (vx * vx + vy * vy) ** 0.5
        if lu <= 0 or lv <= 0:
            continue
        if abs(ux * vy - uy * vx) / (lu * lv) > COLLINEAR_SIN:
            keep.append((bx, by))
    return keep if len(keep) >= 3 else pts


def shoelace(pts):
    s = 0.0
    for i in range(len(pts)):
        x0, y0 = pts[i]; x1, y1 = pts[(i + 1) % len(pts)]
        s += x0 * y1 - x1 * y0
    return abs(s) / 2


def stratum(o):
    cs = corners(o)
    if len(cs) == 3:
        return "tri3"
    if len(cs) == 4:
        xs = sorted({round(p[0] / EPS_M) for p in cs})
        ys = sorted({round(p[1] / EPS_M) for p in cs})
        return "rect4" if (len(xs) == 2 and len(ys) == 2) else "quad4"
    return "other"


async def main():
    db = databases_lib.Database(settings.database_url); await db.connect()
    rows = list(await db.fetch_all(
        "SELECT d.id,d.title,d.discipline,d.file_key FROM drawings d "
        "WHERE d.title LIKE '%平面图%' "
        "AND d.discipline IN ('structure','architecture') "
        "AND d.title NOT LIKE '%详图%' AND d.title NOT LIKE '%大样%' "
        "AND d.title NOT LIKE '%剖面%'"))
    random.shuffle(rows); rows = rows[:N_DRAWINGS]
    buckets = collections.defaultdict(list)
    deadline = time.monotonic() + BUDGET_SEC

    for row in rows:
        if time.monotonic() > deadline:
            print(f"[预算到点]", flush=True); break
        if all(len(buckets[k]) >= PER_STRATUM for k in ("tri3", "rect4", "quad4", "other")):
            break
        did = str(row["id"])
        try:
            geom = extract_pdf_geometry(get_file_bytes(row["file_key"]))
            tr = await db.fetch_one("SELECT scale_m_pt FROM drawing_transform "
                                    "WHERE drawing_id=:d", {"d": did})
            fe = recognize(geom, row["discipline"], did, drawing_title=row["title"],
                           scale_override=float(tr["scale_m_pt"]) if tr else None)
            if not fe.scale:
                continue
            doc = fitz.open(stream=get_file_bytes(row["file_key"]), filetype="pdf")
            page = doc[0]
        except Exception:
            continue

        def crop(bb):
            """按候选包络裁剪并画红框。**doc 用完立刻关**，不把 page 留到下一轮。"""
            k2 = DPI / 72.0
            span = max(bb[2] - bb[0], bb[3] - bb[1])
            half = max(span * 6.0, 40.0) / 2
            cxp, cyp = (bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2
            pr = page.rect
            gx = max(pr.x0, min(cxp - half, pr.x1 - 2 * half))
            gy = max(pr.y0, min(cyp - half, pr.y1 - 2 * half))
            cl = fitz.Rect(gx, gy, min(gx + 2 * half, pr.x1), min(gy + 2 * half, pr.y1))
            if cl.width < 8 or cl.height < 8:
                return None
            try:
                px = page.get_pixmap(dpi=DPI, clip=cl)
                im = Image.frombytes("RGB", (px.width, px.height), px.samples)
            except Exception:
                return None
            d = ImageDraw.Draw(im)
            d.rectangle([(bb[0] - cl.x0) * k2, (bb[1] - cl.y0) * k2,
                         (bb[2] - cl.x0) * k2, (bb[3] - cl.y0) * k2],
                        outline=(255, 0, 0), width=3)
            return im

        cands = [c for c in fe.columns if len(c.get("outline") or []) >= 3]
        random.shuffle(cands)
        taken = 0
        for c in cands:
            if taken >= 3:
                break
            o = c["outline"]
            st = stratum(o)
            if len(buckets[st]) >= PER_STRATUM:
                continue
            pxy = [meters_to_page(mx, my, fe.scale, fe.origin_pt, fe.page_h)
                   for mx, my in o]
            pxs = [p[0] for p in pxy]; pys = [p[1] for p in pxy]
            im = crop((min(pxs), min(pys), max(pxs), max(pys)))
            if im is None:
                continue
            xs = [p[0] for p in o]; ys = [p[1] for p in o]
            w_m, h_m = max(xs) - min(xs), max(ys) - min(ys)
            ar = shoelace(o) / (w_m * h_m) if w_m * h_m > 0 else 0
            buckets[st].append((
                f"{st} c={len(corners(o))} ar={ar:.2f}",
                f"{w_m:.2f}x{h_m:.2f}m",
                str(row["title"] or "")[:18], im))
            taken += 1
        try:
            doc.close()
        except Exception:
            pass
        gc.collect()

    print({k: len(v) for k, v in buckets.items()}, flush=True)
    try:
        font = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 20)
    except Exception:
        font = ImageFont.load_default()
    CELL, COLS = 340, 5
    for st, items in buckets.items():
        if not items:
            continue
        rows_n = (len(items) + COLS - 1) // COLS
        sh = Image.new("RGB", (CELL * COLS, (CELL + 52) * rows_n), "white")
        dd = ImageDraw.Draw(sh)
        for i, (lab, size, title, im) in enumerate(items):
            cx, cy = (i % COLS) * CELL, (i // COLS) * (CELL + 52)
            dd.text((cx + 6, cy + 4), lab, fill="black", font=font)
            dd.text((cx + 6, cy + 26), f"{size} {title}", fill="#444", font=font)
            sh.paste(im.resize((CELL, CELL)), (cx, cy + 52))
            dd.rectangle([cx, cy, cx + CELL - 1, cy + CELL + 51], outline="#888")
        p = f"{OUT}/{st}.png"
        sh.save(p); print(" ", p, flush=True)
    await db.disconnect()

asyncio.run(main())
