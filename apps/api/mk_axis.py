"""轴网有无 → 整图缩略接触表。

Phase I 声称轴号识别三张图 100%，但那是**手工建真值的三张图**，
全库泛化从未测过。而它是世界坐标配准的骨架（Phase J 主线）。

实测：4107 张全部 `ready`，**其中 axis_count = 0 的有 2045 张 = 50%**。
一半的图一条轴线都没识别出来 —— 但那可能是它们本就没有轴网
（详图／表格／说明），也可能是漏检。**这正是需要真值的地方。**

**自带有效性检查**：同时抽 40 张已识别出轴线的做对照。
判读者若分不开这两组，说明仪器无效，当场就知道，不必等分析。

问法避开读轴号（字符转写不可靠）：只问**有没有成规律的轴网图案**
—— 纵横长虚点线 + 周边一圈尺寸链，这个图案在缩略尺度上仍然可见。
"""
import asyncio, collections, os, random
import databases as databases_lib, fitz
from PIL import Image, ImageDraw, ImageFont
from core.config import settings
from core.model3d.gold.batch_codes import make_codes
from core.storage import get_file_bytes

PER_GROUP, DPI = 40, 60
OUT = "/tmp/gpt_axis"
os.makedirs(OUT, exist_ok=True)
random.seed(20260915)


async def main():
    db = databases_lib.Database(settings.database_url); await db.connect()
    picks = []
    for group, cond in (("zero", "a.axis_count = 0"), ("found", "a.axis_count >= 5")):
        rows = await db.fetch_all(
            f"SELECT a.drawing_id, a.axis_count, a.circle_count, d.title, d.file_key "
            f"FROM axis_recognition a JOIN drawings d ON d.id = a.drawing_id "
            f"WHERE a.status = 'ready' AND {cond} AND d.title IS NOT NULL")
        rows = list(rows); random.shuffle(rows)
        picks += [(group, r) for r in rows[:PER_GROUP]]
    random.shuffle(picks)
    print("抽到", len(picks), "·", dict(collections.Counter(g for g, _ in picks)))

    codes = make_codes(len(picks) + 20, seed=20260915)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 26)
    except Exception:
        font = ImageFont.load_default()
    CELL, COLS, ROWS = 460, 4, 3
    tiles = []
    for group, r in picks:
        try:
            page = fitz.open(stream=get_file_bytes(r["file_key"]), filetype="pdf")[0]
            pix = page.get_pixmap(dpi=DPI)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        except Exception:
            continue
        ex = img.convert("L").getextrema()
        if ex[1] - ex[0] < 30:
            continue
        img.thumbnail((CELL, CELL), Image.LANCZOS)
        canvas = Image.new("RGB", (CELL, CELL), "white")
        canvas.paste(img, ((CELL - img.width) // 2, (CELL - img.height) // 2))
        tiles.append((codes.pop(), group, str(r["drawing_id"]),
                      int(r["axis_count"]), int(r["circle_count"]),
                      str(r["title"]), canvas))

    man, sheets = [], []
    for si in range(0, len(tiles), COLS * ROWS):
        chunk = tiles[si:si + COLS * ROWS]
        sh = Image.new("RGB", (CELL * COLS, (CELL + 36) * ROWS), "white")
        dd = ImageDraw.Draw(sh)
        for i, (tag, grp, did, ac, cc, ti, img) in enumerate(chunk):
            cx, cy = (i % COLS) * CELL, (i // COLS) * (CELL + 36)
            dd.text((cx + 8, cy + 6), tag, fill="black", font=font)
            sh.paste(img, (cx, cy + 36))
            dd.rectangle([cx, cy, cx + CELL - 1, cy + CELL + 35], outline="#888")
            man.append((tag, grp, did, str(ac), str(cc), ti))
        p = f"{OUT}/G{si // (COLS * ROWS) + 1}.png"
        sh.save(p); sheets.append(p)
    with open(f"{OUT}/manifest.tsv", "w") as f:
        f.write("tag\tgroup\tdrawing_id\taxis_count\tcircle_count\ttitle\n")
        for x in man:
            f.write("\t".join(x) + "\n")
    print(f"接触表 {len(sheets)} 张 / {len(man)} 格 ·",
          dict(collections.Counter(x[1] for x in man)))
    for p in sheets:
        print(" ", p)
    await db.disconnect()

asyncio.run(main())
