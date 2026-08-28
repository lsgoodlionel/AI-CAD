"""图纸 → 楼层归属候选 → 整图缩略接触表。

楼层归属是**整模型级**的属性，从来没量过。归属错了，构件再准模型也是错的
—— 而且错得看不见（3D 里只是某层多了或少了东西）。已知有一个可疑失效：
`drawing_role` 没接进楼层归属，系统原理图被塞进 F101。

判读者在「整图阅读」这个尺度上已实测 89% 可靠（drawing_usable 批），
所以问法是整图缩略 + 「这张图属于哪一层」。
"""
import asyncio, collections, json, os, random, string
import databases as databases_lib, fitz
from PIL import Image, ImageDraw, ImageFont
from core.config import settings
from core.model3d.gold.batch_codes import make_codes
from core.storage import get_file_bytes

PER_FLOOR = 3
OUT, DPI = "/tmp/gpt_floor", 44
PROJECTS = {"metro": "77777777-7777-7777-7777-777777777777",
            "sgoh": "9188e163-c684-415e-a4ec-08f208273eff"}
# 易混字符不进标识符 —— 与 GB/T 50001 §8.0.4「轴号不得用 I、O、Z」同理。
# 实测判读把 YIWX 转写成 Y1WX、L96T 转写成 L967T。
random.seed(20260908)
os.makedirs(OUT, exist_ok=True)


async def main():
    db = databases_lib.Database(settings.database_url); await db.connect()
    picks = []
    for nm, pid in PROJECTS.items():
        r = await db.fetch_one("SELECT scene FROM project_models WHERE project_id=:p "
                               "ORDER BY version DESC LIMIT 1", {"p": pid})
        if not r:
            continue
        sc = r["scene"] if isinstance(r["scene"], dict) else json.loads(r["scene"])
        for f in sc.get("floors", []):
            srcs = set()
            for k, v in (f.get("elements") or {}).items():
                if isinstance(v, list):
                    for e in v:
                        if e.get("src"):
                            srcs.add(str(e["src"]))
            srcs = sorted(srcs); random.shuffle(srcs)
            for did in srcs[:PER_FLOOR]:
                picks.append((nm, int(f.get("order", 0)), did))
    random.shuffle(picks)
    print("抽到", len(picks), "张 ·",
          dict(collections.Counter(f"{n}{o}" for n, o, _ in picks).most_common(8)))

    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 26)
    except Exception:
        font = ImageFont.load_default()
    CELL, COLS, ROWS = 420, 4, 3
    codes = set()
    while len(codes) < 300:
        codes.add("".join(random.choice(_CODE_ALPHABET) for _ in range(4)))
    codes = sorted(codes); random.shuffle(codes)

    tiles = []
    for nm, order, did in picks:
        row = await db.fetch_one("SELECT title,discipline,file_key FROM drawings "
                                 "WHERE id::text=:d", {"d": did})
        if not row:
            continue
        try:
            page = fitz.open(stream=get_file_bytes(row["file_key"]), filetype="pdf")[0]
            pix = page.get_pixmap(dpi=DPI)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        except Exception:
            continue
        if img.convert("L").getextrema()[1] - img.convert("L").getextrema()[0] < 30:
            continue
        # 整页等比缩放进方格，留白居中 —— 拉伸会毁掉图面比例
        img.thumbnail((CELL, CELL), Image.LANCZOS)
        canvas = Image.new("RGB", (CELL, CELL), "white")
        canvas.paste(img, ((CELL - img.width) // 2, (CELL - img.height) // 2))
        tiles.append((codes.pop(), nm, order, did, str(row["title"] or ""),
                      str(row["discipline"] or ""), canvas))

    man, sheets = [], []
    for si in range(0, len(tiles), COLS * ROWS):
        chunk = tiles[si:si + COLS * ROWS]
        sh = Image.new("RGB", (CELL * COLS, (CELL + 36) * ROWS), "white")
        dd = ImageDraw.Draw(sh)
        for i, (tag, nm, order, did, ti, disc, img) in enumerate(chunk):
            cx, cy = (i % COLS) * CELL, (i // COLS) * (CELL + 36)
            dd.text((cx + 8, cy + 6), tag, fill="black", font=font)
            sh.paste(img, (cx, cy + 36))
            dd.rectangle([cx, cy, cx + CELL - 1, cy + CELL + 35], outline="#888")
            man.append((tag, nm, str(order), did, ti, disc))
        p = f"{OUT}/F{si // (COLS * ROWS) + 1}.png"
        sh.save(p); sheets.append(p)
    with open(f"{OUT}/manifest.tsv", "w") as f:
        f.write("tag\tproject\tassigned_order\tdrawing_id\ttitle\tdiscipline\n")
        for x in man:
            f.write("\t".join(x) + "\n")
    print(f"接触表 {len(sheets)} 张 / {len(man)} 张图")
    for p in sheets:
        print(" ", p)
    await db.disconnect()

asyncio.run(main())
