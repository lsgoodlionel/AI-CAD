"""把「兜底挑板」改前改后的差异画在图纸上，供人眼确认排除的确实是图框。

只报数字不够 —— 座椅闸那次的教训是「误伤要逐张画图核验」
（`docs/PROGRESS.md` 2026-09-25）。本脚本渲染整幅图并叠框：

    红框 = 改前挑中、改后被图层闸排除的（应当是图框/标题栏）
    绿框 = 改后仍挑中的（进算量的那些）

坐标口径与 `geometry_extractor` 同源：图元已变换到**显示坐标系**
（`_apply_rotation`），`page.get_pixmap` 也渲染到显示坐标系，
所以直接乘缩放倍率即可，不需要再翻转 y。

用法（容器内）：
    python -m scripts.model3d.probe_slab_frame_render S-2-32-004C --out /tmp/x.png
"""
from __future__ import annotations

import argparse
import sys

OVERVIEW_WIDTH_PX = 2000


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("drawing_no")
    ap.add_argument("--out", default="/tmp/slab_frame.png")
    ap.add_argument("--width", type=int, default=OVERVIEW_WIDTH_PX)
    args = ap.parse_args()

    import asyncio
    import os

    import asyncpg
    import fitz

    from core.model3d.element_recognizer import (_SLAB_MIN_AREA_M2,
                                                 pick_fallback_slab_polygons,
                                                 recognize)
    from core.model3d.geometry_extractor import (budget_for,
                                                 extract_pdf_geometry)
    from core.storage import get_file_bytes

    async def _row():
        dsn = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
        conn = await asyncpg.connect(dsn)
        try:
            return await conn.fetchrow(
                "SELECT id::text AS id, drawing_no, title, discipline, file_key "
                "FROM drawings WHERE drawing_no = $1 AND file_key IS NOT NULL LIMIT 1",
                args.drawing_no)
        finally:
            await conn.close()

    row = asyncio.run(_row())
    if row is None:
        raise SystemExit(f"找不到图纸 {args.drawing_no}")

    data = get_file_bytes(row["file_key"])
    geom = extract_pdf_geometry(data)
    elems = recognize(geom, row["discipline"] or "structure", row["id"],
                      drawing_title=row["title"])
    scale = float(elems.scale)

    polys = geom.polys[:budget_for("polys")]
    layers = geom.poly_layers[:budget_for("polys")]

    def _area(poly):
        xs = [float(p[0]) for p in poly if len(p) >= 2]
        ys = [float(p[1]) for p in poly if len(p) >= 2]
        if not xs or not ys:
            return 0.0
        return ((max(xs) - min(xs)) * scale) * ((max(ys) - min(ys)) * scale)

    before = pick_fallback_slab_polygons(polys, area_of=_area,
                                         min_area=_SLAB_MIN_AREA_M2)
    after = pick_fallback_slab_polygons(polys, area_of=_area,
                                        min_area=_SLAB_MIN_AREA_M2, layers=layers)
    kept = {id(p) for p in after}
    dropped = [p for p in before if id(p) not in kept]

    doc = fitz.open(stream=data, filetype="pdf")
    page = doc[0]
    zoom = args.width / page.rect.width
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
    out_doc = fitz.open()
    out_page = out_doc.new_page(width=pix.width, height=pix.height)
    out_page.insert_image(fitz.Rect(0, 0, pix.width, pix.height), pixmap=pix)

    def _draw(poly, color, width):
        xs = [float(p[0]) * zoom for p in poly if len(p) >= 2]
        ys = [float(p[1]) * zoom for p in poly if len(p) >= 2]
        if not xs or not ys:
            return
        out_page.draw_rect(fitz.Rect(min(xs), min(ys), max(xs), max(ys)),
                           color=color, width=width)

    for poly in dropped:
        _draw(poly, (1, 0, 0), 4)
    for poly in after:
        _draw(poly, (0, 0.7, 0), 3)

    # PNG 用与底图同分辨率的 pixmap 导出（默认 72dpi 会把叠框糊掉）
    out = args.out if args.out.lower().endswith(".png") else args.out + ".png"
    out_doc.save(out[:-4] + ".pdf")
    out_page.get_pixmap(matrix=fitz.Identity).save(out)

    def _layer_of(poly):
        for j, q in enumerate(polys):
            if q is poly:
                return layers[j] if j < len(layers) else ""
        return ""

    print(f"{row['drawing_no']} {row['title']}")
    # 1pt = 0.352778mm，故 1:N 的 N = scale(米/pt) / 0.000352778
    print(f"  比例 1:{round(scale / 0.000352778) if scale else '?'}  "
          f"页面 {page.rect.width:.0f}×{page.rect.height:.0f}pt  多边形 {len(polys)}")
    print(f"  兜底板 改前 {len(before)} → 改后 {len(after)}")
    print(f"  红框（被排除，{len(dropped)} 个）：")
    for poly in dropped:
        print(f"    {_area(poly):12,.1f} m²  {len(poly):4d} 点  layer={_layer_of(poly)!r}")
    print(f"  绿框（保留，{len(after)} 个）：")
    for poly in after[:12]:
        print(f"    {_area(poly):12,.1f} m²  {len(poly):4d} 点  layer={_layer_of(poly)!r}")
    print(f"  图写入 {out} 与 {out[:-4]}.pdf")
    return 0


if __name__ == "__main__":
    sys.exit(main())
