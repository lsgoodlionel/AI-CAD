"""梁的复测 —— 四个主要结构类里最后一个没在固定判据下测过的。

柱 12%（定案）· 墙 57%（复测）· 管线 ~0%（有把握口径）· **梁 0.56（待复测）**。

梁还有一条悬着的发现：**误检按图纸聚集**（6 张图 100% 全错，与存库比例
1:30、置信 1.00 相关）。而比例批实测**只有 30% 站得住**，且置信度携带负信息
—— 这两件事可能是同一件。所以本批把每格的 `scale_m_pt` 与 `confidence`
一并记进 manifest，回来后能交叉验证「梁的误检是否跟着比例走」。

判据逐字沿用第一版（`gold_sheets/beam/BATCH_BEAM.txt`），
抽样改为当前代码，沿用已三次验证的空白对照。

（原管线批注释保留于下，设计同源。）
管线复测 —— **验证我自己发出去的修复**。

管线原测 **0/58**（58 个候选无一是管线：墙 30 · 结构线 21 · 标注线 7）。
根因是 `_find_pipes` 唯一判据是「够长且不是轴线」，`classify_system` 只用来
贴标签、从不用来排除。此后给它加了「标注层 + 别类层」双闸（与柱对称），
实测删掉 41.2% 候选 —— **但从没复测过精确率**。

若仍是 0，说明闸没用；若有提升，这是第一次证明某个修复真的移动了数字。

判据逐字沿用第一版（`gold_sheets/mep/BATCH_MEP.txt` 的管线段），
抽样改为当前代码逐张 `recognize()`，并沿用墙那批完美通过的**空白对照**。

（原墙批注释保留于下，设计同源。）
墙候选（第二版）—— 判据逐字沿用第一版，只把抽样源换成**当前代码**。

墙的 0.70 是所有构件类里最好的数字，**而它是在 `CRITERIA.md` 建立之前测的**，
且抽样取自存量场景。柱的数字在不同判据下摆动过 0.59 → 0.22 → 68% → 22%，
所以「最好的那个数字能不能扛住固定判据 + 当前代码」值得测。

**判据逐字不变**（从第一版 BATCH_WALL.txt 抄，已固化进 CRITERIA.md#walls），
只改抽样源 —— 差异就能归因于抽样源，又是一次单变量对比。

**对照组构造上就干净**：在**墨迹稀疏处**画同样的红线。判读者若对空白也答
「墙」，仪器当场作废，不必等分析。
"""
import asyncio, collections, gc, os, random
import databases as databases_lib, fitz
from PIL import Image, ImageDraw, ImageFont
from core.config import settings
from core.model3d.element_recognizer import recognize
from core.model3d.geometry_extractor import extract_pdf_geometry
from core.model3d.gold.batch_codes import make_codes
from core.model3d.yolo_export import meters_to_page
from core.storage import get_file_bytes

WANT, N_BLANK, DPI = 34, 12, 150
OUT, CTX_M = "/tmp/gpt_beam2", 7.0
os.makedirs(OUT, exist_ok=True)
random.seed(20260923)


async def main():
    db = databases_lib.Database(settings.database_url); await db.connect()
    rows = await db.fetch_all(
        "SELECT d.id,d.title,d.discipline,d.file_key FROM drawings d "
        "WHERE d.title LIKE '%平面图%' AND d.discipline = 'structure' "
        "AND d.title NOT LIKE '%详图%' AND d.title NOT LIKE '%大样%' "
        "AND d.title NOT LIKE '%剖面%'")
    rows = list(rows); random.shuffle(rows); rows = rows[:45]   # 后台跑，不受超时限制；n=14 的误差带太宽
    rows = list(rows); random.shuffle(rows)
    picks, blanks = [], []
    for _i, row in enumerate(rows, 1):
        if len(picks) >= WANT and len(blanks) >= N_BLANK:
            break
        did = str(row["id"])
        print(f"  [{_i}/{len(rows)}] {str(row['title'])[:26]}", flush=True)
        try:
            geom = extract_pdf_geometry(get_file_bytes(row["file_key"]))
            tr = await db.fetch_one("SELECT scale_m_pt, confidence FROM "
                                    "drawing_transform WHERE drawing_id=:d",
                                    {"d": did})
            _sc = f"{float(tr['scale_m_pt']):.5f}" if tr else "?"
            _cf = f"{float(tr['confidence']):.2f}" if tr else "?"
            fe = recognize(geom, row["discipline"], did, drawing_title=row["title"],
                           scale_override=float(tr["scale_m_pt"]) if tr else None)
            doc = fitz.open(stream=get_file_bytes(row["file_key"]), filetype="pdf")
            page = doc[0]
        except Exception:
            continue
        def _crop(path_pt, is_blank):
            """立刻裁图并画线 —— **doc 用完马上关**。
            把 doc/page 留到第二轮会让几十个文档同时开着，进程在 glibc 层崩
            （墙那批 40 个文档侥幸没崩，机电图更大，第一次就崩了）。"""
            k2 = DPI / 72.0
            pa, pb = path_pt
            cxp, cyp = (pa[0] + pb[0]) / 2, (pa[1] + pb[1]) / 2
            half = CTX_M / fe.scale / 2
            pr = page.rect
            gx = max(pr.x0, min(cxp - half, pr.x1 - 2 * half))
            gy = max(pr.y0, min(cyp - half, pr.y1 - 2 * half))
            cl = fitz.Rect(gx, gy, min(gx + 2 * half, pr.x1), min(gy + 2 * half, pr.y1))
            if cl.width < 8 or cl.height < 8:
                return None
            try:
                px = page.get_pixmap(dpi=DPI, clip=cl)
                im = Image.frombytes("RGB", (px.width, px.height), px.samples)
            except Exception:
                return None
            dr = ImageDraw.Draw(im)
            dr.line([((pa[0] - cl.x0) * k2, (pa[1] - cl.y0) * k2),
                     ((pb[0] - cl.x0) * k2, (pb[1] - cl.y0) * k2)],
                    fill=(255, 0, 0), width=4)
            return im

        ws = [w for w in fe.beams if len(w.get("path") or []) == 2]
        if fe.scale and ws and len(picks) < WANT:
            for w in random.sample(ws, min(3, len(ws))):
                (ax, ay), (bx, by) = w["path"]
                pa = meters_to_page(ax, ay, fe.scale, fe.origin_pt, fe.page_h)
                pb = meters_to_page(bx, by, fe.scale, fe.origin_pt, fe.page_h)
                im = _crop((pa, pb), False)
                if im is not None:
                    picks.append((row, im, _sc, _cf))
                if len(picks) >= WANT:
                    break
        elif fe.scale and len(blanks) < N_BLANK:
            # 对照：页面上找一处墨迹稀疏的地方，画同样长度的红线
            pr = page.rect
            for _ in range(12):
                cx = random.uniform(pr.x0 + 0.15 * pr.width, pr.x0 + 0.7 * pr.width)
                cy = random.uniform(pr.y0 + 0.15 * pr.height, pr.y0 + 0.7 * pr.height)
                half = CTX_M / fe.scale / 2
                clip = fitz.Rect(cx - half, cy - half, cx + half, cy + half)
                try:
                    pix = page.get_pixmap(dpi=60, clip=clip)
                    im = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                except Exception:
                    continue
                ink = sum(1 for p in im.convert("L").getdata() if p < 200)
                if 0 < ink / max(im.width * im.height, 1) < 0.02:   # 稀疏但非全白
                    L = 2.0 / fe.scale
                    big = _crop(((cx - L / 2, cy), (cx + L / 2, cy)), True)
                    if big is not None:
                        blanks.append((row, big, _sc, _cf))
                    break
        try:
            doc.close()
        except Exception:
            pass
        gc.collect()
    print(f"梁候选 {len(picks)} · 空白对照 {len(blanks)}")

    allp = ([(r, im, sc, cf, False) for r, im, sc, cf in picks]
            + [(r, im, sc, cf, True) for r, im, sc, cf in blanks])
    random.shuffle(allp)
    codes = make_codes(len(allp) + 20, seed=20260923)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 26)
    except Exception:
        font = ImageFont.load_default()
    CELL, COLS, ROWS = 400, 5, 4
    tiles = [(codes.pop(), "blank" if is_blank else "beam", str(row["id"]),
              sc, cf, str(row["title"] or ""), im.resize((CELL, CELL)))
             for row, im, sc, cf, is_blank in allp]

    man, sheets = [], []
    for si in range(0, len(tiles), COLS * ROWS):
        chunk = tiles[si:si + COLS * ROWS]
        sh = Image.new("RGB", (CELL * COLS, (CELL + 34) * ROWS), "white")
        dd = ImageDraw.Draw(sh)
        for i, (tag, grp, did, sc, cf, ti, im) in enumerate(chunk):
            cx, cy = (i % COLS) * CELL, (i // COLS) * (CELL + 34)
            dd.text((cx + 8, cy + 6), tag, fill="black", font=font)
            sh.paste(im, (cx, cy + 34))
            dd.rectangle([cx, cy, cx + CELL - 1, cy + CELL + 33], outline="#888")
            man.append((tag, grp, did, sc, cf, ti.replace("\t", " ")))
        p = f"{OUT}/B{si // (COLS * ROWS) + 1}.png"
        sh.save(p); sheets.append(p)
    with open(f"{OUT}/manifest.tsv", "w") as f:
        f.write("tag\tgroup\tdrawing_id\tscale_m_pt\tconfidence\ttitle\n")
        for x in man:
            f.write("\t".join(x) + "\n")
    print(f"接触表 {len(sheets)} 张 / {len(man)} 格 ·",
          dict(collections.Counter(x[1] for x in man)))
    for p in sheets:
        print(" ", p)
    await db.disconnect()

asyncio.run(main())
