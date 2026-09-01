"""误伤核验：渲染**产品代码实际删掉的那些候选**，逐格看是不是真柱。

与 `probe_gate_sheet.py` 的区别：那个用脚本里自带的判据近似，这个跑的是
**产品路径本身** —— 同一张图识别两遍（把 `min_area_rect` 换成轴对齐口径
即复现改前行为），取差集渲染。差集就是这次改动的全部代价，看它即可。

项目教训（MAX_BANDS=40 误杀 451 张核心平面图）：删除量能算，**误伤只能看**。
"""
import asyncio, gc, os, random, time
import databases as databases_lib, fitz
from PIL import Image, ImageDraw, ImageFont
import core.model3d.element_recognizer as er
from core.config import settings
from core.model3d.geometry_extractor import extract_pdf_geometry
from core.model3d.yolo_export import meters_to_page
from core.storage import get_file_bytes

OUT = os.environ.get("OUT", "/tmp/delta_sheet")
BUDGET_SEC = float(os.environ.get("BUDGET_SEC", "700"))
PER_GROUP = int(os.environ.get("PER_GROUP", "20"))
PER_DRAWING = int(os.environ.get("PER_DRAWING", "6"))
_ids = os.environ.get("DRAWING_IDS", "")
if _ids.startswith("@"):
    _ids = open(_ids[1:]).read()
ONLY_IDS = [s.strip() for s in _ids.replace("\n", ",").split(",") if s.strip()]
DPI = 150
os.makedirs(OUT, exist_ok=True)
random.seed(20260925)
_new = er.min_area_rect


def _aabb_extent(poly):
    xs = [p[0] for p in poly]; ys = [p[1] for p in poly]
    w, h = max(xs) - min(xs), max(ys) - min(ys)
    return max(w, h), min(w, h)


def _key(c):
    """候选身份：轮廓包围盒取毫米级四舍五入 —— 同一个多边形两遍identical。"""
    o = c.get("outline") or []
    xs = [p[0] for p in o]; ys = [p[1] for p in o]
    return (round(min(xs), 3), round(min(ys), 3),
            round(max(xs), 3), round(max(ys), 3))


async def main():
    db = databases_lib.Database(settings.database_url); await db.connect()
    rows = list(await db.fetch_all(
        "SELECT id,title,discipline,file_key FROM drawings "
        "WHERE id::text = ANY(:ids) AND file_key IS NOT NULL", {"ids": ONLY_IDS}))
    random.shuffle(rows)
    cells = []
    deadline = time.monotonic() + BUDGET_SEC
    for row in rows:
        if time.monotonic() > deadline or len(cells) >= PER_GROUP:
            break
        did = str(row["id"])
        try:
            geom = extract_pdf_geometry(get_file_bytes(row["file_key"]))
            tr = await db.fetch_one("SELECT scale_m_pt FROM drawing_transform "
                                    "WHERE drawing_id=:d", {"d": did})
            sc = float(tr["scale_m_pt"]) if tr else None
            er.min_area_rect = _aabb_extent
            before = er.recognize(geom, row["discipline"], did,
                                  drawing_title=row["title"], scale_override=sc)
            er.min_area_rect = _new
            after = er.recognize(geom, row["discipline"], did,
                                 drawing_title=row["title"], scale_override=sc)
        except Exception:
            er.min_area_rect = _new
            continue
        kept = {_key(c) for c in after.columns if len(c.get("outline") or []) >= 3}
        gone = [c for c in before.columns
                if len(c.get("outline") or []) >= 3 and _key(c) not in kept]
        if not gone or not before.scale:
            continue
        try:
            doc = fitz.open(stream=get_file_bytes(row["file_key"]), filetype="pdf")
            page = doc[0]
        except Exception:
            continue
        random.shuffle(gone)
        for c in gone[:PER_DRAWING]:
            if len(cells) >= PER_GROUP:
                break
            o = c["outline"]
            pxy = [meters_to_page(mx, my, before.scale, before.origin_pt,
                                  before.page_h) for mx, my in o]
            pxs = [p[0] for p in pxy]; pys = [p[1] for p in pxy]
            bb = (min(pxs), min(pys), max(pxs), max(pys))
            k2 = DPI / 72.0
            span = max(bb[2] - bb[0], bb[3] - bb[1])
            half = max(span * 6.0, 40.0) / 2
            cxp, cyp = (bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2
            pr = page.rect
            gx = max(pr.x0, min(cxp - half, pr.x1 - 2 * half))
            gy = max(pr.y0, min(cyp - half, pr.y1 - 2 * half))
            cl = fitz.Rect(gx, gy, min(gx + 2 * half, pr.x1),
                           min(gy + 2 * half, pr.y1))
            if cl.width < 8 or cl.height < 8:
                continue
            try:
                px = page.get_pixmap(dpi=DPI, clip=cl)
                im = Image.frombytes("RGB", (px.width, px.height), px.samples)
            except Exception:
                continue
            d = ImageDraw.Draw(im)
            d.rectangle([(bb[0] - cl.x0) * k2, (bb[1] - cl.y0) * k2,
                         (bb[2] - cl.x0) * k2, (bb[3] - cl.y0) * k2],
                        outline=(255, 0, 0), width=3)
            ext = _new(o)
            xs = [p[0] for p in o]; ys = [p[1] for p in o]
            cells.append((
                f"真{ext[0]:.2f}x{ext[1]:.2f}" if ext else "退化",
                f"框{max(xs)-min(xs):.2f}x{max(ys)-min(ys):.2f}",
                str(row["title"] or "")[:16], im))
        try:
            doc.close()
        except Exception:
            pass
        gc.collect()

    print(f"删掉的候选样本 {len(cells)} 格", flush=True)
    if not cells:
        await db.disconnect(); return
    try:
        font = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 19)
    except Exception:
        font = ImageFont.load_default()
    CELL, COLS = 330, 5
    rn = (len(cells) + COLS - 1) // COLS
    sh = Image.new("RGB", (CELL * COLS, (CELL + 50) * rn), "white")
    dd = ImageDraw.Draw(sh)
    for i, (real, box, title, im) in enumerate(cells):
        cx, cy = (i % COLS) * CELL, (i // COLS) * (CELL + 50)
        dd.text((cx + 6, cy + 3), f"{i+1}. {real}", fill="black", font=font)
        dd.text((cx + 6, cy + 25), f"{box} {title}", fill="#444", font=font)
        sh.paste(im.resize((CELL, CELL)), (cx, cy + 50))
        dd.rectangle([cx, cy, cx + CELL - 1, cy + CELL + 49], outline="#888")
    p = f"{OUT}/removed.png"
    sh.save(p); print(" ", p, flush=True)
    await db.disconnect()

asyncio.run(main())
