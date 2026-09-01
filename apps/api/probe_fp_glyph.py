"""图框标题栏里的候选**长什么样**：矩形还是多边形、几个顶点、多大（页面点）。

想法：图框/标题栏/图签栏都是按**纸面 1:1** 画的，格子与字号只有几毫米；
而图上构件按 1:50~1:200 画。若两者在「页面点尺寸」或「顶点数」上分得开，
就有一条与图种无关、对平面图同样有效的判据。先量，再决定要不要做。
"""
import asyncio, sys
import databases as dbl
from core.config import settings
from core.model3d import element_recognizer as ER
from core.model3d.geometry_extractor import extract_pdf_geometry
from core.storage import get_file_bytes

TB_X = 0.86      # 仅用于**分组统计**（不是判据）：右侧带当作标题栏侧


async def main(titles):
    db = dbl.Database(settings.database_url, min_size=1, max_size=2)
    await db.connect()
    for t in titles:
        r = await db.fetch_one(
            "SELECT d.id::text AS id, d.title, d.discipline, d.file_key, "
            "t.scale_m_pt FROM drawings d "
            "LEFT JOIN drawing_transform t ON t.drawing_id=d.id "
            "WHERE d.title=:t LIMIT 1", {"t": t})
        if not r:
            print("找不到", t); continue
        geom = extract_pdf_geometry(get_file_bytes(r["file_key"]))
        so = float(r["scale_m_pt"]) if r["scale_m_pt"] else None
        axis_x, axis_y, _ = ER._detect_axes(geom.lines, geom.page_w, geom.page_h, geom.texts)
        all_text = "；".join(x[2] for x in geom.texts)
        det, guess = ER._detect_scale(all_text, geom.page_w, axis_x, axis_y)
        scale = ER.resolve_scale(det, so, geom.page_w, detected_is_guess=guess)
        ctx = ER._Ctx(geom.page_h, scale, ER._origin_pt(axis_x, axis_y, geom.page_h), r["id"])
        groups = {"栏内": [], "图上": []}
        # 矩形分支
        for i, (x, y, w, h, filled) in enumerate(geom.rects):
            if not filled:
                continue
            if not ER._is_column_size(ctx.len_m(w), ctx.len_m(h)):
                continue
            side = "栏内" if (x + w / 2) / geom.page_w >= TB_X else "图上"
            groups[side].append(("rect", 4, max(w, h)))
        # 多边形分支
        for poly in geom.polys:
            x, y, w, h = ER._poly_bbox(poly)
            if not ER._is_column_size(ctx.len_m(w), ctx.len_m(h)):
                continue
            side = "栏内" if (x + w / 2) / geom.page_w >= TB_X else "图上"
            groups[side].append(("poly", len(poly), max(w, h)))
        print(f"\n{str(t)[:34]}  比例 {scale:.5f} m/pt")
        for side, items in groups.items():
            if not items:
                print(f"  {side}: 0"); continue
            rects = [i for i in items if i[0] == "rect"]
            polys = [i for i in items if i[0] == "poly"]
            sizes = sorted(i[2] for i in items)
            vtx = sorted(i[1] for i in polys) or [0]
            print(f"  {side}: {len(items):4d} 个（矩形 {len(rects)} / 多边形 {len(polys)}）"
                  f" 页面边长 pt 中位 {sizes[len(sizes)//2]:5.1f} "
                  f"[{sizes[0]:.1f}~{sizes[-1]:.1f}]"
                  f"  多边形顶点数中位 {vtx[len(vtx)//2]}")
    await db.disconnect()

asyncio.run(main(sys.argv[1:]))
