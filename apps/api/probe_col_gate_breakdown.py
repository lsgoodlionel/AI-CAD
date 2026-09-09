# 运行方式（容器无挂载，需 docker cp 注入；cad_api 被多会话共用，请自建容器）：
#   docker run -d --name cad_api_recall --network cad_default \
#     -e DATABASE_URL=postgresql://cad_user:cad_pass@postgres:5432/cad_db \
#     -e MINIO_ENDPOINT=minio:9000 -e MINIO_ACCESS_KEY=cad_minio \
#     -e MINIO_SECRET_KEY=cad_minio_pass -w /app cad-api:local sleep infinity
#   docker cp core/model3d cad_api_recall:/app/core/ && docker cp <本文件> cad_api_recall:/app/
#   docker exec cad_api_recall python <本文件名>
# PyMuPDF 与 torch 不能同进程；fitz doc 用完必须 close()。
"""召回缺口定位：把柱候选逐个映射到「是哪道闸删的」，出分项计数。

方法：复刻 `_find_columns` 的判定顺序（不是近似 —— 逐行同构），给每条
分支挂计数器；再把复刻出来的存活数与 `recognize()` 的真实产出对账，
对不上就说明复刻有偏差，结论作废。
"""
import asyncio, os
import databases as dbl
from core.config import settings
import core.model3d.element_recognizer as er
from core.model3d.geometry_extractor import extract_pdf_geometry
from core.model3d.layer_conventions import classify_by_layer, is_annotation_layer
from core.storage import get_file_bytes

IDS = [s.strip() for s in os.environ.get("IDS", "").replace("\n", ",").split(",") if s.strip()]


def breakdown(geom, ctx, layer_only):
    """逐候选记「死在哪一条」。返回 (计数字典, 存活候选列表)。"""
    c = {}
    def hit(k): c[k] = c.get(k, 0) + 1
    alive = []
    rects, rect_layers, rect_blocks = geom.rects, geom.rect_layers, geom.rect_blocks
    polys, poly_layers, poly_blocks = geom.polys, geom.poly_layers, geom.poly_blocks
    for src, items, layers, blocks in (("rect", rects, rect_layers, rect_blocks),
                                       ("poly", polys, poly_layers, poly_blocks)):
        for i, it in enumerate(items):
            if src == "rect":
                x, y, w, h, filled = it
            else:
                x, y, w, h = er._poly_bbox(it); filled = True
            lay = er._at(layers, i); blk = er._at(blocks, i)
            annotation = is_annotation_layer(lay)
            kind = classify_by_layer(lay, blk)
            is_column_layer = (not annotation) and kind == "column"
            other_kind = kind is not None and kind != "column"
            hit(f"{src}:总")
            if src == "rect" and not filled and not is_column_layer:
                hit(f"{src}:未填充"); continue
            w_m, h_m = ctx.len_m(w), ctx.len_m(h)
            if is_column_layer:
                if not er._is_plausible_column(w_m, h_m):
                    hit(f"{src}:图层柱但尺寸荒谬"); continue
                hit(f"{src}:图层柱通过"); alive.append((src, i, w_m, h_m, lay)); continue
            if annotation:
                hit(f"{src}:标注图层[{lay[:40]}]"); continue
            if other_kind:
                hit(f"{src}:图层是{kind}[{lay[:40]}]"); continue
            if layer_only:
                hit(f"{src}:layer_only(墙图/埋件图)"); continue
            if not er._is_column_size(w_m, h_m):
                hit(f"{src}:尺寸窗口外 {w_m:.2f}x{h_m:.2f}"); continue
            hit(f"{src}:猜测路径通过"); alive.append((src, i, w_m, h_m, lay))
    return c, alive


async def main():
    db = dbl.Database(settings.database_url); await db.connect()
    for did in IDS:
        row = await db.fetch_one("SELECT id::text AS id,title,discipline,file_key "
                                 "FROM drawings WHERE id::text=:d", {"d": did})
        if not row:
            print(did, "找不到"); continue
        geom = extract_pdf_geometry(get_file_bytes(row["file_key"]))
        tr = await db.fetch_one("SELECT scale_m_pt,confidence FROM drawing_transform "
                                "WHERE drawing_id=:d", {"d": did})
        sc = float(tr["scale_m_pt"]) if tr else None
        fe = er.recognize(geom, row["discipline"], did, drawing_title=row["title"],
                          scale_override=sc)
        print("=" * 100)
        print(f"{row['title']}  [{row['discipline']}]  {did}")
        print(f"  transform: scale_m_pt={sc} conf={tr['confidence'] if tr else None}"
              f"   识别 scale={fe.scale}")
        print(f"  页幅: w={geom.page_w}pt h={geom.page_h}pt  DXF毫米阈值={er._DXF_MODEL_SPACE_THRESHOLD}  明文比例={er._SCALE_RE.search(chr(10).join(t[2] for t in geom.texts) if geom.texts else '')}")
        print(f"  几何: rects={len(geom.rects)} polys={len(geom.polys)} "
              f"lines={len(geom.lines)} texts={len(geom.texts)} truncated={getattr(geom,'truncated',None)}")
        wall_drawing = er.is_wall_drawing(row["title"]) or er._is_embedded_part_plan(row["title"])
        print(f"  is_wall_drawing={er.is_wall_drawing(row['title'])} "
              f"埋件={er._is_embedded_part_plan(row['title'])} → layer_only={wall_drawing}")
        if not fe.scale:
            print("  !! scale=None，识别整体降级"); continue
        ctx = er._Ctx(fe.page_h, fe.scale, tuple(fe.origin_pt), did)
        cnt, alive = breakdown(geom, ctx, wall_drawing)
        print(f"  最终 columns={len(fe.columns)}  dense_arrays={len(getattr(fe,'dense_arrays',[]))}")
        print(f"  复刻存活={len(alive)} （对账：存活 - 密排 - 去重 应 ≈ 最终）")
        for k in sorted(cnt, key=lambda k: -cnt[k])[:25]:
            print(f"     {cnt[k]:7d}  {k}")
    await db.disconnect()

asyncio.run(main())
