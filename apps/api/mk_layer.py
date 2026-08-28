"""图层分类器候选 → 接触表。

`classify_by_layer` 现在是**承重函数**：管线/设备的新图层闸完全依赖它，
而它从未验证过，却已被偶然撞出两个 bug（`C-` 撞窗编号、`M-` 撞机电前缀）。

两个抽样层：
  L 表 —— 分类器**说出了类别**的几何，问「它到底是什么」→ 测精确率
  U 表 —— 分类器**判不出**的几何（实测占 57.5%），问「里面是什么」→ 测漏了什么
"""
import asyncio, collections, json, os, random, string
import databases as databases_lib, fitz
from PIL import Image, ImageDraw, ImageFont
from core.config import settings
from core.model3d.gold.batch_codes import make_codes
from core.model3d.element_recognizer import recognize
from core.model3d.geometry_extractor import extract_pdf_geometry
from core.model3d.layer_conventions import classify_by_layer, is_annotation_layer
from core.model3d.yolo_export import meters_to_page
from core.storage import get_file_bytes

PER_CLASS, N_UNKNOWN = 7, 40
CTX_RATIO, MIN_HALF_PT = 12.0, 60.0  # 上下文 = 图元自身尺寸 ×12，下限 60pt
OUT, DPI = "/tmp/gpt_layer", 200
PROJECTS = ("77777777-7777-7777-7777-777777777777",
            "88888888-8888-8888-8888-888888888888")

random.seed(20260906)
os.makedirs(OUT, exist_ok=True)


async def _drawings(db, n=70):
    rows = await db.fetch_all(
        "SELECT d.id,d.title,d.discipline,d.file_key FROM drawings d "
        "WHERE d.project_id::text = ANY(:p) AND d.title IS NOT NULL", {"p": list(PROJECTS)})
    rows = [r for r in rows if "平面" in str(r["title"])]
    random.shuffle(rows)
    return rows[:n]


async def main():
    db = databases_lib.Database(settings.database_url); await db.connect()
    # 收集：(类别, 图元种类, 几何, 图层名, drawing_row, fe, page)
    pool = collections.defaultdict(list)
    for row in await _drawings(db):
        try:
            geom = extract_pdf_geometry(get_file_bytes(row["file_key"]))
            tr = await db.fetch_one("SELECT scale_m_pt FROM drawing_transform "
                                    "WHERE drawing_id=:d", {"d": str(row["id"])})
            fe = recognize(geom, row["discipline"], str(row["id"]),
                           drawing_title=row["title"],
                           scale_override=float(tr["scale_m_pt"]) if tr else None)
            if not fe.scale:
                continue
            page = fitz.open(stream=get_file_bytes(row["file_key"]), filetype="pdf")[0]
        except Exception:
            continue
        items = []
        for i, ln in enumerate(geom.lines):
            lay = geom.line_layers[i] if i < len(geom.line_layers) else ""
            items.append(("line", ln, lay))
        for i, r in enumerate(geom.rects):
            lay = geom.rect_layers[i] if i < len(geom.rect_layers) else ""
            items.append(("rect", r, lay))
        for i, p in enumerate(geom.polys):
            lay = geom.poly_layers[i] if i < len(geom.poly_layers) else ""
            items.append(("poly", p, lay))
        random.shuffle(items)
        for shape, g, lay in items[:400]:
            if is_annotation_layer(lay):
                continue
            k = classify_by_layer(lay)
            key = k or ("_unknown" if lay.strip() else None)
            if key is None:                    # 空图层名：无信息，不入样
                continue
            cap = N_UNKNOWN if key == "_unknown" else PER_CLASS
            if len(pool[key]) < cap:
                pool[key].append((shape, g, lay, row, fe, page))
    print("抽样池：", {k: len(v) for k, v in sorted(pool.items())})

    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 24)
    except Exception:
        font = ImageFont.load_default()
    CELL, COLS, ROWS = 380, 5, 4

    _seen: set[str] = set()
    while len(_seen) < 400:
        _seen.add("".join(random.choice(_CODE_ALPHABET) for _ in range(4)))
    _codes = sorted(_seen)
    random.shuffle(_codes)

    def build(entries, prefix):
        tiles = []
        for shape, g, lay, row, fe, page in entries:
            if shape == "line":
                x0, y0, x1, y1 = g; pts = [(x0, y0), (x1, y1)]
            elif shape == "rect":
                x, y, w, h, _ = g
                pts = [(x, y), (x + w, y + h)]
            else:
                pts = list(g)
            xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
            cxp, cyp = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
            # **上下文按图元自身尺寸取，不用固定米数**：这里采的是单个几何原语
            # （一条线段、一个小矩形），不是识别出来的构件。固定 8 米时，
            # 一根 0.3 米的线段缩到 380 像素只剩 4 个像素的斑点 —— 实测判读者
            # 在这种表上退化成默认答案（100 格全 confident、notes 全空、
            # 6 个柱格全答成梁）。早先成功的柱批次用的正是「自身尺寸 ×12」。
            extent = max(max(xs) - min(xs), max(ys) - min(ys))
            half = max(extent * CTX_RATIO, MIN_HALF_PT) / 2
            pr = page.rect
            ox = max(pr.x0, min(cxp - half, pr.x1 - 2 * half))
            oy = max(pr.y0, min(cyp - half, pr.y1 - 2 * half))
            clip = fitz.Rect(ox, oy, min(ox + 2 * half, pr.x1), min(oy + 2 * half, pr.y1))
            if clip.width < 8 or clip.height < 8:
                continue
            try:
                pix = page.get_pixmap(dpi=DPI, clip=clip)
                img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            except Exception:
                continue
            ex = img.convert("L").getextrema()
            if ex[1] - ex[0] < 30:
                continue
            d, k2 = ImageDraw.Draw(img), DPI / 72.0
            px = [((p[0] - clip.x0) * k2, (p[1] - clip.y0) * k2) for p in pts]
            if shape == "line":
                d.line(px, fill=(255, 0, 0), width=5)
            else:
                axs = [p[0] for p in px]; ays = [p[1] for p in px]
                d.rectangle([min(axs), min(ays), max(axs), max(ays)],
                            outline=(255, 0, 0), width=5)
            tiles.append((lay, row, img.resize((CELL, CELL))))
        man, sheets = [], []
        for si in range(0, len(tiles), COLS * ROWS):
            chunk = tiles[si:si + COLS * ROWS]
            sh = Image.new("RGB", (CELL * COLS, (CELL + 34) * ROWS), "white")
            dd = ImageDraw.Draw(sh)
            for i, (lay, row, img) in enumerate(chunk):
                cx, cy = (i % COLS) * CELL, (i // COLS) * (CELL + 34)
                tag = _codes.pop()
                dd.text((cx + 8, cy + 6), tag, fill="black", font=font)
                sh.paste(img, (cx, cy + 34))
                dd.rectangle([cx, cy, cx + CELL - 1, cy + CELL + 33], outline="#888")
                man.append((tag, lay, str(row["id"]), str(row["title"] or "")))
            p = f"{OUT}/{prefix}{si // (COLS * ROWS) + 1}.png"
            sh.save(p); sheets.append(p)
        return man, sheets

    # **编号用不可预测的随机码**：上一版用 L1-01 这种可预测编号时，判读者
    # 直接重放了上一批的 103 条回答（两次不同的图，逐格相同）。随机码让重放
    # 在结构上无法提交 —— 重放会带着上一批的编号，一眼可见。
    known = [e for k, v in sorted(pool.items()) if k != "_unknown" for e in v]
    m1, s1 = build(known, "L")
    m2, s2 = build(pool.get("_unknown", []), "U")
    with open(f"{OUT}/manifest.tsv", "w") as f:
        f.write("tag\tlayer\tpredicted\tdrawing_id\ttitle\n")
        for tag, lay, did, ti in m1 + m2:
            f.write(f"{tag}\t{lay}\t{classify_by_layer(lay) or '_unknown'}\t{did}\t{ti}\n")
    print(f"接触表 {len(s1) + len(s2)} 张 / 候选 {len(m1) + len(m2)} 个"
          f"（已分类 {len(m1)} · 判不出 {len(m2)}）")
    for p in s1 + s2:
        print(" ", p)
    await db.disconnect()

asyncio.run(main())
