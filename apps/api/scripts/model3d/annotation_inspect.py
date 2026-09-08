"""把**一张图**的全部柱候选逐个裁出来，红框叠上判据结论，供逐格看图核验。

回测接触表回答的是「删对了没有」，这个脚本回答的是「为什么这张图一个也没删」——
金标准复跑（`annotation_gold_replay.py`）出现了与预期相反的结果时，
下一步只能是**去看图**，不能靠推断。

每格标注：序号 · 判据结论（KEEP/reason）· 真实范围长×短（米）。

用法（容器内）：
    python scripts/model3d/annotation_inspect.py <drawing_id> [输出目录] [最多格数]
"""
from __future__ import annotations

import asyncio
import os
import sys

import databases as databases_lib
import fitz
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.getcwd())

from core.config import settings  # noqa: E402
from core.model3d.annotation_filter import find_annotation_flags, min_area_rect  # noqa: E402
from core.model3d.element_recognizer import recognize  # noqa: E402
from core.model3d.geometry_extractor import extract_pdf_geometry  # noqa: E402
from core.model3d.yolo_export import meters_to_page  # noqa: E402
from core.storage import get_file_bytes  # noqa: E402

DRAWING_ID = sys.argv[1]
OUT = sys.argv[2] if len(sys.argv) > 2 else "/tmp/annot_inspect"
LIMIT = int(sys.argv[3]) if len(sys.argv) > 3 else 20
DPI, CELL, COLS = 150, 400, 5
os.makedirs(OUT, exist_ok=True)


async def main() -> None:
    db = databases_lib.Database(settings.database_url)
    await db.connect()
    row = await db.fetch_one(
        "SELECT id,title,discipline,file_key FROM drawings WHERE id=:d",
        {"d": DRAWING_ID})
    if row is None:
        print("没有这张图")
        return
    geom = extract_pdf_geometry(get_file_bytes(row["file_key"]))
    tr = await db.fetch_one(
        "SELECT scale_m_pt FROM drawing_transform WHERE drawing_id=:d", {"d": DRAWING_ID})
    fe = recognize(geom, row["discipline"], DRAWING_ID, drawing_title=row["title"],
                   scale_override=float(tr["scale_m_pt"]) if tr else None)
    cands = [c for c in fe.columns if len(c.get("outline") or []) >= 3][:LIMIT]
    flags = find_annotation_flags(cands)
    doc = fitz.open(stream=get_file_bytes(row["file_key"]), filetype="pdf")
    page = doc[0]
    try:
        font = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 20)
    except Exception:  # noqa: BLE001
        font = ImageFont.load_default()
    tiles = []
    for i, (el, flag) in enumerate(zip(cands, flags)):
        px = [meters_to_page(mx, my, fe.scale, fe.origin_pt, fe.page_h)
              for mx, my in el["outline"]]
        xs = [p[0] for p in px]
        ys = [p[1] for p in px]
        bb = (min(xs), min(ys), max(xs), max(ys))
        k = DPI / 72.0
        span = max(bb[2] - bb[0], bb[3] - bb[1])
        half = max(span * 6.0, 40.0) / 2
        cx, cy = (bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2
        pr = page.rect
        gx = max(pr.x0, min(cx - half, pr.x1 - 2 * half))
        gy = max(pr.y0, min(cy - half, pr.y1 - 2 * half))
        clip = fitz.Rect(gx, gy, min(gx + 2 * half, pr.x1), min(gy + 2 * half, pr.y1))
        if clip.width < 8 or clip.height < 8:
            continue
        # **小候选要提分辨率**：40pt 的裁剪在 150dpi 下只有 83 像素，
        # 放大到 400 是插值糊的 —— 判读者会把糊成一团的细条看成实心方块
        # （回测里两格「误删」正是这么来的，见 docs 的复核）。
        dpi = min(1200.0, max(float(DPI), DPI * 400.0 / max(clip.width * k, 1.0)))
        pm = page.get_pixmap(dpi=int(dpi), clip=clip)
        k = int(dpi) / 72.0
        im = Image.frombytes("RGB", (pm.width, pm.height), pm.samples)
        dr = ImageDraw.Draw(im)
        dr.rectangle([(bb[0] - clip.x0) * k, (bb[1] - clip.y0) * k,
                      (bb[2] - clip.x0) * k, (bb[3] - clip.y0) * k],
                     outline=(255, 0, 0), width=4)
        lo, sh = min_area_rect([(p[0], p[1]) for p in el["outline"]])
        label = f"{i} {'KEEP' if flag is None else flag.reason} {lo:.2f}x{sh:.3f}"
        tiles.append((label, im.resize((CELL, CELL))))
    doc.close()
    rows_n = (len(tiles) + COLS - 1) // COLS
    sheet = Image.new("RGB", (CELL * COLS, (CELL + 28) * max(rows_n, 1)), "white")
    dd = ImageDraw.Draw(sheet)
    for i, (label, im) in enumerate(tiles):
        x, y = (i % COLS) * CELL, (i // COLS) * (CELL + 28)
        dd.text((x + 6, y + 4), label, fill="black", font=font)
        sheet.paste(im, (x, y + 28))
        dd.rectangle([x, y, x + CELL - 1, y + CELL + 27], outline="#888")
    path = f"{OUT}/{DRAWING_ID[:8]}.png"
    sheet.save(path)
    print(f"{row['title']} · 候选 {len(cands)} · 命中 {sum(1 for f in flags if f)} → {path}")
    await db.disconnect()


asyncio.run(main())
