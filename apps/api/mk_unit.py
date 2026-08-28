"""图纸 → 单体归属候选 → 整图缩略接触表。

单体归属是楼层归属的另一半（楼层管 z，单体管 xy 分组），同样是**整模型级**
属性、同样从未量过。实测：

    metro  1798 张**全部** `main`，来源全是 `default` —— 一个单体都没认出来
    sgoh   2113 default / 196 title —— 只有 8.5% 靠图名认出（south 129 · north 67）

但 metro 是地铁车站，**可能本来就只有一个单体** —— 「全是 main」到底是
识别失败还是工程本就如此，正是需要真值的地方。而歌剧院明确有 A/B/C 区 +
大/中/小歌剧厅，却只认出 south/north 两个。

缩略图上看不出「南区」还是「北区」（那在图名里，而字符转写不可靠），
但**能看出画的是整个工程还是一角** —— 这直接检验 `main` 这个默认值。
"""
import asyncio, collections, json, os, random, string
import databases as databases_lib, fitz
from PIL import Image, ImageDraw, ImageFont
from core.config import settings
from core.model3d.gold.batch_codes import make_codes
from core.storage import get_file_bytes

PER_FLOOR = 3
OUT, DPI = "/tmp/gpt_unit", 44
PROJECTS = {"metro": "77777777-7777-7777-7777-777777777777",
            "sgoh": "9188e163-c684-415e-a4ec-08f208273eff"}
# 易混字符不进标识符 —— 与 GB/T 50001 §8.0.4「轴号不得用 I、O、Z」同理。
# 实测判读把 YIWX 转写成 Y1WX、L96T 转写成 L967T。
random.seed(20260910)
os.makedirs(OUT, exist_ok=True)


async def main():
    db = databases_lib.Database(settings.database_url); await db.connect()
    from services.model_story import detect_building_unit
    picks = []
    for nm, pid in PROJECTS.items():
        rows = await db.fetch_all(
            "SELECT id,title,drawing_no,discipline FROM drawings "
            "WHERE project_id::text=:p AND title IS NOT NULL", {"p": pid})
        pool = collections.defaultdict(list)
        for r in rows:
            d = {"id": str(r["id"]), "title": r["title"] or "",
                 "drawing_no": r["drawing_no"] or "", "discipline": r["discipline"] or ""}
            u = detect_building_unit(d, {})
            pool[u.unit_key].append(str(r["id"]))
        for key, dids in sorted(pool.items()):
            random.shuffle(dids)
            # `main` 是默认值兜底，占绝大多数，多抽一些；具名单体各抽满
            cap = 26 if key == "main" else 14
            for did in dids[:cap]:
                picks.append((nm, key, did))
    random.shuffle(picks)
    print("抽到", len(picks), "张 ·",
          dict(collections.Counter(f"{n}/{k}" for n, k, _ in picks).most_common()))

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
        p = f"{OUT}/U{si // (COLS * ROWS) + 1}.png"
        sh.save(p); sheets.append(p)
    with open(f"{OUT}/manifest.tsv", "w") as f:
        f.write("tag\tproject\tassigned_unit\tdrawing_id\ttitle\tdiscipline\n")
        for x in man:
            f.write("\t".join(x) + "\n")
    print(f"接触表 {len(sheets)} 张 / {len(man)} 张图")
    for p in sheets:
        print(" ", p)
    await db.disconnect()

asyncio.run(main())
