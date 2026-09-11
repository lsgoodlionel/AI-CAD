"""导出 YOLO 训练集 + 生成带框核验图。

坐标链：scene 里的构件是**米** → 用 `drawing_transform` 反算回页面点
→ 按渲染 DPI 转像素。任何一环错了框就会偏，所以核验图是必须的。
"""
import asyncio, json, os, sys
from collections import defaultdict
import databases as databases_lib
from core.config import settings
from dependencies import DatabaseAdapter
from core.model3d.render_budget import dpi_for_scale
from core.model3d.yolo_export import CLASS_NAMES, class_id, outline_to_yolo_box

#: 兜底 DPI。**实际渲染分辨率按每张图自己的比例算**（`dpi_for_scale`）——
#: 一个全库常数对主力比例不够：1:150（实测 1068 张，全库最多）下
#: 0.6m 的柱在 DPI=100 时只有 **15.7px**，压在 YOLO 检测下限上。
#: 推导与像素预算见 `core/model3d/render_budget.py`。
DPI = 100
OUT = "/tmp/yolo_ds3"
# **只用闭合轮廓类**：管线/梁是折线，轴对齐包围盒对它们无意义——
# 核验图实测品红框（管线）横跨大片区域，框住的 99% 是别的东西。
K = ("columns", "walls", "slabs")
import re
PLAN = re.compile(r"平面")
# **按图种过滤**：核验图实测第一张就是电气防雷接地平面图，
# 结构构件训练集不该收它
BAD = re.compile(r"防雷|接地|照明|插座|弱电|消防报警|喷淋|通风|空调|给排水|电气")
VERIFY_N = int(os.environ.get("VERIFY_N", "3"))

#: 瓦片边长 = 训练 imgsz。瓦片间重叠 20%，跨缝的构件至少在一块里完整。
TILE = 1024
#: 没有任何框的瓦片按这个比例留作负样本 —— 全扔掉模型会把「有东西」
#: 当成默认，全留下正负样本严重失衡（大图上九成瓦片是空白）。
NEG_RATE = 0.05


def _export_tiles(page, fe, did, tag, tile_dpi, verified, meters_to_page):
    """整页像素框 → 按瓦片切 → 每块写一张图和一份 YOLO 标注。"""
    import random as _random
    from core.model3d.tiling import boxes_in_tile, tile_origins
    k = tile_dpi / 72.0
    ox, oy = getattr(fe, "origin_pt", (0.0, 0.0))
    ph = float(getattr(fe, "page_h", 0) or page.rect.height)
    boxes = []
    for kind, items, key in (("columns", fe.columns, "outline"),
                             ("walls", fe.walls, "path"),
                             ("slabs", fe.slabs, "outline")):
        cid = class_id(kind)
        if cid is None:
            continue
        for el in items:
            pts = [meters_to_page(p[0], p[1], fe.scale, (ox, oy), ph)
                   for p in (el.get(key) or []) if isinstance(p, (list, tuple)) and len(p) >= 2]
            if len(pts) < 2:
                continue
            xs = [x * k for x, _ in pts]; ys = [y * k for _, y in pts]
            boxes.append((cid, (min(xs), min(ys), max(xs), max(ys))))
    if len(boxes) < 10:
        return 0, 0, verified
    rng = _random.Random(did)
    W, H = page.rect.width * k, page.rect.height * k
    n_tiles = n_boxes = 0
    for i, (tx, ty) in enumerate(tile_origins(W, H, tile=TILE, overlap=0.2)):
        labels = boxes_in_tile(boxes, origin=(tx, ty), tile=TILE)
        if not labels and rng.random() > NEG_RATE:
            continue
        clip = fitz.Rect(tx / k, ty / k, (tx + TILE) / k, (ty + TILE) / k)
        # 浮点 DPI 必须走缩放矩阵 —— get_pixmap(dpi=) 只收整数（见 render_clip）
        from core.model3d.render_budget import render_clip
        pix = render_clip(page, clip, tile_dpi)
        name = f"{tag}_{did[:8]}_{i:03d}"
        pix.save(f"{OUT}/images/{name}.png")
        open(f"{OUT}/labels/{name}.txt", "w").write(
            "\n".join(f"{c} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}" for c, cx, cy, w, h in labels))
        n_tiles += 1; n_boxes += len(labels)
        if labels and verified < VERIFY_N:
            img = Image.open(f"{OUT}/images/{name}.png").convert("RGB")
            d = ImageDraw.Draw(img)
            colors = ["#e00", "#0a0", "#00e", "#e80", "#a0a", "#0aa"]
            for c, cx, cy, w, h in labels:
                d.rectangle([(cx - w / 2) * img.width, (cy - h / 2) * img.height,
                             (cx + w / 2) * img.width, (cy + h / 2) * img.height],
                            outline=colors[c % 6], width=2)
            img.save(f"{OUT}/verify/{name}_boxes.png")
            verified += 1
    return n_tiles, n_boxes, verified


async def main():
    raw = databases_lib.Database(settings.database_url); await raw.connect()
    db = DatabaseAdapter(raw)
    import fitz
    from PIL import Image, ImageDraw
    from core.storage import get_file_bytes
    os.makedirs(f"{OUT}/images", exist_ok=True)
    os.makedirs(f"{OUT}/labels", exist_ok=True)
    os.makedirs(f"{OUT}/verify", exist_ok=True)

    total_img = total_box = 0
    verified = 0
    for pid, tag in (("9188e163-c684-415e-a4ec-08f208273eff", "sgoh"),
                     ("77777777-7777-7777-7777-777777777777", "metro")):
        r = await db.fetch_one("SELECT scene FROM project_models WHERE project_id=$1 "
                               "ORDER BY version DESC LIMIT 1", pid)
        sc = r["scene"] if isinstance(r["scene"], dict) else json.loads(r["scene"])
        by_src = defaultdict(list)
        for f in sc.get("floors", []):
            for kind in K:
                for e in (f.get("elements") or {}).get(kind) or []:
                    if e.get("scale_suspect"): continue
                    by_src[str(e.get("src"))].append(
                        {"kind": kind, "pts": e.get("outline") or e.get("path") or []})
        tf = {str(x["drawing_id"]): dict(x) for x in await db.fetch_all(
            "SELECT t.drawing_id, t.scale_m_pt, t.origin_x, t.origin_y, t.page_h "
            "FROM drawing_transform t JOIN drawings d ON d.id=t.drawing_id "
            "WHERE d.project_id=$1", pid)}
        meta = {str(x["id"]): (x["file_key"], x["title"] or "", x["discipline"] or "")
                for x in await db.fetch_all(
                    "SELECT id, file_key, title, discipline FROM drawings "
                    "WHERE project_id=$1", pid)}
        keys = {k: v[0] for k, v in meta.items()}

        for did, elems in by_src.items():
            t = tf.get(did); fk = keys.get(did)
            _fk, title, disc = meta.get(did, ("", "", ""))
            if not t or not fk or len(elems) < 10: continue
            if not PLAN.search(title) or BAD.search(title): continue
            if disc not in ("structure", "architecture", "decoration"): continue
            # **按这张图自己的比例定分辨率，再切成 TILE 见方的瓦片**。
            # 旧做法是整页渲染：现役模型 imgsz=800 训整张图，A0 压到长边 800px
            # 等效只有 17 DPI，1:150 下 0.6m 的柱只有 2.7px —— 模型从没见过柱，
            # mAP50 = 0.049。切块后每块只渲染自己那一小片（clip），所以不受整页
            # 像素预算约束，DPI 可以按比例取足（柱 ≥24px，见 render_budget）。
            try:
                page = fitz.open(stream=get_file_bytes(fk), filetype="pdf")[0]
                _scale_m_pt = float(t[0]) if isinstance(t, (list, tuple)) else float(t)
                _denom = _scale_m_pt * 1000.0 / (25.4 / 72.0) if _scale_m_pt > 0 else None
                tile_dpi = dpi_for_scale(_denom)
            except Exception:
                continue
            from core.model3d.geometry_extractor import extract_pdf_geometry
            from core.model3d.element_recognizer import recognize
            from core.model3d.yolo_export import meters_to_page
            try:
                geom = extract_pdf_geometry(get_file_bytes(fk))
                fe = recognize(geom, disc, did, drawing_title=title)
            except Exception:
                continue
            sc_m = float(getattr(fe, "scale", 0) or 0)
            if sc_m <= 0: continue
            n_tiles, n_boxes, verified = _export_tiles(
                page, fe, did, tag, tile_dpi, verified, meters_to_page)
            total_img += n_tiles; total_box += n_boxes
    print(f"导出 {total_img} 张图 / {total_box} 个框 → {OUT}", flush=True)
    print(f"核验图 {verified} 张 → {OUT}/verify", flush=True)
    await raw.disconnect()

asyncio.run(main())
