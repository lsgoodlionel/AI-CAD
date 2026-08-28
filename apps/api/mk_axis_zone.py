"""按识别出的**分区范围**裁轴号带——上次按图幅边缘裁，切到的是图签栏。"""
import asyncio, json, sys
import databases as databases_lib, fitz
from PIL import Image, ImageDraw, ImageFont
from core.config import settings
from core.storage import get_file_bytes
from services.axis_recognition import merge_both_end_labels

DID = sys.argv[1] if len(sys.argv) > 1 else "19424243"
DPI = 220
PAD_PT = 200         # **宁可多切也不能少切**：46pt 时区3 右端的轴号圈被裁掉了，
                     # 而裁剪位置本身就是一种判据——不完整的图会得出错误结论

async def main():
    db = databases_lib.Database(settings.database_url); await db.connect()
    r = await db.fetch_one(
        "SELECT d.id,d.title,d.file_key,a.axes,a.zones,a.page_w,a.page_h "
        "FROM drawings d JOIN axis_recognition a ON a.drawing_id=d.id "
        "WHERE d.id::text LIKE :p", {"p": DID + "%"})
    page = fitz.open(stream=get_file_bytes(r["file_key"]), filetype="pdf")[0]
    pix = page.get_pixmap(dpi=DPI)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    k = DPI / 72.0
    zs = r["zones"] if isinstance(r["zones"], list) else json.loads(r["zones"] or "[]")
    ax = r["axes"] if isinstance(r["axes"], list) else json.loads(r["axes"] or "[]")
    merged = merge_both_end_labels([q for q in ax if q.get("label")])
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 30)
    except Exception:
        font = ImageFont.load_default()

    for z in zs:
        x0, y0, x1, y1 = z["extent"]
        horiz = abs(x1 - x0) >= abs(y1 - y0)
        bx = (min(x0, x1) - PAD_PT, min(y0, y1) - PAD_PT,
              max(x0, x1) + PAD_PT, max(y0, y1) + PAD_PT)
        box = tuple(int(v * k) for v in bx)
        box = (max(0, box[0]), max(0, box[1]),
               min(img.width, box[2]), min(img.height, box[3]))
        crop = img.crop(box)
        if not horiz:
            crop = crop.transpose(Image.ROTATE_90)
        scale = min(3.0, 2200 / max(crop.size))
        crop = crop.resize((max(1, int(crop.width*scale)), max(1, int(crop.height*scale))),
                           Image.LANCZOS)
        BAR = 46
        out = Image.new("RGB", (crop.width, crop.height + BAR), "white")
        d = ImageDraw.Draw(out)
        d.rectangle([0, 0, out.width-1, BAR-1], fill="#111")
        n = sum(1 for a in merged if a.get("zone_index") == z["index"])
        d.text((12, 7), f"区{z['index']}  {'横向带' if horiz else '竖向带(已转正)'}"
                        f"  识别 {n} 条", fill="white", font=font)
        out.paste(crop, (0, BAR))
        out.save(f"/tmp/gold/z{DID}_{z['index']}.png")
        labs = [a["label"] for a in merged if a.get("zone_index") == z["index"]]
        print(f"  z{DID}_{z['index']}.png {out.size[0]}x{out.size[1]} | 识别轴号: {' '.join(labs)}")
    await db.disconnect()
asyncio.run(main())
