"""墙候选 → 带上下文的小图接触表。

墙是**带宽度的线段**（path 两点 + width），不像柱是方块，
所以把线段本身画成红线叠在图上，让判读者看「这条线是不是墙」。
"""
import asyncio, json, os, random, sys
import databases as databases_lib, fitz
from PIL import Image, ImageDraw, ImageFont
from core.config import settings
from core.model3d.element_recognizer import recognize
from core.model3d.geometry_extractor import extract_pdf_geometry
from core.model3d.yolo_export import meters_to_page
from core.storage import get_file_bytes

N = int(sys.argv[1]) if len(sys.argv) > 1 else 60
OUT = "/tmp/gpt_wall"
DPI = 150
CTX_M = 6.0        # 上下文半径（米）
random.seed(20260831)
os.makedirs(OUT, exist_ok=True)

async def main():
    db = databases_lib.Database(settings.database_url); await db.connect()
    r = await db.fetch_one(
        "SELECT scene FROM project_models WHERE project_id=:p "
        "ORDER BY version DESC LIMIT 1",
        {"p": "77777777-7777-7777-7777-777777777777"})
    sc = r["scene"] if isinstance(r["scene"], dict) else json.loads(r["scene"])
    by_src = {}
    for f in sc.get("floors", []):
        for w in (f.get("elements") or {}).get("walls") or []:
            p = w.get("path") or []
            if len(p) != 2 or w.get("scale_suspect"):
                continue
            by_src.setdefault(str(w.get("src")), []).append(w)
    srcs = [s for s, v in by_src.items() if len(v) >= 6]
    random.shuffle(srcs)
    print(f"有 ≥6 面墙且比例可信的图 {len(srcs)} 张")

    picks = []
    for did in srcs:
        if len(picks) >= N:
            break
        row = await db.fetch_one(
            "SELECT id,title,discipline,file_key FROM drawings WHERE id::text=:d",
            {"d": did})
        if not row:
            continue
        # **只取平面图**：详图/配筋图上的「墙」是断面大样，不是平面墙，
        # 判读者会被迫在两种语境间来回切换
        t = str(row["title"] or "")
        if "平面" not in t or any(k in t for k in ("详图", "大样", "配筋", "剖面")):
            continue
        try:
            geom = extract_pdf_geometry(get_file_bytes(row["file_key"]))
            t = await db.fetch_one(
                "SELECT scale_m_pt FROM drawing_transform WHERE drawing_id=:d", {"d": did})
            fe = recognize(geom, row["discipline"], did, drawing_title=row["title"],
                           scale_override=float(t["scale_m_pt"]) if t else None)
            page = fitz.open(stream=get_file_bytes(row["file_key"]), filetype="pdf")[0]
        except Exception:
            continue
        if not fe.scale:
            continue
        for w in random.sample(by_src[did], min(4, len(by_src[did]))):
            picks.append((row, fe, page, w))
            if len(picks) >= N:
                break
    print(f"取到 {len(picks)} 面墙候选")

    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 24)
    except Exception:
        font = ImageFont.load_default()
    CELL, COLS, ROWS = 380, 5, 4
    # **先渲染、再排版**：边渲染边排版时，被筛掉的格子会在接触表里留下空洞
    tiles = []
    for row, fe, page, w in picks:
        if True:
            (ax, ay), (bx, by) = w["path"]
            k2 = DPI / 72.0
            pa = meters_to_page(ax, ay, fe.scale, fe.origin_pt, fe.page_h)
            pb = meters_to_page(bx, by, fe.scale, fe.origin_pt, fe.page_h)
            cxp, cyp = (pa[0] + pb[0]) / 2, (pa[1] + pb[1]) / 2
            half = CTX_M / fe.scale / 2
            # **裁剪框必须夹在页面内**：越界时 PyMuPDF 返回全黑，
            # 实测 20 格里 5 格是黑的（W1-15、17~20）。
            pr = page.rect
            x0 = max(pr.x0, min(cxp - half, pr.x1 - 2 * half))
            y0 = max(pr.y0, min(cyp - half, pr.y1 - 2 * half))
            clip = fitz.Rect(x0, y0, min(x0 + 2 * half, pr.x1),
                             min(y0 + 2 * half, pr.y1))
            if clip.width < 8 or clip.height < 8:
                continue
            try:
                pix = page.get_pixmap(dpi=DPI, clip=clip)
                img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            except Exception:
                continue
            # 全黑/全白的格子不送 —— 判读者在空图里找不到东西
            probe = img.convert("L").resize((48, 48))
            dark = sum(1 for v in probe.getdata() if v < 60) / 2304
            ink = sum(1 for v in probe.getdata() if v < 200) / 2304
            if dark > 0.9 or ink < 0.02:
                continue
            sx = img.width / max(clip.width, 1e-6)
            dd = ImageDraw.Draw(img)
            dd.line([((pa[0] - clip.x0) * sx, (pa[1] - clip.y0) * sx),
                     ((pb[0] - clip.x0) * sx, (pb[1] - clip.y0) * sx)],
                    fill=(255, 0, 0), width=4)
            tiles.append((img.resize((CELL, CELL), Image.LANCZOS),
                          row["id"], round(w.get("width", 0), 3)))

    manifest = []
    per = COLS * ROWS
    for si in range((len(tiles) + per - 1) // per):
        chunk = tiles[si * per:(si + 1) * per]
        BAR = 44
        rows_n = (len(chunk) + COLS - 1) // COLS
        sheet = Image.new("RGB", (CELL * COLS, BAR + CELL * rows_n), "white")
        d0 = ImageDraw.Draw(sheet)
        d0.rectangle([0, 0, sheet.width - 1, BAR - 1], fill="#111")
        sid = f"W{si+1}"
        d0.text((12, 8), f"{sid}   {len(chunk)} 个墙候选  编号 {sid}-01 ~ {sid}-{len(chunk):02d}",
                fill="white", font=font)
        for k, (img, did, wid) in enumerate(chunk):
            img = img.copy()
            dd = ImageDraw.Draw(img)
            tag = f"{sid}-{k+1:02d}"
            dd.rectangle([0, 0, 100, 28], fill=(255, 0, 0))
            dd.text((5, 2), tag, fill="white", font=font)
            px, py = (k % COLS) * CELL, BAR + (k // COLS) * CELL
            sheet.paste(img, (px, py))
            d0.rectangle([px, py, px + CELL - 1, py + CELL - 1],
                         outline=(90, 90, 90), width=2)
            manifest.append(f"{tag}\t{did}\t{wid}")
        sheet.save(f"{OUT}/{sid}.png")
        print(f"  {sid}.png  {len(chunk)} 格")
    open(f"{OUT}/manifest.tsv", "w").write(
        "tag\tdrawing_id\twidth_m\n" + "\n".join(manifest) + "\n")
    await db.disconnect()
asyncio.run(main())
