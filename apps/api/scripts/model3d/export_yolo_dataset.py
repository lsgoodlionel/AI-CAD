"""导出 YOLO 训练集 + 生成带框核验图。

坐标链：scene 里的构件是**米** → 用 `drawing_transform` 反算回页面点
→ 按渲染 DPI 转像素。任何一环错了框就会偏，所以核验图是必须的。
"""
import asyncio, json, os, sys
from collections import Counter, defaultdict
import databases as databases_lib
from core.config import settings
from dependencies import DatabaseAdapter
from core.model3d.render_budget import dpi_for_scale
from services.scale_candidates import STANDARD_DENOMINATORS
import fitz  # type: ignore[import-untyped]
from PIL import Image, ImageDraw
from core.model3d.yolo_export import CLASS_NAMES, class_id, outline_to_yolo_box

#: 兜底 DPI。**实际渲染分辨率按每张图自己的比例算**（`dpi_for_scale`）——
#: 一个全库常数对主力比例不够：1:150（实测 1068 张，全库最多）下
#: 0.6m 的柱在 DPI=100 时只有 **15.7px**，压在 YOLO 检测下限上。
#: 推导与像素预算见 `core/model3d/render_budget.py`。
DPI = 100
#: 输出目录。**不能是 `/tmp/yolo_ds3`** —— 那里是现役模型的 76 张整页训练图，
#: 新瓦片写进去会和旧图混在一起，训练集就说不清是哪一版了。
OUT = os.environ.get("YOLO_OUT", "/tmp/yolo_tiles")
#: 冒烟时只跑前 N 张合格图；0 = 不限。
MAX_DRAWINGS = int(os.environ.get("MAX_DRAWINGS", "0"))
#: 每张图最多留这么多块瓦片。A0 在主力比例 1:150 下约 48 块 —— 远超这个数，
#: 要么比例错了，要么图异常密。实测一张比例错成 1:2465 的图算出 2505 DPI、
#: 约 1.4 万块瓦片，冒烟的 555 块几乎全来自它：一张图就能占满数据集。
#: 与金标准「每图至多 2 格」同一个道理。在渲染**之前**抽样，超出的不花渲染成本。
MAX_TILES_PER_DRAWING = 48
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


def tile_dpi_for(row) -> float:
    """transform 表的一行（dict）→ 渲染 DPI。

    此前写成 `float(t)`，而 `t` 是整行 dict —— 抛 TypeError，被外层
    `except Exception: continue` 吞掉，**每张图都被静默跳过**，导出 0 张。
    缺比例时回落 `dpi_for_scale` 的兜底值，而不是让整张图消失。
    """
    denom = _denom_of(row)
    if denom is not None and not _is_standard(denom):
        # 不在 GB/T 50001 §6.0.4 表里的比例不可信 —— 回落兜底，不照单全收。
        # 实测「基础底板换撑平面布置图」存的是 1:2465，照算就是 2505 DPI。
        denom = None
    return dpi_for_scale(denom)


def _denom_of(row) -> float | None:
    scale = row.get("scale_m_pt") if isinstance(row, dict) else None
    try:
        scale = float(scale)
    except (TypeError, ValueError):
        return None
    return scale * 1000.0 / (25.4 / 72.0) if scale > 0 else None


def _is_standard(denom: float) -> bool:
    """与 `scale_evidence` 同一容差：离表内某个分母 2% 以内算是它。"""
    return any(abs(denom - d) <= 0.02 * d for d in STANDARD_DENOMINATORS)


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
    keep = []
    for i, (tx, ty) in enumerate(tile_origins(W, H, tile=TILE, overlap=0.2)):
        labels = boxes_in_tile(boxes, origin=(tx, ty), tile=TILE)
        if not labels and rng.random() > NEG_RATE:
            continue
        keep.append((i, tx, ty, labels))
    if len(keep) > MAX_TILES_PER_DRAWING:
        keep = sorted(rng.sample(keep, MAX_TILES_PER_DRAWING), key=lambda k: k[0])
    for i, tx, ty, labels in keep:
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
    from core.storage import get_file_bytes
    os.makedirs(f"{OUT}/images", exist_ok=True)
    os.makedirs(f"{OUT}/labels", exist_ok=True)
    os.makedirs(f"{OUT}/verify", exist_ok=True)

    total_img = total_box = 0
    verified = 0
    # 跳过要**计数**，不能静默 —— 上一版每张图都被吞掉，结果是 0 张且无人知晓
    skipped: Counter = Counter()
    processed = 0
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
            if MAX_DRAWINGS and processed >= MAX_DRAWINGS: break
            processed += 1
            # **按这张图自己的比例定分辨率，再切成 TILE 见方的瓦片**。
            # 旧做法是整页渲染：现役模型 imgsz=800 训整张图，A0 压到长边 800px
            # 等效只有 17 DPI，1:150 下 0.6m 的柱只有 2.7px —— 模型从没见过柱，
            # mAP50 = 0.049。切块后每块只渲染自己那一小片（clip），所以不受整页
            # 像素预算约束，DPI 可以按比例取足（柱 ≥24px，见 render_budget）。
            try:
                page = fitz.open(stream=get_file_bytes(fk), filetype="pdf")[0]
            except Exception as exc:  # noqa: BLE001 - 单图失败跳过，但必须计数
                skipped[f"打不开:{type(exc).__name__}"] += 1
                continue
            tile_dpi = tile_dpi_for(t)
            _d = _denom_of(t)
            if _d is not None and not _is_standard(_d):
                skipped[f"比例不合国标→兜底 DPI（不跳过，只计数）"] += 1
            from core.model3d.geometry_extractor import extract_pdf_geometry
            from core.model3d.element_recognizer import recognize
            from core.model3d.yolo_export import meters_to_page
            try:
                geom = extract_pdf_geometry(get_file_bytes(fk))
                fe = recognize(geom, disc, did, drawing_title=title)
            except Exception as exc:  # noqa: BLE001
                skipped[f"识别失败:{type(exc).__name__}"] += 1
                continue
            sc_m = float(getattr(fe, "scale", 0) or 0)
            if sc_m <= 0:
                skipped["识别出的比例为 0"] += 1
                continue
            n_tiles, n_boxes, verified = _export_tiles(
                page, fe, did, tag, tile_dpi, verified, meters_to_page)
            total_img += n_tiles; total_box += n_boxes
    print(f"导出 {total_img} 张图 / {total_box} 个框 → {OUT}", flush=True)
    print(f"核验图 {verified} 张 → {OUT}/verify", flush=True)
    print(f"处理 {processed} 张合格图；跳过（按原因）：{dict(skipped) or '无'}", flush=True)
    if total_img == 0:
        await raw.disconnect()
        raise SystemExit("✗ 导出 0 张 —— 数据集无效。上一版就是这样静默产出 0 张的。")
    await raw.disconnect()

if __name__ == "__main__":
    # 守卫是可测性的前提：此前模块底部直接 `asyncio.run(main())`，
    # 测试一 import 就会对着线上库跑一遍全量导出。
    asyncio.run(main())
