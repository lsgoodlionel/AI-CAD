"""标注过滤器回测：量删除量，并出**接触表**供独立判读量误伤。

两件事一次做完：

1. 全样本统计 —— 每张图的柱候选里，判据标了多少、按 reason 分账；
2. 抽样出图 —— 被删组 / 保留组 / 空白对照三组混发、洗牌、四位码编号，
   判读者（含我自己）只看图不看来路，判完再对 manifest。

对照组沿用柱定案批（`mk_col2.py`）的做法：在墨迹稀疏处画**同样尺度的红框**，
形状与被删组一致，判读者无法凭形状认出对照组。

用法（容器内）：
    python scripts/model3d/annotation_backtest.py [图纸数] [输出目录]
"""
from __future__ import annotations

import asyncio
import collections
import gc
import json
import os
import random
import sys

import databases as databases_lib
import fitz
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.getcwd())

from core.config import settings  # noqa: E402
from core.model3d.annotation_filter import find_annotation_flags  # noqa: E402
from core.model3d.element_recognizer import recognize  # noqa: E402
from core.model3d.geometry_extractor import extract_pdf_geometry  # noqa: E402
from core.model3d.gold.batch_codes import make_codes  # noqa: E402
from core.model3d.yolo_export import meters_to_page  # noqa: E402
from core.storage import get_file_bytes  # noqa: E402

N_DRAWINGS = int(sys.argv[1]) if len(sys.argv) > 1 else 60
OUT = sys.argv[2] if len(sys.argv) > 2 else "/tmp/annot_bt"
N_DROP, N_KEEP, N_BLANK, DPI = 24, 16, 10, 150
os.makedirs(OUT, exist_ok=True)
random.seed(20260908)


def _crop_box(page, bb, dpi=DPI):
    """按候选包络裁剪并画红框；上下文取包络的 6 倍。"""
    k = dpi / 72.0
    span = max(bb[2] - bb[0], bb[3] - bb[1])
    half = max(span * 6.0, 40.0) / 2
    cx, cy = (bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2
    pr = page.rect
    gx = max(pr.x0, min(cx - half, pr.x1 - 2 * half))
    gy = max(pr.y0, min(cy - half, pr.y1 - 2 * half))
    clip = fitz.Rect(gx, gy, min(gx + 2 * half, pr.x1), min(gy + 2 * half, pr.y1))
    if clip.width < 8 or clip.height < 8:
        return None
    try:
        px = page.get_pixmap(dpi=dpi, clip=clip)
        im = Image.frombytes("RGB", (px.width, px.height), px.samples)
    except Exception:  # noqa: BLE001
        return None
    dr = ImageDraw.Draw(im)
    dr.rectangle([(bb[0] - clip.x0) * k, (bb[1] - clip.y0) * k,
                  (bb[2] - clip.x0) * k, (bb[3] - clip.y0) * k],
                 outline=(255, 0, 0), width=4)
    return im


async def main() -> None:
    db = databases_lib.Database(settings.database_url)
    await db.connect()
    rows = list(await db.fetch_all(
        "SELECT d.id,d.title,d.discipline,d.file_key FROM drawings d "
        "WHERE d.title LIKE '%平面图%' "
        "AND d.discipline IN ('structure','architecture') "
        "AND d.title NOT LIKE '%详图%' AND d.title NOT LIKE '%大样%' "
        "AND d.title NOT LIKE '%剖面%'"))
    random.shuffle(rows)

    stats = {"drawings": 0, "candidates": 0, "flagged": 0,
             "by_reason": collections.Counter(), "per_drawing": []}
    drops, keeps, blanks = [], [], []
    done = 0
    for row in rows:
        if done >= N_DRAWINGS:
            break
        did = str(row["id"])
        try:
            geom = extract_pdf_geometry(get_file_bytes(row["file_key"]))
            tr = await db.fetch_one(
                "SELECT scale_m_pt FROM drawing_transform WHERE drawing_id=:d",
                {"d": did})
            fe = recognize(geom, row["discipline"], did, drawing_title=row["title"],
                           scale_override=float(tr["scale_m_pt"]) if tr else None)
            doc = fitz.open(stream=get_file_bytes(row["file_key"]), filetype="pdf")
            page = doc[0]
        except Exception as exc:  # noqa: BLE001
            print("skip", did, exc)
            continue
        done += 1
        cands = [c for c in fe.columns if len(c.get("outline") or []) >= 3]
        flags = find_annotation_flags(cands)
        stats["drawings"] += 1
        stats["candidates"] += len(cands)
        stats["flagged"] += sum(1 for f in flags if f)
        for f in flags:
            if f:
                stats["by_reason"][f.reason] += 1
        stats["per_drawing"].append({
            "drawing_id": did, "title": row["title"],
            "discipline": row["discipline"],
            "n": len(cands), "flagged": sum(1 for f in flags if f)})

        def _bb(el):
            o = el.get("outline") or []
            px = [meters_to_page(mx, my, fe.scale, fe.origin_pt, fe.page_h)
                  for mx, my in o]
            xs = [p[0] for p in px]
            ys = [p[1] for p in px]
            return (min(xs), min(ys), max(xs), max(ys))

        flagged = [(c, f) for c, f in zip(cands, flags) if f]
        kept = [c for c, f in zip(cands, flags) if not f]
        if fe.scale:
            # **每张图最多 2 格**，避免单图主导整批（柱定案批的纪律）
            for c, f in random.sample(flagged, min(2, len(flagged))):
                if len(drops) >= N_DROP:
                    break
                im = _crop_box(page, _bb(c))
                if im is not None:
                    drops.append((row, im, f.reason))
            for c in random.sample(kept, min(2, len(kept))):
                if len(keeps) >= N_KEEP:
                    break
                im = _crop_box(page, _bb(c))
                if im is not None:
                    keeps.append((row, im, "-"))
            if len(blanks) < N_BLANK:
                pr = page.rect
                for _ in range(10):
                    cx = random.uniform(pr.x0 + 0.15 * pr.width, pr.x0 + 0.7 * pr.width)
                    cy = random.uniform(pr.y0 + 0.15 * pr.height, pr.y0 + 0.7 * pr.height)
                    half = 2.5 / fe.scale
                    clip = fitz.Rect(cx - half, cy - half, cx + half, cy + half)
                    try:
                        pix = page.get_pixmap(dpi=60, clip=clip)
                        im0 = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                    except Exception:  # noqa: BLE001
                        continue
                    ink = sum(1 for p in im0.convert("L").getdata() if p < 200)
                    if 0 < ink / max(im0.width * im0.height, 1) < 0.02:
                        side = 0.6 / fe.scale
                        big = _crop_box(page, (cx - side / 2, cy - side / 2,
                                               cx + side / 2, cy + side / 2))
                        if big is not None:
                            blanks.append((row, big, "blank"))
                        break
        try:
            doc.close()
        except Exception:  # noqa: BLE001
            pass
        gc.collect()

    with open(f"{OUT}/stats.json", "w") as fh:
        json.dump({**stats, "by_reason": dict(stats["by_reason"])}, fh,
                  ensure_ascii=False, indent=1)
    print(f"图 {stats['drawings']} · 候选 {stats['candidates']} · "
          f"标记 {stats['flagged']} "
          f"({stats['flagged'] / max(stats['candidates'], 1):.1%}) · "
          f"{dict(stats['by_reason'])}")

    allp = ([(r, im, "dropped", why) for r, im, why in drops]
            + [(r, im, "kept", why) for r, im, why in keeps]
            + [(r, im, "blank", why) for r, im, why in blanks])
    random.shuffle(allp)
    codes = make_codes(len(allp) + 20, seed=20260908)
    try:
        font = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 26)
    except Exception:  # noqa: BLE001
        font = ImageFont.load_default()
    CELL, COLS, ROWS = 400, 5, 4
    tiles = [(codes.pop(), grp, why, str(r["id"]), str(r["title"] or ""),
              im.resize((CELL, CELL))) for r, im, grp, why in allp]
    man, sheets = [], []
    for si in range(0, len(tiles), COLS * ROWS):
        chunk = tiles[si:si + COLS * ROWS]
        sheet = Image.new("RGB", (CELL * COLS, (CELL + 34) * ROWS), "white")
        dd = ImageDraw.Draw(sheet)
        for i, (tag, grp, why, did, title, im) in enumerate(chunk):
            cx, cy = (i % COLS) * CELL, (i // COLS) * (CELL + 34)
            dd.text((cx + 8, cy + 6), tag, fill="black", font=font)
            sheet.paste(im, (cx, cy + 34))
            dd.rectangle([cx, cy, cx + CELL - 1, cy + CELL + 33], outline="#888")
            man.append((tag, grp, why, did, title.replace("\t", " ")))
        path = f"{OUT}/A{si // (COLS * ROWS) + 1}.png"
        sheet.save(path)
        sheets.append(path)
    with open(f"{OUT}/manifest.tsv", "w") as fh:
        fh.write("tag\tgroup\treason\tdrawing_id\ttitle\n")
        for x in man:
            fh.write("\t".join(x) + "\n")
    print(f"接触表 {len(sheets)} 张 / {len(man)} 格 ·",
          dict(collections.Counter(x[1] for x in man)))
    for p in sheets:
        print(" ", p)
    await db.disconnect()


asyncio.run(main())
