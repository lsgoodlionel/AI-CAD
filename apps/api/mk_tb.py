"""图签栏区域 → 放大图，供独立读取比例/图号/专业。

**为什么先做比例**：比例错会让所有尺寸全错。实测某图落库 1:15、
识别 1:100，选错后尺寸判据下的柱候选从 658 塌到 3。
"""
import asyncio, json, os, random, sys
import databases as databases_lib, fitz
from PIL import Image, ImageDraw, ImageFont
from core.config import settings
from core.storage import get_file_bytes

N = int(sys.argv[1]) if len(sys.argv) > 1 else 24
OUT = "/tmp/gpt_tb"
DPI = 200
random.seed(20260829)
os.makedirs(OUT, exist_ok=True)

async def main():
    db = databases_lib.Database(settings.database_url); await db.connect()
    # **用标签位置的中位数取窗，而不是最大外接框** ——
    # 标签散落全图，min/max 会把整张图框进来（实测 6 张里只有 1 张裁对）。
    rows = await db.fetch_all("""
        SELECT a.drawing_id, d.title, d.file_key, count(*) n,
               percentile_cont(0.5) WITHIN GROUP (
                   ORDER BY (a.location_json->'bbox'->>0)::float) mx,
               percentile_cont(0.5) WITHIN GROUP (
                   ORDER BY (a.location_json->'bbox'->>1)::float) my,
               percentile_cont(0.15) WITHIN GROUP (
                   ORDER BY (a.location_json->'bbox'->>0)::float) qx,
               percentile_cont(0.85) WITHIN GROUP (
                   ORDER BY (a.location_json->'bbox'->>1)::float) qy
        FROM drawing_extracted_info a JOIN drawings d ON d.id=a.drawing_id
        WHERE a.category='title_block_label' AND a.is_active
          AND a.location_json ? 'bbox'
        GROUP BY a.drawing_id, d.title, d.file_key
        HAVING count(*) >= 8
           -- **只取标签空间上聚集的图**：散开说明定位不可靠，
           -- 裁出来多半落在图纸主体上（实测 6 格只中 2 格）
           AND (percentile_cont(0.9) WITHIN GROUP (
                    ORDER BY (a.location_json->'bbox'->>1)::float)
              - percentile_cont(0.1) WITHIN GROUP (
                    ORDER BY (a.location_json->'bbox'->>1)::float)) < 320
           AND (percentile_cont(0.9) WITHIN GROUP (
                    ORDER BY (a.location_json->'bbox'->>0)::float)
              - percentile_cont(0.1) WITHIN GROUP (
                    ORDER BY (a.location_json->'bbox'->>0)::float)) < 420
        LIMIT 400""")
    rows = [dict(r) | {"x0": r["qx"] - 40, "y0": min(r["my"], r["qy"]) - 150,
                       "x1": r["mx"] + 480, "y1": max(r["my"], r["qy"]) + 190}
            for r in rows]
    picked = random.sample(rows, min(N, len(rows)))
    print(f"有 ≥6 个带坐标标签的图 {len(rows)} 张，抽 {len(picked)} 张")

    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 26)
    except Exception:
        font = ImageFont.load_default()
    CELL, COLS = 620, 3
    manifest = []
    per = COLS * 2
    sheets = (len(picked) + per - 1) // per
    for s in range(sheets):
        chunk = picked[s * per:(s + 1) * per]
        BAR = 44
        sheet = Image.new("RGB", (CELL * COLS, BAR + CELL * 2), "white")
        d0 = ImageDraw.Draw(sheet)
        d0.rectangle([0, 0, sheet.width - 1, BAR - 1], fill="#111")
        sid = f"T{s+1}"
        d0.text((12, 8), f"{sid}   {len(chunk)} 个图签栏  编号 {sid}-1 ~ {sid}-{len(chunk)}",
                fill="white", font=font)
        for k, r in enumerate(chunk):
            try:
                page = fitz.open(stream=get_file_bytes(r["file_key"]), filetype="pdf")[0]
                pad = 20
                clip = fitz.Rect(r["x0"] - pad, r["y0"] - pad, r["x1"] + pad, r["y1"] + pad)
                pix = page.get_pixmap(dpi=DPI, clip=clip)
                img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            except Exception as exc:
                print(f"   跳过 {str(r['drawing_id'])[:8]}: {exc}"); continue
            img.thumbnail((CELL - 8, CELL - 8), Image.LANCZOS)
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
            manifest.append(f"{tag}\t{str(r['drawing_id'])}\t{r['title']}")
        sheet.save(f"{OUT}/{sid}.png")
        print(f"  {sid}.png {sheet.width}x{sheet.height}")
    open(f"{OUT}/manifest.tsv", "w").write(
        "tag\tdrawing_id\tdb_title\n" + "\n".join(manifest) + "\n")
    await db.disconnect()
asyncio.run(main())
