"""柱轮廓吻合度候选 → 接触表。

前面所有批次只测了「是不是柱」，没测「多大」——一根 600×600 被识别成
300×300，精确率算对，**算量差 4 倍**。而算量是这个平台的核心价值。

尺寸标注要读数字 = 字符转写 = 已知不可靠（33% 编造）。所以**不问尺寸，
问红框与图上那根柱的轮廓吻不吻合** —— 纯视觉比例判断，判读者可靠的那一档。

上下文取构件自身尺寸 ×6：太大看不清边界，太小没有参照。

**从当前代码的输出抽样，不读存量场景**。第一版读了 `project_models.scene`，
抽出 4×11 mm 的「柱」—— 而识别器里明明有 `_COLUMN_ABSURD_MIN_M = 0.1`。
用现在的代码重跑同样 4 张图：超小柱 **0 个**。存量场景是旧代码的产物。
实测存量比当前代码多 **46%（metro）/ 82%（sgoh）** 的柱。

方法论：**金标准要针对当前代码的输出，不是数据库里的存量。**
"""
import asyncio, collections, json, os, random, string
import databases as databases_lib, fitz
from PIL import Image, ImageDraw, ImageFont
from core.config import settings
from core.model3d.element_recognizer import recognize
from core.model3d.geometry_extractor import extract_pdf_geometry
from core.model3d.yolo_export import meters_to_page
from core.storage import get_file_bytes

WANT = 60
OUT, DPI = "/tmp/gpt_size", 200
CTX_RATIO, MIN_HALF_PT = 6.0, 40.0
PROJECTS = {"metro": "77777777-7777-7777-7777-777777777777",
            "sgoh": "9188e163-c684-415e-a4ec-08f208273eff"}
_CODE_ALPHABET = "".join(c for c in string.ascii_uppercase + string.digits
                         if c not in "IO01S5Z")
random.seed(20260909)
os.makedirs(OUT, exist_ok=True)


async def main():
    db = databases_lib.Database(settings.database_url); await db.connect()
    rows = await db.fetch_all(
        "SELECT d.id,d.title,d.discipline,d.file_key,p.name proj "
        "FROM drawings d JOIN projects p ON p.id=d.project_id "
        "WHERE d.project_id::text = ANY(:p) AND d.title IS NOT NULL",
        {"p": list(PROJECTS.values())})
    rows = list(rows); random.shuffle(rows)
    print(f"候选图纸 {len(rows)} 张，逐张跑当前识别器")

    picks = []
    for row in rows:
        if len(picks) >= WANT:
            break
        did = str(row["id"])
        ti = str(row["title"] or "")
        # 只取平面图：柱在平面上是方块，在剖面/详图上是断面大样，判读语境不同
        if "平面" not in ti or any(k in ti for k in ("详图", "大样", "剖面", "系统")):
            continue
        try:
            geom = extract_pdf_geometry(get_file_bytes(row["file_key"]))
            tr = await db.fetch_one("SELECT scale_m_pt FROM drawing_transform "
                                    "WHERE drawing_id=:d", {"d": did})
            fe = recognize(geom, row["discipline"], did, drawing_title=row["title"],
                           scale_override=float(tr["scale_m_pt"]) if tr else None)
            page = fitz.open(stream=get_file_bytes(row["file_key"]), filetype="pdf")[0]
        except Exception:
            continue
        cols = [c for c in fe.columns if len(c.get("outline") or []) >= 3]
        if not fe.scale or len(cols) < 4:
            continue
        nm = "metro" if "轨道" in str(row["proj"]) else "sgoh"
        for c in random.sample(cols, 3):
            picks.append((nm, row, fe, page, c))
            if len(picks) >= WANT:
                break
    print(f"取到 {len(picks)} 根柱")

    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 26)
    except Exception:
        font = ImageFont.load_default()
    CELL, COLS, ROWS = 400, 5, 4
    codes = set()
    while len(codes) < 300:
        codes.add("".join(random.choice(_CODE_ALPHABET) for _ in range(4)))
    codes = sorted(codes); random.shuffle(codes)

    tiles = []
    for nm, row, fe, page, c in picks:
        out = c["outline"]
        xs = [p[0] for p in out]; ys = [p[1] for p in out]
        w_m, h_m = max(xs) - min(xs), max(ys) - min(ys)
        cxp, cyp = meters_to_page((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2,
                                  fe.scale, fe.origin_pt, fe.page_h)
        half = max(max(w_m, h_m) * CTX_RATIO / fe.scale, MIN_HALF_PT) / 2
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
        px = []
        for mx, my in out:
            a, b = meters_to_page(mx, my, fe.scale, fe.origin_pt, fe.page_h)
            px.append(((a - clip.x0) * k2, (b - clip.y0) * k2))
        d.line(px + [px[0]], fill=(255, 0, 0), width=4)
        tiles.append((codes.pop(), nm, row, c, w_m, h_m, img.resize((CELL, CELL))))

    man, sheets = [], []
    for si in range(0, len(tiles), COLS * ROWS):
        chunk = tiles[si:si + COLS * ROWS]
        sh = Image.new("RGB", (CELL * COLS, (CELL + 36) * ROWS), "white")
        dd = ImageDraw.Draw(sh)
        for i, (tag, nm, row, c, w_m, h_m, img) in enumerate(chunk):
            cx, cy = (i % COLS) * CELL, (i // COLS) * (CELL + 36)
            dd.text((cx + 8, cy + 6), tag, fill="black", font=font)
            sh.paste(img, (cx, cy + 36))
            dd.rectangle([cx, cy, cx + CELL - 1, cy + CELL + 35], outline="#888")
            man.append((tag, nm, str(c.get("src")), str(row["title"] or ""),
                        f"{w_m*1000:.0f}", f"{h_m*1000:.0f}"))
        p = f"{OUT}/C{si // (COLS * ROWS) + 1}.png"
        sh.save(p); sheets.append(p)
    with open(f"{OUT}/manifest.tsv", "w") as f:
        f.write("tag\tproject\tdrawing_id\ttitle\tw_mm\th_mm\n")
        for x in man:
            f.write("\t".join(x) + "\n")
    print(f"接触表 {len(sheets)} 张 / {len(man)} 根柱")
    ws = sorted(float(x[4]) for x in man)
    print(f"识别宽度 中位 {ws[len(ws)//2]:.0f} mm · 最小 {ws[0]:.0f} · 最大 {ws[-1]:.0f}")
    for p in sheets:
        print(" ", p)
    await db.disconnect()

asyncio.run(main())
