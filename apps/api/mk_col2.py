"""柱的定案复测 —— **验证刚落地的座椅闸**，并给最有争议的数字定案。

柱是用得最多的类（两工程 2.4 万个构件），也是数字最有争议的一个：
0.59（旧批）→ 0.22（新批）→ 68%（实体批）→ 22%（轮廓批）。
`CRITERIA.md#columns` 现已把「带叉方块算柱」「柱编号算柱」固定下来。

工作区里刚落地了**密排阵列闸**（`find_dense_array_flags`），
判据是「间距 ≈ 自身尺寸」—— 座椅/吸声板/铺装单元密排，真柱之间隔着一个跨度。
本批三组：

    kept    闸之后保留的柱      → 精确率
    dropped 被闸删掉的候选      → 删对了没有
    blank   空白对照            → 仪器是否有效

每张图最多取 2 格，避免 YOLO 批那种「一张图占 15 格」的集中。

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

WANT, N_DROP, N_BLANK, DPI = 20, 16, 8, 150
OUT, CTX_M = "/tmp/gpt_col2", 5.0
os.makedirs(OUT, exist_ok=True)
random.seed(20260922)


async def main():
    db = databases_lib.Database(settings.database_url); await db.connect()
    rows = await db.fetch_all(
        "SELECT d.id,d.title,d.discipline,d.file_key FROM drawings d "
        "WHERE d.title LIKE '%平面图%' "
        "AND d.discipline IN ('structure','architecture') "
        "AND d.title NOT LIKE '%详图%' AND d.title NOT LIKE '%大样%' "
        "AND d.title NOT LIKE '%剖面%'")
    rows = list(rows); random.shuffle(rows)
    # **扫描上限**：`dropped` 组要碰上有密排阵列的图，全库里稀少，
    # 不设上限可能扫几千张。到点就用手上的量出表，宁可组小也不空转。
    rows = rows[:80]   # 260 张跑不完 900 秒上限，一张表都没写出来
    picks, drops, blanks = [], [], []
    for row in rows:
        if (len(picks) >= WANT and len(drops) >= N_DROP
                and len(blanks) >= N_BLANK):
            break
        did = str(row["id"])
        try:
            geom = extract_pdf_geometry(get_file_bytes(row["file_key"]))
            tr = await db.fetch_one("SELECT scale_m_pt FROM drawing_transform "
                                    "WHERE drawing_id=:d", {"d": did})
            fe = recognize(geom, row["discipline"], did, drawing_title=row["title"],
                           scale_override=float(tr["scale_m_pt"]) if tr else None)
            doc = fitz.open(stream=get_file_bytes(row["file_key"]), filetype="pdf")
            page = doc[0]
        except Exception:
            continue
        def _crop_box(bb):
            """按构件包络裁剪并画**红框**（柱是块状，不是线状）。"""
            k2 = DPI / 72.0
            span = max(bb[2] - bb[0], bb[3] - bb[1])
            half = max(span * 6.0, 40.0) / 2
            cxp, cyp = (bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2
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
            dr.rectangle([(bb[0] - cl.x0) * k2, (bb[1] - cl.y0) * k2,
                          (bb[2] - cl.x0) * k2, (bb[3] - cl.y0) * k2],
                         outline=(255, 0, 0), width=4)
            return im

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

        def _box(el):
            o = el.get("outline") or []
            if len(o) < 3:
                return None
            px = [meters_to_page(mx, my, fe.scale, fe.origin_pt, fe.page_h)
                  for mx, my in o]
            xs = [p[0] for p in px]; ys = [p[1] for p in px]
            return (min(xs), min(ys), max(xs), max(ys))

        kept = [c for c in fe.columns if len(c.get("outline") or []) >= 3]
        dropped = [c for c in getattr(fe, "dense_arrays", [])
                   if len(c.get("outline") or []) >= 3]
        if fe.scale:
            # **每张图最多 2 格**：YOLO 批一张图占了 15 格，把座椅问题放大成了整批
            for el, bucket, cap in ((kept, picks, WANT), (dropped, drops, N_DROP)):
                if not el or len(bucket) >= cap:
                    continue
                for c in random.sample(el, min(2, len(el))):
                    bb = _box(c)
                    if bb is None:
                        continue
                    im = _crop_box(bb)
                    if im is not None:
                        bucket.append((row, im))
                    if len(bucket) >= cap:
                        break
        if fe.scale and len(blanks) < N_BLANK and not kept:
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
                    # **对照组必须和柱组同形**：先前空白画的是红线、柱画的是红框，
                    # 判读者能凭形状认出对照组，对照就失效了。改画同尺度的红框
                    # （0.6 米见方，典型柱截面）。
                    L = 0.6 / fe.scale
                    big = _crop_box((cx - L / 2, cy - L / 2, cx + L / 2, cy + L / 2))
                    if big is not None:
                        blanks.append((row, big))
                    break
        try:
            doc.close()
        except Exception:
            pass
        gc.collect()
    print(f"保留柱 {len(picks)} · 被闸删掉 {len(drops)} · 空白对照 {len(blanks)}")

    allp = ([(r, im, "kept") for r, im in picks]
            + [(r, im, "dropped") for r, im in drops]
            + [(r, im, "blank") for r, im in blanks])
    random.shuffle(allp)
    codes = make_codes(len(allp) + 20, seed=20260922)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 26)
    except Exception:
        font = ImageFont.load_default()
    CELL, COLS, ROWS = 400, 5, 4
    tiles = [(codes.pop(), grp, str(row["id"]),
              str(row["title"] or ""), im.resize((CELL, CELL)))
             for row, im, grp in allp]

    man, sheets = [], []
    for si in range(0, len(tiles), COLS * ROWS):
        chunk = tiles[si:si + COLS * ROWS]
        sh = Image.new("RGB", (CELL * COLS, (CELL + 34) * ROWS), "white")
        dd = ImageDraw.Draw(sh)
        for i, (tag, grp, did, ti, im) in enumerate(chunk):
            cx, cy = (i % COLS) * CELL, (i // COLS) * (CELL + 34)
            dd.text((cx + 8, cy + 6), tag, fill="black", font=font)
            sh.paste(im, (cx, cy + 34))
            dd.rectangle([cx, cy, cx + CELL - 1, cy + CELL + 33], outline="#888")
            man.append((tag, grp, did, ti.replace("\t", " ")))
        p = f"{OUT}/C{si // (COLS * ROWS) + 1}.png"
        sh.save(p); sheets.append(p)
    with open(f"{OUT}/manifest.tsv", "w") as f:
        f.write("tag\tgroup\tdrawing_id\ttitle\n")
        for x in man:
            f.write("\t".join(x) + "\n")
    print(f"接触表 {len(sheets)} 张 / {len(man)} 格 ·",
          dict(collections.Counter(x[1] for x in man)))
    for p in sheets:
        print(" ", p)
    await db.disconnect()

asyncio.run(main())
