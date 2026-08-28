"""板候选 → 接触表。板是**唯一从未验证过**的规则类。

算量侧早已报过「板混凝土量的 84% 来自兜底，而算量看不见」。实测兜底板
（`column_envelope` / `axis_envelope`）数量只占 5.7%，却是那几块巨型的
（包络面积最大 32933 m²，一整个车站的footprint）—— 体量全在它们身上。

**兜底板判读不了**：实测 52/53 块的 `src` 是合成标记（`columns-envelope`、
`piles-envelope`）而非图纸 id —— 包络由整层所有柱合成，不来自任何一张图，
所以图上无处可看。这是它们的本性，不是渲染缺陷。本批只覆盖能渲染的两层
（`layer` / `largest_polygon`），兜底层作为硬边界如实记录。

板是大面积对象，判读者在这个尺度上已被证明可靠。
"""
import asyncio, collections, json, os, random, string
import databases as databases_lib, fitz
from PIL import Image, ImageDraw, ImageFont
from core.config import settings
from core.model3d.gold.batch_codes import make_codes
from core.model3d.element_recognizer import recognize
from core.model3d.geometry_extractor import extract_pdf_geometry
from core.model3d.yolo_export import meters_to_page
from core.storage import get_file_bytes

PER_BASIS = 25
OUT, DPI = "/tmp/gpt_slab", 170
CTX_RATIO, MIN_SPAN_M = 1.6, 6.0   # 上下文 = 板自身尺寸 ×1.6，让整块板加周边都进画面
PROJECTS = {"metro": "77777777-7777-7777-7777-777777777777",
            "sgoh": "9188e163-c684-415e-a4ec-08f208273eff"}
FALLBACK = ("column_envelope", "axis_envelope")

random.seed(20260907)
os.makedirs(OUT, exist_ok=True)


async def main():
    db = databases_lib.Database(settings.database_url); await db.connect()
    pool = collections.defaultdict(list)
    for nm, pid in PROJECTS.items():
        r = await db.fetch_one("SELECT scene FROM project_models WHERE project_id=:p "
                               "ORDER BY version DESC LIMIT 1", {"p": pid})
        if not r:
            continue
        sc = r["scene"] if isinstance(r["scene"], dict) else json.loads(r["scene"])
        for f in sc.get("floors", []):
            for s in (f.get("elements") or {}).get("slabs") or []:
                # 比例存疑的不取：米制坐标本身就错，画出来的红框会落在别处，
                # 那测的是比例不是识别（与墙/梁/机电几批同一条判据）
                if s.get("scale_suspect") or len(s.get("outline") or []) < 3:
                    continue
                b = str(s.get("basis") or "?")
                pool["fallback" if b in FALLBACK else b].append((nm, s))
    print("可用板（比例可信）：", {k: len(v) for k, v in sorted(pool.items())})

    picks = []
    for b, items in sorted(pool.items()):
        random.shuffle(items)
        picks += [(b, nm, s) for nm, s in items[:PER_BASIS]]
    random.shuffle(picks)

    # 每张图只解析一次
    cache: dict[str, tuple] = {}
    async def prep(did):
        if did in cache:
            return cache[did]
        row = await db.fetch_one("SELECT title,discipline,file_key FROM drawings "
                                 "WHERE id::text=:d", {"d": did})
        out = None
        if row:
            try:
                geom = extract_pdf_geometry(get_file_bytes(row["file_key"]))
                tr = await db.fetch_one("SELECT scale_m_pt FROM drawing_transform "
                                        "WHERE drawing_id=:d", {"d": did})
                fe = recognize(geom, row["discipline"], did, drawing_title=row["title"],
                               scale_override=float(tr["scale_m_pt"]) if tr else None)
                if fe.scale:
                    page = fitz.open(stream=get_file_bytes(row["file_key"]),
                                     filetype="pdf")[0]
                    out = (row, fe, page)
            except Exception:
                out = None
        cache[did] = out
        return out

    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 26)
    except Exception:
        font = ImageFont.load_default()
    CELL, COLS, ROWS = 400, 5, 4
    codes = set()
    while len(codes) < 300:
        codes.add("".join(random.choice(_CODE_ALPHABET) for _ in range(4)))
    codes = sorted(codes); random.shuffle(codes)

    tiles = []
    for basis, nm, s in picks:
        got = await prep(str(s.get("src")))
        if not got:
            continue
        row, fe, page = got
        out = s["outline"]
        xs = [p[0] for p in out]; ys = [p[1] for p in out]
        span_m = max(max(xs) - min(xs), max(ys) - min(ys), MIN_SPAN_M)
        cx_m, cy_m = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
        cxp, cyp = meters_to_page(cx_m, cy_m, fe.scale, fe.origin_pt, fe.page_h)
        half = span_m * CTX_RATIO / fe.scale / 2
        pr = page.rect
        ox = max(pr.x0, min(cxp - half, pr.x1 - 2 * half))
        oy = max(pr.y0, min(cyp - half, pr.y1 - 2 * half))
        clip = fitz.Rect(ox, oy, min(ox + 2 * half, pr.x1), min(oy + 2 * half, pr.y1))
        if clip.width < 8 or clip.height < 8:
            continue
        try:
            pix = page.get_pixmap(dpi=DPI, clip=clip)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        except Exception:
            continue
        if img.convert("L").getextrema()[1] - img.convert("L").getextrema()[0] < 30:
            continue
        d, k2 = ImageDraw.Draw(img), DPI / 72.0
        px = []
        for mx, my in out:
            a, b2 = meters_to_page(mx, my, fe.scale, fe.origin_pt, fe.page_h)
            px.append(((a - clip.x0) * k2, (b2 - clip.y0) * k2))
        d.line(px + [px[0]], fill=(255, 0, 0), width=6)
        tiles.append((codes.pop(), basis, nm, row, s, img.resize((CELL, CELL))))

    man, sheets = [], []
    for si in range(0, len(tiles), COLS * ROWS):
        chunk = tiles[si:si + COLS * ROWS]
        sh = Image.new("RGB", (CELL * COLS, (CELL + 36) * ROWS), "white")
        dd = ImageDraw.Draw(sh)
        for i, (tag, basis, nm, row, s, img) in enumerate(chunk):
            cx, cy = (i % COLS) * CELL, (i // COLS) * (CELL + 36)
            dd.text((cx + 8, cy + 6), tag, fill="black", font=font)
            sh.paste(img, (cx, cy + 36))
            dd.rectangle([cx, cy, cx + CELL - 1, cy + CELL + 35], outline="#888")
            o = s["outline"]
            xs = [p[0] for p in o]; ys = [p[1] for p in o]
            man.append((tag, basis, nm, str(s.get("src")), str(row["title"] or ""),
                        f"{(max(xs)-min(xs))*(max(ys)-min(ys)):.0f}",
                        str(s.get("thickness"))))
        p = f"{OUT}/S{si // (COLS * ROWS) + 1}.png"
        sh.save(p); sheets.append(p)
    with open(f"{OUT}/manifest.tsv", "w") as f:
        f.write("tag\tbasis\tproject\tdrawing_id\ttitle\tbbox_m2\tthickness\n")
        for x in man:
            f.write("\t".join(x) + "\n")
    print(f"接触表 {len(sheets)} 张 / 候选 {len(man)} 块")
    print("分层：", dict(collections.Counter(x[1] for x in man).most_common()))
    for p in sheets:
        print(" ", p)
    await db.disconnect()

asyncio.run(main())
