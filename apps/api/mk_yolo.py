"""规则引擎 vs YOLO v5 —— 分歧处谁对。

Phase C 的 M1 结论依赖「学习模型是否强过纯规则」，而 v5 训练完从没判过。
本批只判**两者分歧**的框：规则认为有柱而模型不认（rule_only）、
模型认为有柱而规则不认（model_only）。谁对，一格一格判。

**对照组构造上就干净**：两者都认的框（both）—— 若判读连这些也说不是柱，
说明看不清，当场就知道。

判据照抄 `CRITERIA.md#columns`（上一轮的教训：判据不固定，数字就不可比）。
"""
import asyncio, collections, os, random
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
    from ultralytics import YOLO
    model = YOLO(WEIGHTS)
    db = databases_lib.Database(settings.database_url); await db.connect()
    rows = await db.fetch_all(
        "SELECT d.id, d.title, d.discipline, d.file_key FROM drawings d "
        "WHERE d.title LIKE '%平面图%' AND d.discipline IN ('structure','architecture') "
        "AND d.title NOT LIKE '%详图%' AND d.title NOT LIKE '%大样%'")
    rows = list(rows); random.shuffle(rows)

    pool = collections.defaultdict(list)
    for row in rows:
        if all(len(pool[k]) >= PER_GROUP for k in ("rule_only", "model_only", "both")):
            break
        did = str(row["id"])
        try:
            geom = extract_pdf_geometry(get_file_bytes(row["file_key"]))
            tr = await db.fetch_one("SELECT scale_m_pt FROM drawing_transform "
                                    "WHERE drawing_id=:d", {"d": did})
            fe = recognize(geom, row["discipline"], did, drawing_title=row["title"],
                           scale_override=float(tr["scale_m_pt"]) if tr else None)
            page = fitz.open(stream=get_file_bytes(row["file_key"]), filetype="pdf")[0]
        except Exception:
            continue
        if not fe.scale or not fe.columns:
            continue
        k = DPI / 72.0
        # 规则框 → 像素
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
        # 取一块含规则框的 640 瓦片，两边都在同一像素坐标系里跑
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
        if img.convert("L").getextrema()[1] - img.convert("L").getextrema()[0] < 30:
            continue
        rb = [(a - x0, b - y0, c2 - x0, d2 - y0) for a, b, c2, d2 in rule
              if a >= x0 and c2 <= x0 + TILE and b >= y0 and d2 <= y0 + TILE]
        try:
            res = model.predict(img, conf=CONF, verbose=False)[0]
        except Exception:
            continue
        mb = [tuple(float(v) for v in b.xyxy[0].tolist())
              for b in res.boxes if int(b.cls[0]) == 0]
        matched_m = set()
        for r in rb:
            hit = [j for j, m in enumerate(mb) if _iou(r, m) >= IOU_SAME]
            key = "both" if hit else "rule_only"
            matched_m.update(hit)
            if len(pool[key]) < PER_GROUP:
                pool[key].append((row, img, r))
        for j, m in enumerate(mb):
            if j not in matched_m and len(pool["model_only"]) < PER_GROUP:
                pool["model_only"].append((row, img, m))
    print("分组:", {k: len(v) for k, v in pool.items()})

    picks = [(g, *e) for g, v in pool.items() for e in v]
    random.shuffle(picks)
    codes = make_codes(len(picks) + 20, seed=20260919)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 26)
    except Exception:
        font = ImageFont.load_default()
    CELL, COLS, ROWS = 420, 4, 3
    CTX = 6.0
    tiles = []
    for grp, row, img, box in picks:
        bx0, by0, bx1, by1 = box
        span = max(bx1 - bx0, by1 - by0)
        half = max(span * CTX, 90) / 2
        cx, cy = (bx0 + bx1) / 2, (by0 + by1) / 2
        ox = max(0, min(cx - half, img.width - 2 * half))
        oy = max(0, min(cy - half, img.height - 2 * half))
        crop = img.crop((int(ox), int(oy), int(ox + 2 * half), int(oy + 2 * half)))
        if crop.width < 8 or crop.height < 8:
            continue
        d = ImageDraw.Draw(crop)
        d.rectangle([bx0 - ox, by0 - oy, bx1 - ox, by1 - oy],
                    outline=(255, 0, 0), width=4)
        crop = crop.resize((CELL, CELL))
        tiles.append((codes.pop(), grp, str(row["id"]), str(row["title"]), crop))

    man, sheets = [], []
    for si in range(0, len(tiles), COLS * ROWS):
        chunk = tiles[si:si + COLS * ROWS]
        sh = Image.new("RGB", (CELL * COLS, (CELL + 36) * ROWS), "white")
        dd = ImageDraw.Draw(sh)
        for i, (tag, grp, did, ti, im) in enumerate(chunk):
            cx2, cy2 = (i % COLS) * CELL, (i // COLS) * (CELL + 36)
            dd.text((cx2 + 8, cy2 + 6), tag, fill="black", font=font)
            sh.paste(im, (cx2, cy2 + 36))
            dd.rectangle([cx2, cy2, cx2 + CELL - 1, cy2 + CELL + 35], outline="#888")
            man.append((tag, grp, did, ti.replace("\t", " ")))
        p = f"{OUT}/Y{si // (COLS * ROWS) + 1}.png"
        sh.save(p); sheets.append(p)
    with open(f"{OUT}/manifest.tsv", "w") as f:
        f.write("tag\tgroup\tdrawing_id\ttitle\n")
        for x in man:
            f.write("\t".join(x) + "\n")
    print(f"接触表 {len(sheets)} 张 / {len(man)} 格 ·",
          dict(collections.Counter(x[1] for x in man)))
    for p in sheets:
        print(" ", p)
    await db.disconnect()

asyncio.run(main())
