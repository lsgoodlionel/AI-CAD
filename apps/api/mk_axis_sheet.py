"""渲染轨道交通图纸的轴号区,供独立读号。

轴号圈注写在轴线端部（GB/T 50001 §8.0.2），所以取图幅四边的窄带放大。
"""
import asyncio, json, sys
import databases as databases_lib, fitz
from PIL import Image, ImageDraw, ImageFont
from core.config import settings
from core.storage import get_file_bytes

DID = sys.argv[1] if len(sys.argv) > 1 else "19424243"
DPI = 200
BAND = 0.13          # 边带占图幅的比例

async def main():
    db = databases_lib.Database(settings.database_url); await db.connect()
    r = await db.fetch_one(
        "SELECT d.id,d.title,d.file_key,a.axes,a.axis_count FROM drawings d "
        "JOIN axis_recognition a ON a.drawing_id=d.id "
        "WHERE d.id::text LIKE :p", {"p": DID + "%"})
    page = fitz.open(stream=get_file_bytes(r["file_key"]), filetype="pdf")[0]
    pix = page.get_pixmap(dpi=DPI)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    W, H = img.size
    b = int(min(W, H) * BAND)
    strips = {"上": (0, 0, W, b), "下": (0, H - b, W, H),
              "左": (0, 0, b, H), "右": (W - b, 0, W, H)}
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 34)
    except Exception:
        font = ImageFont.load_default()
    for name, box in strips.items():
        crop = img.crop(box)
        # 竖带转横放，读起来更容易
        if name in ("左", "右"):
            crop = crop.transpose(Image.ROTATE_90)
        scale = min(2.0, 2400 / max(crop.size))
        crop = crop.resize((int(crop.width * scale), int(crop.height * scale)),
                           Image.LANCZOS)
        BARH = 52
        out = Image.new("RGB", (crop.width, crop.height + BARH), "white")
        d = ImageDraw.Draw(out)
        d.rectangle([0, 0, out.width - 1, BARH - 1], fill="#111")
        d.text((14, 8), f"{r['title'][:26]}  —  {name}边 轴号带",
               fill="white", font=font)
        out.paste(crop, (0, BARH))
        out.save(f"/tmp/gold/axis_{DID}_{name}.png")
        print(f"  axis_{DID}_{name}.png  {out.size[0]}x{out.size[1]}")
    ax = r["axes"] if isinstance(r["axes"], list) else json.loads(r["axes"] or "[]")
    from collections import Counter
    zc = Counter((q.get("zone_index"), q.get("label_kind")) for q in ax if q.get("label"))
    print(f"\n{r['title']} | 识别 {r['axis_count']} 条")
    print(f"  按(分区,类型)分组: {dict(zc)}")
    await db.disconnect()
asyncio.run(main())
