"""按页面坐标区域高分辨率渲染图纸,供人工读真值(轴号/坐标/尺寸)。

**为什么需要它**:A0 图整幅渲出来轴号只有几像素,人眼读不出;
而真值是所有识别工作的标尺(见 docs/PHASE_I_BLUEPRINT.md §1),必须能读准。

用法:
    python scripts/model3d/render_region.py <drawing_no> [x0 y0 x1 y1] [--scale N]

坐标为页面 pt。不给区域则整幅渲出(用于先定位要放大的区域)。
"""
from __future__ import annotations

import argparse
import asyncio
import sys

import fitz

from core.database import database
from core.storage import get_file_bytes

#: 整幅预览的目标宽度(px)。够看清分区布局,又不至于几十 MB
OVERVIEW_WIDTH_PX = 2400

#: 区域放大的默认倍率。轴号圈实测 14pt,×6 后 ≈84px,人眼可读
DEFAULT_REGION_SCALE = 6.0


async def fetch_pdf(drawing_no: str) -> bytes:
    await database.connect()
    try:
        row = await database.fetch_one(
            "SELECT file_key FROM drawings WHERE drawing_no = :no", {"no": drawing_no}
        )
    finally:
        await database.disconnect()
    if not row:
        raise SystemExit(f"未找到图纸 {drawing_no}")
    return get_file_bytes(row["file_key"])


def render(pdf: bytes, out: str, rect: tuple | None, scale: float) -> None:
    doc = fitz.open(stream=pdf, filetype="pdf")
    page = doc[0]
    if rect is None:
        scale = OVERVIEW_WIDTH_PX / page.rect.width
        clip = None
    else:
        clip = fitz.Rect(*rect)
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=clip)
    pix.save(out)
    print(f"{out}  {pix.width}×{pix.height}px  scale={scale:.2f}  "
          f"page={page.rect.width:.0f}×{page.rect.height:.0f}pt")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("drawing_no")
    ap.add_argument("coords", nargs="*", type=float, help="x0 y0 x1 y1(页面 pt)")
    ap.add_argument("--scale", type=float, default=DEFAULT_REGION_SCALE)
    ap.add_argument("--out", default="/tmp/region.png")
    args = ap.parse_args(argv)

    if args.coords and len(args.coords) != 4:
        raise SystemExit("区域需要恰好 4 个坐标:x0 y0 x1 y1")
    rect = tuple(args.coords) if args.coords else None
    render(asyncio.run(fetch_pdf(args.drawing_no)), args.out, rect, args.scale)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
