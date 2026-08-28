"""图签栏 = 图纸**右下角**（GB/T 50001 §4 标题栏位置），不靠 OCR 标签定位。

**为什么改**：先用 title_block_label 的位置定位，标签散落全图，
min/max 取窗 6 格只中 1；改中位数取窗中 2；加聚集度筛选反而回到 1。
标签位置本身不可靠 —— 而标题栏的位置是**国标规定的**。
"""
import asyncio, json, os, random, sys
import databases as databases_lib, fitz
from PIL import Image, ImageDraw, ImageFont
from core.config import settings
from core.storage import get_file_bytes

N = int(sys.argv[1]) if len(sys.argv) > 1 else 24
OUT = "/tmp/gpt_tb"
DPI = 190
RIGHT, BOTTOM = 0.26, 0.34      # 右下角窗口占图幅的比例
random.seed(20260830)
os.makedirs(OUT, exist_ok=True)

async def main():
    db = databases_lib.Database(settings.database_url); await db.connect()
    rows = []
    for pid, proj in (("9188e163-c684-415e-a4ec-08f208273eff", "sgoh"),
                      ("77777777-7777-7777-7777-777777777777", "metro")):
        rs = await db.fetch_all(
            "SELECT id, title, file_key, discipline FROM drawings "
            "WHERE project_id=:p AND file_key IS NOT NULL", {"p": pid})
        rows += [dict(r) | {"proj": proj} for r in rs]
    picked = random.sample(rows, min(N * 2, len(rows)))

    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 26)
    except Exception:
        font = ImageFont.load_default()
    CELL, COLS, ROWS = 620, 3, 2
    crops = []
    for r in picked:
        if len(crops) >= N:
            break
        try:
            page = fitz.open(stream=get_file_bytes(r["file_key"]), filetype="pdf")[0]
            w, h = page.rect.width, page.rect.height
            clip = fitz.Rect(w * (1 - RIGHT), h * (1 - BOTTOM), w, h)
            pix = page.get_pixmap(dpi=DPI, clip=clip)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        except Exception:
            continue
        # 全白的角说明这张图的标题栏不在右下（或是空页），跳过
        g = img.convert("L").resize((80, 80))
        if sum(1 for v in g.getdata() if v < 200) / 6400 < 0.02:
            continue
        crops.append((r, img))

    manifest = []
    per = COLS * ROWS
    for s in range((len(crops) + per - 1) // per):
        chunk = crops[s * per:(s + 1) * per]
        BAR = 44
        sheet = Image.new("RGB", (CELL * COLS, BAR + CELL * ROWS), "white")
        d0 = ImageDraw.Draw(sheet)
        d0.rectangle([0, 0, sheet.width - 1, BAR - 1], fill="#111")
        sid = f"T{s+1}"
        d0.text((12, 8), f"{sid}   {len(chunk)} 个图签栏区  编号 {sid}-1 ~ {sid}-{len(chunk)}",
                fill="white", font=font)
        for k, (r, img) in enumerate(chunk):
            im = img.copy(); im.thumbnail((CELL - 8, CELL - 8), Image.LANCZOS)
            canvas = Image.new("RGB", (CELL, CELL), "white")
            canvas.paste(im, ((CELL - im.width) // 2, (CELL - im.height) // 2))
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
    print(f"合计 {len(crops)} 个")
    await db.disconnect()
asyncio.run(main())
