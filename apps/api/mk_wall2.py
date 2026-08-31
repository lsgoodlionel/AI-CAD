"""墙候选（第二版）—— 判据逐字沿用第一版，只把抽样源换成**当前代码**。

墙的 0.70 是所有构件类里最好的数字，**而它是在 `CRITERIA.md` 建立之前测的**，
且抽样取自存量场景。柱的数字在不同判据下摆动过 0.59 → 0.22 → 68% → 22%，
所以「最好的那个数字能不能扛住固定判据 + 当前代码」值得测。

**判据逐字不变**（从第一版 BATCH_WALL.txt 抄，已固化进 CRITERIA.md#walls），
只改抽样源 —— 差异就能归因于抽样源，又是一次单变量对比。

**对照组构造上就干净**：在**墨迹稀疏处**画同样的红线。判读者若对空白也答
「墙」，仪器当场作废，不必等分析。
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

WANT, N_BLANK, DPI = 40, 12, 150
OUT, CTX_M = "/tmp/gpt_wall2", 6.0
os.makedirs(OUT, exist_ok=True)
random.seed(20260920)


async def main():
    db = databases_lib.Database(settings.database_url); await db.connect()
    rows = await db.fetch_all(
        "SELECT d.id,d.title,d.discipline,d.file_key FROM drawings d "
        "WHERE d.title LIKE '%平面图%' AND d.discipline IN ('structure','architecture') "
        "AND d.title NOT LIKE '%详图%' AND d.title NOT LIKE '%大样%' "
        "AND d.title NOT LIKE '%剖面%'")
    rows = list(rows); random.shuffle(rows)
    picks, blanks = [], []
    for row in rows:
        if len(picks) >= WANT and len(blanks) >= N_BLANK:
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
        ws = [w for w in fe.walls if len(w.get("path") or []) == 2]
        if fe.scale and ws and len(picks) < WANT:
            for w in random.sample(ws, min(3, len(ws))):
                picks.append((row, fe, page, w["path"], doc))
                if len(picks) >= WANT:
                    break
        elif fe.scale and len(blanks) < N_BLANK:
            # 对照：页面上找一处墨迹稀疏的地方，画同样长度的红线
            pr = page.rect
            for _ in range(12):
                cx = random.uniform(pr.x0 + 0.15 * pr.width, pr.x0 + 0.7 * pr.width)
                cy = random.uniform(pr.y0 + 0.15 * pr.height, pr.y0 + 0.7 * pr.height)
                half = CTX_M / fe.scale / 2
                clip = fitz.Rect(cx - half, cy - half, cx + half, cy + half)
                try:
                    pix = page.get_pixmap(dpi=60, clip=clip)
                    im = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                except Exception:
                    continue
                ink = sum(1 for p in im.convert("L").getdata() if p < 200)
                if 0 < ink / max(im.width * im.height, 1) < 0.02:   # 稀疏但非全白
                    L = 2.0 / fe.scale
                    blanks.append((row, fe, page,
                                   [[cx - L / 2, cy], [cx + L / 2, cy]], doc, True))
                    break
        gc.collect()
    print(f"墙候选 {len(picks)} · 空白对照 {len(blanks)}")

    allp = [(*p, False) if len(p) == 5 else p for p in picks] + blanks
    random.shuffle(allp)
    codes = make_codes(len(allp) + 20, seed=20260920)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 26)
    except Exception:
        font = ImageFont.load_default()
    CELL, COLS, ROWS = 400, 5, 4
    tiles = []
    for row, fe, page, path, doc, is_blank in allp:
        k2 = DPI / 72.0
        if is_blank:
            pa, pb = path[0], path[1]
        else:
            (ax, ay), (bx, by) = path
            pa = meters_to_page(ax, ay, fe.scale, fe.origin_pt, fe.page_h)
            pb = meters_to_page(bx, by, fe.scale, fe.origin_pt, fe.page_h)
        cxp, cyp = (pa[0] + pb[0]) / 2, (pa[1] + pb[1]) / 2
        half = CTX_M / fe.scale / 2
        pr = page.rect
        x0 = max(pr.x0, min(cxp - half, pr.x1 - 2 * half))
        y0 = max(pr.y0, min(cyp - half, pr.y1 - 2 * half))
        clip = fitz.Rect(x0, y0, min(x0 + 2 * half, pr.x1), min(y0 + 2 * half, pr.y1))
        if clip.width < 8 or clip.height < 8:
            continue
        try:
            pix = page.get_pixmap(dpi=DPI, clip=clip)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        except Exception:
            continue
        d = ImageDraw.Draw(img)
        d.line([((pa[0] - clip.x0) * k2, (pa[1] - clip.y0) * k2),
                ((pb[0] - clip.x0) * k2, (pb[1] - clip.y0) * k2)],
               fill=(255, 0, 0), width=4)
        tiles.append((codes.pop(), "blank" if is_blank else "wall",
                      str(row["id"]), str(row["title"] or ""), img.resize((CELL, CELL))))

    man, sheets = [], []
    for si in range(0, len(tiles), COLS * ROWS):
        chunk = tiles[si:si + COLS * ROWS]
        sh = Image.new("RGB", (CELL * COLS, (CELL + 34) * ROWS), "white")
        dd = ImageDraw.Draw(sh)
        for i, (tag, grp, did, ti, im) in enumerate(chunk):
            cx, cy = (i % COLS) * CELL, (i // COLS) * (CELL + 34)
            dd.text((cx + 8, cy + 6), tag, fill="black", font=font)
            sh.paste(im, (cx, cy + 34))
            dd.rectangle([cx, cy, cx + CELL - 1, cy + CELL + 33], outline="#888")
            man.append((tag, grp, did, ti.replace("\t", " ")))
        p = f"{OUT}/V{si // (COLS * ROWS) + 1}.png"
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
