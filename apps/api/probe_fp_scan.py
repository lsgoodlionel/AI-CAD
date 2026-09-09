"""假柱候选 · 全库扫描 worker：导出每张图的柱候选**页面坐标**框 + 图种。

与 `probe_seat_scan.py` 的差别：那份只导出米坐标（判「间距≈自身尺寸」够用），
本轮要测的闸**与图种/轴网有关**，所以要留页面坐标（叠框核验用）、
`classify_view_type` 的判别结果、以及识别器自己检出的轴线条数。

**只跑柱这条路径**，不调用整个 `recognize`：实测整库跑 `recognize`
平均 23 秒/张（单进程 26 张 600 秒），最慢一张 102 秒，几乎全耗在
`_pair_up` 的 O(n²) 墙梁配对上——而墙梁与柱**互不影响**。
柱这条路径是自足的：`_detect_axes → _detect_scale → _Ctx → _find_columns
→ _drop_duplicate_elements → _clip_to_axes`，与 `_recognize` 里
`result.columns` 的算法逐字一致（`_find_slabs` 只读柱不改柱）。

用法：python probe_fp_scan.py <shard_index> <shard_total>

**不导入 torch**（PyMuPDF 与 torch 同进程在 aarch64 段错误）。
每 10 张增量落盘；`databases.Database` 传 min_size=1（8 个 worker ×
默认 10 连接会撞 PG 的 max_connections）。
"""
import asyncio, gc, json, os, sys
import databases as dbl
from core.config import settings
from core.model3d import element_recognizer as ER
from core.model3d.geometry_extractor import extract_pdf_geometry
from core.model3d.types import FloorElements
from core.model3d.yolo_export import meters_to_page
from core.storage import get_file_bytes
from services.drawing_view_classifier import classify_view_type



#: 侦听 `_downsample_ring`，把**降点前**的顶点数按页面包围盒记下来。
#: 降点保住四个极值点（见其 docstring），所以包围盒可以当键；
#: 之后 `_drop_duplicate_elements` 会合并轮廓，配不上的记 -1 并统计。
_VTX: dict = {}
#: 比例的来源与取值（`resolve_scale` 的三个输入 + 结论），供 eval 判
#: 「这张图的尺寸判据站不站得住」。
_SCALE: dict = {}
_ORIG_DOWNSAMPLE = ER._downsample_ring


def _spy_downsample(poly, limit):
    xs = [p[0] for p in poly]; ys = [p[1] for p in poly]
    _VTX[(round(min(xs), 1), round(min(ys), 1),
          round(max(xs), 1), round(max(ys), 1))] = len(poly)
    return _ORIG_DOWNSAMPLE(poly, limit)


ER._downsample_ring = _spy_downsample


def columns_only(geom, discipline, did, title, scale_override):
    """`_recognize` 的柱分支逐字复刻（省掉墙/梁/板/管线）。

    **机电图不产柱** —— `_recognize` 在 `discipline == "mep"` 时提前
    返回管线/设备，根本不走柱分支。等价性检查逮到过：漏了这一条，
    「电气-竣工图--一层电气综合监控平面图（三）」快路径多出 26 根柱。
    """
    _VTX.clear()
    _SCALE.clear()
    if discipline == "mep":
        fe = FloorElements(scale=0.0)
        return fe
    lines = geom.lines[:ER.MAX_PRIMITIVES]
    rects = geom.rects[:ER.MAX_PRIMITIVES]
    polys = geom.polys[:ER.MAX_PRIMITIVES]
    all_text = "；".join(t[2] for t in geom.texts)
    axis_x, axis_y, _axis_lines = ER._detect_axes(
        lines, geom.page_w, geom.page_h, geom.texts)
    detected, is_guess = ER._detect_scale(all_text, geom.page_w, axis_x, axis_y)
    scale = ER.resolve_scale(detected, scale_override, geom.page_w,
                             detected_is_guess=is_guess)
    _SCALE.update(detected=detected, guess=bool(is_guess), scale=scale,
                  stored=scale_override)
    origin = ER._origin_pt(axis_x, axis_y, geom.page_h)
    ctx = ER._Ctx(geom.page_h, scale, origin, did)
    truncated = geom.primitive_count() > ER.MAX_PRIMITIVES
    result = FloorElements(
        scale=scale, axes=ER._axes_dict(axis_x, axis_y, ctx, truncated, all_text))
    wall_drawing = ER.is_wall_drawing(title) or ER._is_embedded_part_plan(title)
    args = (rects, geom.rect_layers[:ER.MAX_PRIMITIVES],
            geom.rect_blocks[:ER.MAX_PRIMITIVES], polys,
            geom.poly_layers[:ER.MAX_PRIMITIVES],
            geom.poly_blocks[:ER.MAX_PRIMITIVES], ctx)
    result.columns = ER._find_columns(*args, layer_only=wall_drawing)
    # 闸只关**猜测路径**，图层明说是柱的照留。所以「闸删多少」不等于
    # 候选总数 —— 要减去 layer_only 下仍留下的那些，这里一并算出来。
    kept = FloorElements(scale=scale, axes=result.axes)
    kept.columns = ER._find_columns(*args, layer_only=True)
    ER._drop_duplicate_elements(result)
    ER._drop_duplicate_elements(kept)
    ER._clip_to_axes(result)
    ER._clip_to_axes(kept)
    result.origin_pt = tuple(ctx.origin)
    result.page_h = ctx.page_h
    result.layer_only_columns = len(kept.columns)
    return result


async def main(IDX: int, TOT: int):
    OUT = f"/tmp/fp_scan/shard_{IDX}.json"
    os.makedirs("/tmp/fp_scan", exist_ok=True)
    db = dbl.Database(settings.database_url, min_size=1, max_size=2)
    await db.connect()
    rows = await db.fetch_all(
        "SELECT d.id::text AS id, d.title, d.drawing_no, d.discipline, "
        "       d.file_key, d.project_id::text AS pid, t.scale_m_pt "
        "FROM drawings d LEFT JOIN drawing_transform t ON t.drawing_id = d.id "
        "ORDER BY d.id")
    mine = [r for i, r in enumerate(rows) if i % TOT == IDX]
    done = {}
    if os.path.exists(OUT):
        done = {r["did"]: r for r in json.load(open(OUT))}
    out = list(done.values())
    for k, row in enumerate(mine):
        if row["id"] in done:
            continue
        vt = classify_view_type({"title": row["title"] or "",
                                 "drawing_no": row["drawing_no"] or ""})
        rec = {"did": row["id"], "pid": row["pid"], "title": row["title"],
               "discipline": row["discipline"], "view": vt.view_type,
               "vconf": round(vt.confidence, 2), "cols": [], "err": None,
               "pw": 0.0, "ph": 0.0, "nax": [0, 0], "nlayer": 0, "vtx": [],
               "scale": 0.0, "guess": None, "detected": 0.0, "stored": None}
        try:
            geom = extract_pdf_geometry(get_file_bytes(row["file_key"]))
            rec["pw"], rec["ph"] = round(geom.page_w, 1), round(geom.page_h, 1)
            fe = columns_only(geom, row["discipline"], row["id"], row["title"],
                              float(row["scale_m_pt"]) if row["scale_m_pt"] else None)
            rec["nax"] = [len(fe.axes.get("x") or []), len(fe.axes.get("y") or [])]
            rec["nlayer"] = getattr(fe, "layer_only_columns", 0)
            rec["scale"] = round(float(_SCALE.get("scale") or 0.0), 6)
            rec["guess"] = _SCALE.get("guess")
            rec["detected"] = round(float(_SCALE.get("detected") or 0.0), 6)
            rec["stored"] = (round(float(_SCALE["stored"]), 6)
                             if _SCALE.get("stored") else None)
            for c in fe.columns:
                o = c.get("outline") or []
                if len(o) < 3 or not fe.scale:
                    continue
                px = [meters_to_page(mx, my, fe.scale, fe.origin_pt, fe.page_h)
                      for mx, my in o]
                xs = [p[0] for p in px]; ys = [p[1] for p in px]
                box = [round(min(xs), 1), round(min(ys), 1),
                       round(max(xs), 1), round(max(ys), 1)]
                rec["cols"].append(box)
                # 0 = 查不到。矩形分支不经过降点（无记录），
                # 去重合并过的多边形包围盒也会变、配不上 —— 两种都记 0，
                # 由 eval 报出占比，不冒充成一个具体数字。
                rec["vtx"].append(_VTX.get(tuple(box), 0))
            del geom, fe
        except Exception as e:
            rec["err"] = type(e).__name__
        out.append(rec)
        del rec
        if k % 10 == 0:
            json.dump(out, open(OUT, "w"))
            gc.collect()
            print(f"[{IDX}] {k}/{len(mine)} n={len(out)}", flush=True)
    json.dump(out, open(OUT, "w"))
    print(f"[{IDX}] DONE {len(out)}", flush=True)
    await db.disconnect()

if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]), int(sys.argv[2])))
