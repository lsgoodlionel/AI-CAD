"""档案「标高」条目 → 接触表。

**为什么是这一类**：全库 4109 张图里，图名含「剖面」的只有 35 张 = 0.85%，
排掉土方／基坑／地质等只剩 **9 张 = 0.22%**。跨视图 z 恢复完全依赖剖面图，
而 `model_story_levels` 与 `model_z_recovery_levels` **都是 0 行** —— 对得上。
实测层高序列也几乎全部落在常量 `DEFAULT_STORY_HEIGHT_M = 4.5` /
`DEFAULT_BASEMENT_HEIGHT_M = 4.2` 上，`elevation_source` 以人工录入为主。

**所以 z 方向仅存的自动来源，就是平面图上的标高标注** —— 档案里
`category='elevation'` 共 **24816 条**。它准不准，从没验过；
而 `level_name` 的样例已经可疑（`B22`、`B25`、`B00`、`B32` 像轴号不像层名）。

**对照组构造上就干净**：混入 `room_name` 条目 —— 房间名不可能是标高标注。
（上一批对照组取自 component_instances、本身 32% 不是柱，两组都吵、分不开，
不重蹈。）

判据固定在 `CRITERIA.md`，命令里照抄。
"""
import asyncio, collections, json, os, random
import databases as databases_lib, fitz
from PIL import Image, ImageDraw, ImageFont
from core.config import settings
from core.model3d.gold.batch_codes import make_codes
from core.storage import get_file_bytes

OUT, DPI = "/tmp/gpt_elev", 200
CTX_RATIO, MIN_HALF_PT = 5.0, 26.0
os.makedirs(OUT, exist_ok=True)
random.seed(20260918)


async def main():
    db = databases_lib.Database(settings.database_url); await db.connect()
    picks = []
    for grp, cat, cap in (("elevation", "elevation", 40),
                          ("room_control", "room_name", 16)):
        rows = await db.fetch_all(
            "SELECT e.drawing_id, e.content, e.location_json, e.confidence, "
            "       d.title, d.file_key "
            "FROM drawing_extracted_info e JOIN drawings d ON d.id = e.drawing_id "
            "WHERE e.is_active AND e.category = :c AND e.location_json IS NOT NULL "
            "LIMIT 3000", {"c": cat})
        rows = list(rows); random.shuffle(rows)
        picks += [(grp, r) for r in rows[:cap]]
    random.shuffle(picks)
    print("抽到", len(picks), dict(collections.Counter(g for g, _ in picks)))

    codes = make_codes(len(picks) + 20, seed=20260918)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 26)
    except Exception:
        font = ImageFont.load_default()
    CELL, COLS, ROWS = 420, 4, 3
    cache: dict[str, object] = {}
    tiles = []
    for grp, r in picks:
        did = str(r["drawing_id"])
        if did not in cache:
            try:
                cache[did] = fitz.open(stream=get_file_bytes(r["file_key"]),
                                       filetype="pdf")[0]
            except Exception:
                cache[did] = None
        page = cache[did]
        if page is None:
            continue
        loc = r["location_json"]
        loc = json.loads(loc) if isinstance(loc, str) else loc
        bb = (loc or {}).get("bbox")
        if not bb or len(bb) != 4:
            continue
        x0, y0, x1, y1 = [float(v) for v in bb]
        w, h = abs(x1 - x0), abs(y1 - y0)
        half = max(max(w, h) * CTX_RATIO, MIN_HALF_PT) / 2
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        pr = page.rect
        ox = max(pr.x0, min(cx - half, pr.x1 - 2 * half))
        oy = max(pr.y0, min(cy - half, pr.y1 - 2 * half))
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
        k = DPI / 72.0
        d = ImageDraw.Draw(img)
        d.rectangle([(min(x0, x1) - clip.x0) * k, (min(y0, y1) - clip.y0) * k,
                     (max(x0, x1) - clip.x0) * k, (max(y0, y1) - clip.y0) * k],
                    outline=(255, 0, 0), width=4)
        img.thumbnail((CELL, CELL), Image.LANCZOS)
        canvas = Image.new("RGB", (CELL, CELL), "white")
        canvas.paste(img, ((CELL - img.width) // 2, (CELL - img.height) // 2))
        tiles.append((codes.pop(), grp, did, str(r["content"])[:40],
                      f"{float(r['confidence'] or 0):.2f}", str(r["title"]), canvas))

    man, sheets = [], []
    for si in range(0, len(tiles), COLS * ROWS):
        chunk = tiles[si:si + COLS * ROWS]
        sh = Image.new("RGB", (CELL * COLS, (CELL + 36) * ROWS), "white")
        dd = ImageDraw.Draw(sh)
        for i, (tag, grp, did, content, conf, ti, img) in enumerate(chunk):
            cx2, cy2 = (i % COLS) * CELL, (i // COLS) * (CELL + 36)
            dd.text((cx2 + 8, cy2 + 6), tag, fill="black", font=font)
            sh.paste(img, (cx2, cy2 + 36))
            dd.rectangle([cx2, cy2, cx2 + CELL - 1, cy2 + CELL + 35], outline="#888")
            man.append((tag, grp, did, conf, content.replace("\t", " "),
                        ti.replace("\t", " ")))
        p = f"{OUT}/E{si // (COLS * ROWS) + 1}.png"
        sh.save(p); sheets.append(p)
    with open(f"{OUT}/manifest.tsv", "w") as f:
        f.write("tag\tgroup\tdrawing_id\tconfidence\tcontent\ttitle\n")
        for x in man:
            f.write("\t".join(x) + "\n")
    print(f"接触表 {len(sheets)} 张 / {len(man)} 格 ·",
          dict(collections.Counter(x[1] for x in man)))
    for p in sheets:
        print(" ", p)
    await db.disconnect()

asyncio.run(main())
