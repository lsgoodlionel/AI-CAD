"""实体合并 → 接触表。「这些红框圈住的，是一根柱还是好几根？」

Phase H 的核心是「实体中心装配」：同一构件在多处出现，合并成一个实体。
实测这个合并**在同一张图内**就大量发生：(实体, 图纸) 对里 **37.5%**
带着 ≥2 条观测 —— 它们是一根柱的几个片段，还是几根柱被并成了一个？
从没验过。

顺带记下的判据外事实（不依赖判读者，直接查库）：
    grid_ref（声称的「统一轴号主键」） 只有 **42.58%** 有值
    section_json                      **0.00%**
    type_label（档案 OCR → 类型标签）   31 条 = 0.005%
    review_state = conflict           42113 = 6.2%

**批次自带有效性检查**：混入 N=1 的单观测实体作对照。
判读者若把它们也说成「好几根」，说明看不清，当场就知道。
"""
import asyncio, collections, json, os, random
import databases as databases_lib, fitz
from PIL import Image, ImageDraw, ImageFont
from core.config import settings
from core.model3d.element_recognizer import recognize
from core.model3d.geometry_extractor import extract_pdf_geometry
from core.model3d.gold.batch_codes import make_codes
from core.model3d.yolo_export import meters_to_page
from core.storage import get_file_bytes

PER_GROUP, DPI = 22, 150
OUT, CTX_RATIO, MIN_HALF_PT = "/tmp/gpt_merge", 2.5, 30.0
os.makedirs(OUT, exist_ok=True)
random.seed(20260916)


async def main():
    db = databases_lib.Database(settings.database_url); await db.connect()
    picks = []
    for grp, cond in (("single", "= 1"), ("two", "= 2"),
                      ("three", "= 3"), ("many", ">= 4")):
        rows = await db.fetch_all(f"""
            SELECT o.instance_id, o.drawing_id, count(*) n
            FROM component_observations o
            JOIN component_instances i ON i.id = o.instance_id
            WHERE i.type = 'column'
            GROUP BY 1, 2 HAVING count(*) {cond}
            LIMIT 400""")
        rows = list(rows); random.shuffle(rows)
        picks += [(grp, r) for r in rows[:PER_GROUP]]
    random.shuffle(picks)
    print("抽到", len(picks), dict(collections.Counter(g for g, _ in picks)))

    codes = make_codes(len(picks) + 20, seed=20260916)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 26)
    except Exception:
        font = ImageFont.load_default()
    CELL, COLS, ROWS = 420, 4, 3
    cache: dict[str, tuple] = {}
    tiles = []
    for grp, r in picks:
        did = str(r["drawing_id"])
        if did not in cache:
            row = await db.fetch_one("SELECT title,discipline,file_key FROM drawings "
                                     "WHERE id::text=:d", {"d": did})
            got = None
            if row:
                try:
                    geom = extract_pdf_geometry(get_file_bytes(row["file_key"]))
                    tr = await db.fetch_one("SELECT scale_m_pt FROM drawing_transform "
                                            "WHERE drawing_id=:d", {"d": did})
                    fe = recognize(geom, row["discipline"], did,
                                   drawing_title=row["title"],
                                   scale_override=float(tr["scale_m_pt"]) if tr else None)
                    if fe.scale:
                        page = fitz.open(stream=get_file_bytes(row["file_key"]),
                                         filetype="pdf")[0]
                        got = (row, fe, page)
                except Exception:
                    got = None
            cache[did] = got
        got = cache[did]
        if not got:
            continue
        row, fe, page = got
        obs = await db.fetch_all(
            "SELECT local_coord FROM component_observations "
            "WHERE instance_id=:i AND drawing_id=:d",
            {"i": r["instance_id"], "d": r["drawing_id"]})
        polys = []
        for o in obs:
            v = o["local_coord"]
            v = json.loads(v) if isinstance(v, str) else v
            if isinstance(v, list) and len(v) >= 3:
                polys.append(v)
        if not polys:
            continue
        xs = [p[0] for pl in polys for p in pl]; ys = [p[1] for pl in polys for p in pl]
        span = max(max(xs) - min(xs), max(ys) - min(ys))
        cxp, cyp = meters_to_page((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2,
                                  fe.scale, fe.origin_pt, fe.page_h)
        half = max(span * CTX_RATIO / fe.scale, MIN_HALF_PT) / 2
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
        ex = img.convert("L").getextrema()
        if ex[1] - ex[0] < 30:
            continue
        d, k2 = ImageDraw.Draw(img), DPI / 72.0
        for pl in polys:
            px = []
            for mx, my in pl:
                a, b = meters_to_page(mx, my, fe.scale, fe.origin_pt, fe.page_h)
                px.append(((a - clip.x0) * k2, (b - clip.y0) * k2))
            d.line(px + [px[0]], fill=(255, 0, 0), width=4)
        # **画完之后确认红色真的在画面里**：退化成零面积的多边形什么也画不出来，
        # 自验 M1 表时有 2~3 格完全看不到红色 —— 空格子送出去就是浪费一次判读。
        rgb = img.convert("RGB")
        if not any(r > 180 and g < 90 and b < 90
                   for r, g, b in rgb.getdata()):
            continue
        img.thumbnail((CELL, CELL), Image.LANCZOS)
        canvas = Image.new("RGB", (CELL, CELL), "white")
        canvas.paste(img, ((CELL - img.width) // 2, (CELL - img.height) // 2))
        tiles.append((codes.pop(), grp, len(polys), did,
                      str(row["title"] or ""), canvas))

    man, sheets = [], []
    for si in range(0, len(tiles), COLS * ROWS):
        chunk = tiles[si:si + COLS * ROWS]
        sh = Image.new("RGB", (CELL * COLS, (CELL + 36) * ROWS), "white")
        dd = ImageDraw.Draw(sh)
        for i, (tag, grp, n, did, ti, img) in enumerate(chunk):
            cx, cy = (i % COLS) * CELL, (i // COLS) * (CELL + 36)
            dd.text((cx + 8, cy + 6), tag, fill="black", font=font)
            sh.paste(img, (cx, cy + 36))
            dd.rectangle([cx, cy, cx + CELL - 1, cy + CELL + 35], outline="#888")
            man.append((tag, grp, str(n), did, ti))
        p = f"{OUT}/M{si // (COLS * ROWS) + 1}.png"
        sh.save(p); sheets.append(p)
    with open(f"{OUT}/manifest.tsv", "w") as f:
        f.write("tag\tgroup\tn_obs\tdrawing_id\ttitle\n")
        for x in man:
            f.write("\t".join(x) + "\n")
    print(f"接触表 {len(sheets)} 张 / {len(man)} 格 ·",
          dict(collections.Counter(x[1] for x in man)))
    for p in sheets:
        print(" ", p)
    await db.disconnect()

asyncio.run(main())
