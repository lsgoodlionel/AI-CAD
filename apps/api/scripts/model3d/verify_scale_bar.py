"""按金标准的问法重画比例验证图，但**不缩到 420 像素**。

金标准批次把 1872×1324 的裁图缩到 420 见方再让判读者看。
1:150 的平面图上一扇 1 米宽的门在原裁图里是 31 像素，缩完只剩 **7 像素**；
0.3 米的楼梯踏步只剩 2 像素 —— 判读者要拿来比长度的参照物**根本看不见**。
这个脚本用同样的裁法、同样的红线，但按原分辨率输出，用来检验
「判读者说错了」到底是比例真错，还是参照物被缩没了。

用法：
    python -m scripts.model3d.verify_scale_bar <drawing_id> <out.png> [比例分母]
不给分母就用库里的 `drawing_transform.scale_m_pt`。
"""
from __future__ import annotations

import asyncio
import sys

import databases as databases_lib
import fitz  # type: ignore[import-untyped]
from PIL import Image, ImageDraw

from core.config import settings
from core.storage import get_file_bytes

BAR_M = 8.0
PROBE_DPI, CROP_DPI, GRID = 24, 120, 3
EXCLUDE_RIGHT, EXCLUDE_BOTTOM = 0.28, 0.18
PT_MM = 25.4 / 72


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


async def main() -> int:
    drawing_id, out_path = sys.argv[1], sys.argv[2]
    override = float(sys.argv[3]) if len(sys.argv) > 3 else None
    db = databases_lib.Database(settings.database_url)
    await db.connect()
    row = await db.fetch_one(
        "SELECT t.scale_m_pt, d.title, d.file_key "
        "FROM drawing_transform t JOIN drawings d ON d.id=t.drawing_id "
        "WHERE t.drawing_id=:d", {"d": drawing_id})
    await db.disconnect()
    if row is None:
        print("找不到该图的变换记录")
        return 1
    scale = (override * PT_MM / 1000.0) if override else float(row["scale_m_pt"])

    page = fitz.open(stream=get_file_bytes(row["file_key"]), filetype="pdf")[0]
    clip = _densest_cell(page)
    pix = page.get_pixmap(dpi=CROP_DPI, clip=clip)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    k = CROP_DPI / 72.0
    d = ImageDraw.Draw(img)
    y = img.height * 0.5
    x0 = img.width * 0.08
    bar_px = BAR_M / scale * k
    d.line([(x0, y), (x0 + bar_px, y)], fill=(255, 0, 0), width=5)
    for xx in (x0, x0 + bar_px):
        d.line([(xx, y - 14), (xx, y + 14)], fill=(255, 0, 0), width=5)
    img.save(out_path)
    print(f"{row['title']}  1:{scale * 1000 / PT_MM:.0f}  裁格覆盖 "
          f"{clip.width * scale:.1f} m  红线占幅 {bar_px / img.width:.2f}  "
          f"输出 {img.width}x{img.height} -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
