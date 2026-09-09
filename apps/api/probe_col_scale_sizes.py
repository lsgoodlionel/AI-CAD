# 运行方式（容器无挂载，需 docker cp 注入；cad_api 被多会话共用，请自建容器）：
#   docker run -d --name cad_api_recall --network cad_default \
#     -e DATABASE_URL=postgresql://cad_user:cad_pass@postgres:5432/cad_db \
#     -e MINIO_ENDPOINT=minio:9000 -e MINIO_ACCESS_KEY=cad_minio \
#     -e MINIO_SECRET_KEY=cad_minio_pass -w /app cad-api:local sleep infinity
#   docker cp core/model3d cad_api_recall:/app/core/ && docker cp <本文件> cad_api_recall:/app/
#   docker exec cad_api_recall python <本文件名>
# PyMuPDF 与 torch 不能同进程；fitz doc 用完必须 close()。
"""把被「尺寸」类判据删掉的候选按图层与实测边长列出来，并试算换比例后的结局。"""
import asyncio, os, statistics as st, collections
import databases as dbl
from core.config import settings
import core.model3d.element_recognizer as er
from core.model3d.geometry_extractor import extract_pdf_geometry
from core.model3d.layer_conventions import classify_by_layer, is_annotation_layer
from core.storage import get_file_bytes

IDS = [s.strip() for s in os.environ.get("IDS", "").split(",") if s.strip()]


def run(geom, scale, page_h, did):
    ctx = er._Ctx(page_h, scale, (None, None), did)
    keep, rej = [], collections.defaultdict(list)
    for i, poly in enumerate(geom.polys):
        x, y, w, h = er._poly_bbox(poly)
        lay = er._at(geom.poly_layers, i); blk = er._at(geom.poly_blocks, i)
        ann = is_annotation_layer(lay); kind = classify_by_layer(lay, blk)
        icl = (not ann) and kind == "column"
        other = kind is not None and kind != "column"
        w_m, h_m = ctx.len_m(w), ctx.len_m(h)
        if icl:
            (keep if er._is_plausible_column(w_m, h_m) else rej["图层柱-荒谬"]
             ).append((w_m, h_m, lay))
        elif ann: rej["标注层"].append((w_m, h_m, lay))
        elif other: rej[f"图层={kind}"].append((w_m, h_m, lay))
        elif er._is_column_size(w_m, h_m): keep.append((w_m, h_m, lay))
        else: rej["猜测-窗口外"].append((w_m, h_m, lay))
    return keep, rej


async def main():
    db = dbl.Database(settings.database_url); await db.connect()
    for did in IDS:
        row = await db.fetch_one("SELECT title,discipline,file_key FROM drawings WHERE id::text=:d", {"d": did})
        geom = extract_pdf_geometry(get_file_bytes(row["file_key"]))
        tr = await db.fetch_one("SELECT scale_m_pt FROM drawing_transform WHERE drawing_id=:d", {"d": did})
        stored = float(tr["scale_m_pt"]) if tr else None
        det, guess = er._detect_scale("；".join(t[2] for t in geom.texts), geom.page_w,
                                      [], [])
        used = er.resolve_scale(det, stored, geom.page_w, guess)
        print("=" * 96)
        print(f"{row['title']}  页宽{geom.page_w}pt")
        print(f"  _detect_scale → {det:.6f} (是猜的={guess})  落库={stored}  "
              f"resolve→{used:.6f}   图宽换算: 用{geom.page_w*used:.0f}m / 落库{geom.page_w*(stored or 0):.0f}m")
        for tag, sc in (("当前(used)", used), ("落库(stored)", stored)):
            if not sc: continue
            keep, rej = run(geom, sc, geom.page_h, did)
            sizes = sorted(max(a, b) for a, b, _ in keep)
            print(f"  [{tag} {sc:.5f}] 存活 {len(keep):4d}"
                  + (f"  边长中位 {st.median(sizes):.2f}m 范围 {sizes[0]:.2f}~{sizes[-1]:.2f}" if sizes else ""))
            for k, v in sorted(rej.items(), key=lambda kv: -len(kv[1]))[:5]:
                s = sorted(max(a, b) for a, b, _ in v)
                print(f"        删 {len(v):4d}  {k:14s} 边长中位 {st.median(s):.3f}m "
                      f"[{s[0]:.3f}~{s[-1]:.3f}]  例图层 {v[0][2][:52]}")
    await db.disconnect()
asyncio.run(main())
