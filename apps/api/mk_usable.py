"""整张图缩略图 → 判「这张图能不能用于建模」。

**为什么加这一类**：梁的判读显示误检**按图纸聚集**——6 张图 100% 全错、
6 张零误检。问题不在「某些梁难认」，而在「某些图整张都认不对」。
识别的粒度应当是图纸而不是构件。

判「这是什么图、能不能用」属于**图种判别**，实测判读者在这项上 89% 正确。
"""
import asyncio, json, os, random, sys
import databases as databases_lib, fitz
from PIL import Image, ImageDraw, ImageFont
from core.config import settings
from core.storage import get_file_bytes

N = int(sys.argv[1]) if len(sys.argv) > 1 else 36
OUT = "/tmp/gpt_use"
random.seed(20260902)
os.makedirs(OUT, exist_ok=True)

async def main():
    db = databases_lib.Database(settings.database_url); await db.connect()
    rows = []
    for pid, proj in (("9188e163-c684-415e-a4ec-08f208273eff", "sgoh"),
                      ("77777777-7777-7777-7777-777777777777", "metro")):
        rs = await db.fetch_all(
            "SELECT id, title, discipline, file_key FROM drawings "
            "WHERE project_id=:p AND file_key IS NOT NULL", {"p": pid})
        rows += [dict(r) | {"proj": proj} for r in rs]
    random.shuffle(rows)

    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 26)
    except Exception:
        font = ImageFont.load_default()
    CELL, COLS, ROWS = 620, 3, 2
    tiles = []
    for r in rows:
        if len(tiles) >= N:
            break
        try:
            page = fitz.open(stream=get_file_bytes(r["file_key"]), filetype="pdf")[0]
            pix = page.get_pixmap(dpi=42)          # 整张图缩到能看清布局即可
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        except Exception:
            continue
        g = img.convert("L").resize((64, 64))
        if sum(1 for v in g.getdata() if v < 200) / 4096 < 0.01:
            continue
        img.thumbnail((CELL - 8, CELL - 8), Image.LANCZOS)
        tiles.append((img, r))

    manifest = []
    per = COLS * ROWS
    for si in range((len(tiles) + per - 1) // per):
        chunk = tiles[si * per:(si + 1) * per]
        BAR = 44
        rn = (len(chunk) + COLS - 1) // COLS
        sheet = Image.new("RGB", (CELL * COLS, BAR + CELL * rn), "white")
        d0 = ImageDraw.Draw(sheet)
        d0.rectangle([0, 0, sheet.width - 1, BAR - 1], fill="#111")
        sid = f"U{si+1}"
        d0.text((12, 8), f"{sid}   {len(chunk)} 张整图  编号 {sid}-1 ~ {sid}-{len(chunk)}",
                fill="white", font=font)
        for k, (img, r) in enumerate(chunk):
            canvas = Image.new("RGB", (CELL, CELL), "white")
            canvas.paste(img, ((CELL - img.width) // 2, (CELL - img.height) // 2))
            dd = ImageDraw.Draw(canvas)
            tag = f"{sid}-{k+1}"
            dd.rectangle([0, 0, 92, 30], fill=(255, 0, 0))
            dd.text((6, 2), tag, fill="white", font=font)
            px, py = (k % COLS) * CELL, BAR + (k // COLS) * CELL
            sheet.paste(canvas, (px, py))
            d0.rectangle([px, py, px + CELL - 1, py + CELL - 1],
                         outline=(90, 90, 90), width=2)
            manifest.append(f"{tag}\t{r['id']}\t{r['proj']}\t{r['discipline']}\t{r['title']}")
        sheet.save(f"{OUT}/{sid}.png")
        print(f"  {sid}.png  {len(chunk)} 格")
    open(f"{OUT}/manifest.tsv", "w").write(
        "tag\tdrawing_id\tproject\tdiscipline\tdb_title\n" + "\n".join(manifest) + "\n")
    print(f"合计 {len(tiles)} 张")
    await db.disconnect()
asyncio.run(main())
