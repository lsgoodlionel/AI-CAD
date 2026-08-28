"""比例（scale）候选 → 接触表。

比例是算量链条上**杠杆最大**的一环：比例错一倍，所有工程量错一倍，
影响远大于构件检出率。而它**从来没有独立验证过**。

实测分布就有问题：2142 张有变换记录的图里 **61% 置信度是 1.00**，
而折算比例里有 1:1790（46 张）、>1:2000（18 张），最大到约 1:4900。

**问法绕开读数字**（字符转写是已知不可靠的能力边界）：
画一条**系统认为是 8 米**的红线，让判读者拿图上的门、楼梯踏步、
车位、房间去比 —— 纯比较，不读数。

**裁剪用固定的页面比例，与 scale 无关**；只有红线长度依赖 scale。
所以比例错了，红线就会横贯整格或缩成一点 —— 错误直接可见。
"""
import asyncio, collections, os
import databases as databases_lib, fitz
from PIL import Image, ImageDraw, ImageFont
from core.config import settings
from core.model3d.gold.batch_codes import make_codes
from core.storage import get_file_bytes

BAR_M = 8.0
OUT, PROBE_DPI, CROP_DPI, GRID = "/tmp/gpt_scale", 24, 120, 3
EXCLUDE_RIGHT, EXCLUDE_BOTTOM = 0.28, 0.18   # GB/T 50001 §4 图签栏一带
PER_STRATUM = 15
os.makedirs(OUT, exist_ok=True)


def _densest_cell(page):
    pix = page.get_pixmap(dpi=PROBE_DPI)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples).convert("L")
    w, h = img.size
    best, best_ink = (0, 0), -1.0
    for gy in range(max(1, int(GRID * (1 - EXCLUDE_BOTTOM)))):
        for gx in range(max(1, int(GRID * (1 - EXCLUDE_RIGHT)))):
            cell = img.crop((gx * w // GRID, gy * h // GRID,
                             (gx + 1) * w // GRID, (gy + 1) * h // GRID))
            ink = sum(1 for p in cell.getdata() if p < 200) / max(cell.width * cell.height, 1)
            if ink > best_ink:
                best_ink, best = ink, (gx, gy)
    gx, gy = best
    r = page.rect
    cw, ch = r.width / GRID, r.height / GRID
    return fitz.Rect(r.x0 + gx * cw, r.y0 + gy * ch,
                     r.x0 + (gx + 1) * cw, r.y0 + (gy + 1) * ch)


def _stratum(scale_m_pt: float, conf: float) -> str:
    """按「置信度 × 比例是否落在常用档」分层。常用档取国标常见的
    1:20~1:500（换算 scale_m_pt 约 0.007~0.18）。"""
    common = 0.007 <= scale_m_pt <= 0.18
    if conf >= 0.99:
        return "满分置信·常用比例" if common else "满分置信·**离谱比例**"
    if conf > 0.0:
        return "中等置信"
    return "零置信"


async def main():
    db = databases_lib.Database(settings.database_url); await db.connect()
    rows = await db.fetch_all(
        "SELECT t.drawing_id, t.scale_m_pt, t.confidence, d.title, d.file_key "
        "FROM drawing_transform t JOIN drawings d ON d.id=t.drawing_id "
        "WHERE d.title IS NOT NULL")
    pool = collections.defaultdict(list)
    for r in rows:
        pool[_stratum(float(r["scale_m_pt"]), float(r["confidence"]))].append(r)
    print("分层:", {k: len(v) for k, v in sorted(pool.items())})

    import random
    random.seed(20260913)
    picks = []
    for k, v in sorted(pool.items()):
        random.shuffle(v); picks += [(k, r) for r in v[:PER_STRATUM]]
    random.shuffle(picks)
    codes = make_codes(len(picks) + 20, seed=20260913)

    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 26)
    except Exception:
        font = ImageFont.load_default()
    CELL, COLS, ROWS = 420, 4, 3
    tiles = []
    for stratum, r in picks:
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
        k = CROP_DPI / 72.0
        bar_px = BAR_M / float(r["scale_m_pt"]) * k          # 8 米在这张图上的像素长度
        d = ImageDraw.Draw(img)
        y = img.height * 0.5
        x0 = img.width * 0.08
        # 红线可能远超画幅 —— 那本身就是答案，照画，超出部分自然被裁掉
        d.line([(x0, y), (x0 + bar_px, y)], fill=(255, 0, 0), width=7)
        for xx in (x0, x0 + bar_px):
            d.line([(xx, y - 18), (xx, y + 18)], fill=(255, 0, 0), width=7)
        img.thumbnail((CELL, CELL), Image.LANCZOS)
        canvas = Image.new("RGB", (CELL, CELL), "white")
        canvas.paste(img, ((CELL - img.width) // 2, (CELL - img.height) // 2))
        tiles.append((codes.pop(), stratum, str(r["drawing_id"]),
                      float(r["scale_m_pt"]), float(r["confidence"]),
                      str(r["title"]), bar_px / max(img.width, 1), canvas))

    man, sheets = [], []
    for si in range(0, len(tiles), COLS * ROWS):
        chunk = tiles[si:si + COLS * ROWS]
        sh = Image.new("RGB", (CELL * COLS, (CELL + 36) * ROWS), "white")
        dd = ImageDraw.Draw(sh)
        for i, (tag, st, did, sc, cf, ti, frac, img) in enumerate(chunk):
            cx, cy = (i % COLS) * CELL, (i // COLS) * (CELL + 36)
            dd.text((cx + 8, cy + 6), tag, fill="black", font=font)
            sh.paste(img, (cx, cy + 36))
            dd.rectangle([cx, cy, cx + CELL - 1, cy + CELL + 35], outline="#888")
            man.append((tag, st, did, f"{sc:.6f}", f"{cf:.2f}", ti))
        p = f"{OUT}/S{si // (COLS * ROWS) + 1}.png"
        sh.save(p); sheets.append(p)
    with open(f"{OUT}/manifest.tsv", "w") as f:
        f.write("tag\tstratum\tdrawing_id\tscale_m_pt\tconfidence\ttitle\n")
        for x in man:
            f.write("\t".join(x) + "\n")
    print(f"接触表 {len(sheets)} 张 / {len(man)} 格")
    print("分层:", dict(collections.Counter(x[1] for x in man).most_common()))
    for p in sheets:
        print(" ", p)
    await db.disconnect()

asyncio.run(main())
