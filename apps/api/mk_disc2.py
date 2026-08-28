"""专业判别（第二版）—— 同样 90 张图，只改渲染尺度。

第一版用 DPI 44 的整图缩略，测出「一致率 21%」，其中最刺眼的是
「系统说 mep 的 20 张判读只有 1 张是 mep」。查图名发现 **19 张里 16 张
明写着机电关键词** —— 系统是对的，是判读者看不见。

原因：**机电平面图画在建筑底图上**，管线与末端符号的线宽在缩略尺度上直接消失。
前几批问的是「这是什么图 / 该不该有层」，答案写在**轮廓**里；
专业写在**细线**里（管线、配筋、铺装分格）。

**渲染分辨率要匹配问题所在的尺度。**

本版：把页面按 3×3 分格，取**墨迹最密的一格**按高 DPI 裁出。
图纸不变、问题不变、判据不变（CRITERIA.md v2），只有尺度变了 ——
任何差异都能归因于分辨率。
"""
import asyncio, collections, os
import databases as databases_lib, fitz
from PIL import Image, ImageDraw, ImageFont
from core.config import settings
from core.model3d.gold.batch_codes import make_codes
from core.storage import get_file_bytes

SRC_MANIFEST = "/tmp/gpt_disc/manifest.tsv"
OUT = "/tmp/gpt_disc2"
PROBE_DPI, CROP_DPI = 24, 130
GRID = 4
# **图签栏一带排除在外**：GB/T 50001 §4 规定标题栏在图幅右侧／右下角，
# 竣工图章也贴在那附近。它们是红框加密集文字，**墨迹密度全页最高**——
# 第一版直接按密度选格，90 格里有一大片选中的是图签栏和竣工图章，
# 那里不含任何专业信息。实测该缺陷在自验时被看出来，没有发出去。
EXCLUDE_RIGHT = 0.28   # 右侧 28% 不参与选格
EXCLUDE_BOTTOM = 0.18  # 下方 18% 不参与选格
os.makedirs(OUT, exist_ok=True)


def _densest_cell(page) -> fitz.Rect:
    """在**排除图签栏之后**的区域里，取墨迹最密的一格。"""
    pix = page.get_pixmap(dpi=PROBE_DPI)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples).convert("L")
    w, h = img.size
    best, best_ink = None, -1.0
    max_gx = max(1, int(GRID * (1.0 - EXCLUDE_RIGHT)))
    max_gy = max(1, int(GRID * (1.0 - EXCLUDE_BOTTOM)))
    for gy in range(max_gy):
        for gx in range(max_gx):
            box = (gx * w // GRID, gy * h // GRID,
                   (gx + 1) * w // GRID, (gy + 1) * h // GRID)
            cell = img.crop(box)
            ink = sum(1 for p in cell.getdata() if p < 200) / max(cell.width * cell.height, 1)
            if ink > best_ink:
                best_ink, best = ink, (gx, gy)
    gx, gy = best
    r = page.rect
    cw, ch = r.width / GRID, r.height / GRID
    return fitz.Rect(r.x0 + gx * cw, r.y0 + gy * ch,
                     r.x0 + (gx + 1) * cw, r.y0 + (gy + 1) * ch)


async def main():
    db = databases_lib.Database(settings.database_url); await db.connect()
    rows = [l.split("\t") for l in open(SRC_MANIFEST).read().splitlines()[1:]]
    print(f"沿用第一版的 {len(rows)} 张图，只改渲染尺度")
    codes = make_codes(len(rows) + 20, seed=20260912)

    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 26)
    except Exception:
        font = ImageFont.load_default()
    CELL, COLS, ROWS = 420, 4, 3
    tiles = []
    for tag0, proj, sysdisc, did, title, dbd in rows:
        r = await db.fetch_one("SELECT file_key FROM drawings WHERE id::text=:d",
                               {"d": did})
        if not r:
            continue
        try:
            page = fitz.open(stream=get_file_bytes(r["file_key"]), filetype="pdf")[0]
            clip = _densest_cell(page)
            pix = page.get_pixmap(dpi=CROP_DPI, clip=clip)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        except Exception:
            continue
        ex = img.convert("L").getextrema()
        if ex[1] - ex[0] < 30:
            continue
        img.thumbnail((CELL, CELL), Image.LANCZOS)
        canvas = Image.new("RGB", (CELL, CELL), "white")
        canvas.paste(img, ((CELL - img.width) // 2, (CELL - img.height) // 2))
        tiles.append((codes.pop(), proj, sysdisc, did, title, canvas))

    man, sheets = [], []
    for si in range(0, len(tiles), COLS * ROWS):
        chunk = tiles[si:si + COLS * ROWS]
        sh = Image.new("RGB", (CELL * COLS, (CELL + 36) * ROWS), "white")
        dd = ImageDraw.Draw(sh)
        for i, (tag, proj, sysdisc, did, title, img) in enumerate(chunk):
            cx, cy = (i % COLS) * CELL, (i // COLS) * (CELL + 36)
            dd.text((cx + 8, cy + 6), tag, fill="black", font=font)
            sh.paste(img, (cx, cy + 36))
            dd.rectangle([cx, cy, cx + CELL - 1, cy + CELL + 35], outline="#888")
            man.append((tag, proj, sysdisc, did, title))
        p = f"{OUT}/Z{si // (COLS * ROWS) + 1}.png"
        sh.save(p); sheets.append(p)
    with open(f"{OUT}/manifest.tsv", "w") as f:
        f.write("tag\tproject\tassigned_disc\tdrawing_id\ttitle\n")
        for x in man:
            f.write("\t".join(x) + "\n")
    print(f"接触表 {len(sheets)} 张 / {len(man)} 格")
    print("分层:", dict(collections.Counter(x[2] for x in man).most_common()))
    for p in sheets:
        print(" ", p)
    await db.disconnect()

asyncio.run(main())
