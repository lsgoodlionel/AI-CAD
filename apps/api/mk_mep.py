"""机电候选（设备 + 管线）→ 带上下文的小图接触表。

这两类是全库最大的未验证盲区：管线 30467 条 + 设备 10033 台（第二工程），
比柱还多，而**从来没有过任何真值**。

设备是块（画红框），管线是线（画红线），两者混在一张表里判读者要来回切语境，
所以**分开出表**：E 表只有设备，P 表只有管线。
"""
import asyncio, json, os, random, sys
import databases as databases_lib, fitz
from PIL import Image, ImageDraw, ImageFont
from core.config import settings
from core.model3d.element_recognizer import recognize
from core.model3d.geometry_extractor import extract_pdf_geometry
from core.model3d.yolo_export import meters_to_page
from core.storage import get_file_bytes

PER_KIND = 60
OUT = "/tmp/gpt_mep"
DPI, CTX_M = 150, 8.0
PROJECTS = {"metro": "77777777-7777-7777-7777-777777777777",
            "sgoh": "88888888-8888-8888-8888-888888888888"}
random.seed(20260904)
os.makedirs(OUT, exist_ok=True)


def _center(el, kind):
    if kind == "pipes":
        (ax, ay), (bx, by) = el["path"]
        return (ax + bx) / 2, (ay + by) / 2
    xs = [p[0] for p in el["outline"]]; ys = [p[1] for p in el["outline"]]
    return (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2


async def collect(db, kind, pid, want):
    r = await db.fetch_one("SELECT scene FROM project_models WHERE project_id=:p "
                           "ORDER BY version DESC LIMIT 1", {"p": pid})
    if not r:
        return []
    sc = r["scene"] if isinstance(r["scene"], dict) else json.loads(r["scene"])
    by_src = {}
    for f in sc.get("floors", []):
        for el in (f.get("elements") or {}).get(kind) or []:
            # **比例存疑的一律不取**：判读者会在错误尺度下判「这么细的不是管」
            if el.get("scale_suspect"):
                continue
            if kind == "pipes" and len(el.get("path") or []) != 2:
                continue
            if kind == "equipment" and len(el.get("outline") or []) < 3:
                continue
            by_src.setdefault(str(el.get("src")), []).append(el)
    srcs = [s for s, v in by_src.items() if len(v) >= 5]
    random.shuffle(srcs)
    picks = []
    for did in srcs:
        if len(picks) >= want:
            break
        row = await db.fetch_one("SELECT id,title,discipline,file_key FROM drawings "
                                 "WHERE id::text=:d", {"d": did})
        if not row:
            continue
        t = str(row["title"] or "")
        # 只取平面图：机电的详图是系统原理图/大样，上面的「管」是示意线不是实物
        if "平面" not in t or any(k in t for k in ("详图", "大样", "系统", "原理", "剖面")):
            continue
        try:
            geom = extract_pdf_geometry(get_file_bytes(row["file_key"]))
            tr = await db.fetch_one("SELECT scale_m_pt FROM drawing_transform "
                                    "WHERE drawing_id=:d", {"d": did})
            fe = recognize(geom, row["discipline"], did, drawing_title=row["title"],
                           scale_override=float(tr["scale_m_pt"]) if tr else None)
            page = fitz.open(stream=get_file_bytes(row["file_key"]), filetype="pdf")[0]
        except Exception:
            continue
        if not fe.scale:
            continue
        for el in random.sample(by_src[did], min(3, len(by_src[did]))):
            picks.append((row, fe, page, el))
            if len(picks) >= want:
                break
    return picks


def render(picks, kind, prefix, font):
    CELL, COLS, ROWS = 380, 5, 4
    tiles = []
    for row, fe, page, el in picks:
        k2, cxm, cym = DPI / 72.0, *_center(el, kind)
        cxp, cyp = meters_to_page(cxm, cym, fe.scale, fe.origin_pt, fe.page_h)
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
        ex = img.convert("L").getextrema()
        if ex[1] - ex[0] < 30:      # 全黑或全白的格子不送
            continue
        d = ImageDraw.Draw(img)
        def to_px(mx, my):
            px, py = meters_to_page(mx, my, fe.scale, fe.origin_pt, fe.page_h)
            return ((px - clip.x0) * k2, (py - clip.y0) * k2)
        if kind == "pipes":
            (ax, ay), (bx, by) = el["path"]
            d.line([to_px(ax, ay), to_px(bx, by)], fill=(255, 0, 0), width=4)
        else:
            pts = [to_px(*p) for p in el["outline"]]
            xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
            d.rectangle([min(xs), min(ys), max(xs), max(ys)], outline=(255, 0, 0), width=4)
        tiles.append((row, el, img.resize((CELL, CELL))))

    man, sheets = [], []
    for si in range(0, len(tiles), COLS * ROWS):
        chunk = tiles[si:si + COLS * ROWS]
        sh = Image.new("RGB", (CELL * COLS, (CELL + 34) * ROWS), "white")
        dd = ImageDraw.Draw(sh)
        for i, (row, el, img) in enumerate(chunk):
            cx, cy = (i % COLS) * CELL, (i // COLS) * (CELL + 34)
            tag = f"{prefix}{si // (COLS * ROWS) + 1}-{i + 1:02d}"
            dd.text((cx + 8, cy + 6), tag, fill="black", font=font)
            sh.paste(img, (cx, cy + 34))
            dd.rectangle([cx, cy, cx + CELL - 1, cy + CELL + 33], outline="#888")
            man.append((tag, str(row["id"]), str(row["title"] or ""),
                        str(row["discipline"] or "")))
        p = f"{OUT}/{prefix}{si // (COLS * ROWS) + 1}.png"
        sh.save(p); sheets.append(p)
    return man, sheets


async def main():
    db = databases_lib.Database(settings.database_url); await db.connect()
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 24)
    except Exception:
        font = ImageFont.load_default()
    allman, allsheets = [], []
    for kind, prefix in (("equipment", "E"), ("pipes", "P")):
        picks = []
        for nm, pid in PROJECTS.items():
            picks += await collect(db, kind, pid, PER_KIND - len(picks))
        print(f"{kind}: 候选 {len(picks)}")
        m, s = render(picks, kind, prefix, font)
        allman += [(kind,) + x for x in m]; allsheets += s
    with open(f"{OUT}/manifest.tsv", "w") as f:
        f.write("kind\ttag\tdrawing_id\ttitle\tdiscipline\n")
        for x in allman:
            f.write("\t".join(x) + "\n")
    print(f"接触表 {len(allsheets)} 张 / 候选 {len(allman)} 个")
    for p in allsheets:
        print(" ", p)
    await db.disconnect()

asyncio.run(main())
