"""删除量核验：把候选按**拟议判据**分成「会删」「会留」两组渲染出来。

项目教训（`MODELING_PIPELINE_BLUEPRINT.md` §7 与 MAX_BANDS=40 那次误杀
451 张核心平面图）：任何新闸在落地前必须量出**删掉多少、误伤多少**。
删除量能从转储算出来，**误伤只能看**。

拟议判据（`GATE` 环境变量选）：
  minrect —— 多边形候选的尺寸窗口与长宽比改用**最小面积外接矩形**
             而非轴对齐包围盒。不引入新阈值，沿用 `_COLUMN_SIZE`
             (0.2~1.5m) 与 `_COLUMN_MAX_ASPECT` (4.0)。

两组各出一张接触表：`gate_drop.png` 是**误伤清单**（里面出现真柱就是代价），
`gate_keep.png` 是**残留清单**（里面还剩多少标注类就是本闸的天花板）。

**这里的 `would_drop` 是近似，不是产品判据** —— 它把猜测路径的窗口
(0.2~1.5m) 套在所有候选上，而产品里「图层明确标注为柱」的候选走的是
`_is_plausible_column`（0.1~3.0m）。这个近似**当时救了一次**：它把结构
平面图上 0.18m 的真柱列进了删除清单，逼出了「只换量法、不换阈值」的
设计修正。要看产品实际删了什么，用 `probe_delta_sheet.py`（跑产品路径取差集）。
"""
import asyncio, math, os, random, time
import databases as databases_lib, fitz
from PIL import Image, ImageDraw, ImageFont
from core.config import settings
from core.model3d.element_recognizer import (
    _COLUMN_MAX_ASPECT, _COLUMN_SIZE, recognize,
)
from core.model3d.geometry_extractor import extract_pdf_geometry
from core.model3d.yolo_export import meters_to_page
from core.storage import get_file_bytes

OUT = os.environ.get("OUT", "/tmp/gate_sheet")
N_DRAWINGS = int(os.environ.get("N_DRAWINGS", "80"))
BUDGET_SEC = float(os.environ.get("BUDGET_SEC", "900"))
PER_GROUP = int(os.environ.get("PER_GROUP", "20"))
PER_DRAWING = int(os.environ.get("PER_DRAWING", "2"))
#: 只看这些图（逗号分隔的 drawing_id）。空则按 SQL 抽样。
ONLY_IDS = [s.strip() for s in os.environ.get("DRAWING_IDS", "").split(",") if s.strip()]
#: 只看三角形候选（去重后 3 个相异顶点）—— 用于核验「三角形是什么」。
TRI_ONLY = os.environ.get("TRI_ONLY", "") == "1"
DPI = 150
os.makedirs(OUT, exist_ok=True)
random.seed(20260923)


def convex_hull(pts):
    ps = sorted(set((round(x, 9), round(y, 9)) for x, y in pts))
    if len(ps) <= 2:
        return ps

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lo = []
    for p in ps:
        while len(lo) >= 2 and cross(lo[-2], lo[-1], p) <= 0:
            lo.pop()
        lo.append(p)
    up = []
    for p in reversed(ps):
        while len(up) >= 2 and cross(up[-2], up[-1], p) <= 0:
            up.pop()
        up.append(p)
    return lo[:-1] + up[:-1]


def min_area_rect(hull):
    if len(hull) < 3:
        return None
    best = None
    for i in range(len(hull)):
        ax, ay = hull[i]; bx, by = hull[(i + 1) % len(hull)]
        ex, ey = bx - ax, by - ay
        L = math.hypot(ex, ey)
        if L <= 0:
            continue
        ux, uy = ex / L, ey / L
        us = [p[0] * ux + p[1] * uy for p in hull]
        vs = [-p[0] * uy + p[1] * ux for p in hull]
        w, h = max(us) - min(us), max(vs) - min(vs)
        if best is None or w * h < best[0] * best[1]:
            best = (w, h)
    return (max(best), min(best)) if best else None


def would_drop(outline):
    """拟议判据下这个候选会不会被删。"""
    mr = min_area_rect(convex_hull(outline))
    if mr is None:
        return True, "退化"
    lo, hi = _COLUMN_SIZE
    long_m, short_m = mr
    if not (lo <= short_m <= hi and lo <= long_m <= hi):
        return True, f"实尺寸 {long_m:.2f}x{short_m:.2f}"
    if long_m / max(short_m, 1e-9) >= _COLUMN_MAX_ASPECT:
        return True, f"实长宽比 {long_m/max(short_m,1e-9):.1f}"
    return False, f"实 {long_m:.2f}x{short_m:.2f}"


async def main():
    db = databases_lib.Database(settings.database_url); await db.connect()
    rows = list(await db.fetch_all(
        "SELECT d.id,d.title,d.discipline,d.file_key FROM drawings d "
        "WHERE d.title LIKE '%平面图%' "
        "AND d.discipline IN ('structure','architecture') "
        "AND d.title NOT LIKE '%详图%' AND d.title NOT LIKE '%大样%' "
        "AND d.title NOT LIKE '%剖面%'"))
    if ONLY_IDS:
        rows = [r for r in rows if str(r["id"]) in ONLY_IDS] or list(
            await db.fetch_all(
                "SELECT id,title,discipline,file_key FROM drawings WHERE id::text = ANY(:ids)",
                {"ids": ONLY_IDS}))
    else:
        random.shuffle(rows); rows = rows[:N_DRAWINGS]
    groups = {"drop": [], "keep": []}
    deadline = time.monotonic() + BUDGET_SEC
    for row in rows:
        if time.monotonic() > deadline:
            print("[预算到点]", flush=True); break
        if all(len(v) >= PER_GROUP for v in groups.values()):
            break
        did = str(row["id"])
        try:
            geom = extract_pdf_geometry(get_file_bytes(row["file_key"]))
            tr = await db.fetch_one("SELECT scale_m_pt FROM drawing_transform "
                                    "WHERE drawing_id=:d", {"d": did})
            fe = recognize(geom, row["discipline"], did, drawing_title=row["title"],
                           scale_override=float(tr["scale_m_pt"]) if tr else None)
            if not fe.scale:
                continue
            doc = fitz.open(stream=get_file_bytes(row["file_key"]), filetype="pdf")
            page = doc[0]
        except Exception:
            continue

        def crop(bb):
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
            d = ImageDraw.Draw(im)
            d.rectangle([(bb[0] - cl.x0) * k2, (bb[1] - cl.y0) * k2,
                         (bb[2] - cl.x0) * k2, (bb[3] - cl.y0) * k2],
                        outline=(255, 0, 0), width=3)
            return im

        cands = [c for c in fe.columns if len(c.get("outline") or []) >= 3]
        random.shuffle(cands)
        taken = 0
        for c in cands:
            if taken >= PER_DRAWING:
                break
            o = c["outline"]
            if TRI_ONLY and len(set((round(x, 6), round(y, 6)) for x, y in o)) != 3:
                continue
            drop, why = would_drop(o)
            g = "drop" if drop else "keep"
            if len(groups[g]) >= PER_GROUP:
                continue
            pxy = [meters_to_page(mx, my, fe.scale, fe.origin_pt, fe.page_h)
                   for mx, my in o]
            pxs = [p[0] for p in pxy]; pys = [p[1] for p in pxy]
            im = crop((min(pxs), min(pys), max(pxs), max(pys)))
            if im is None:
                continue
            xs = [p[0] for p in o]; ys = [p[1] for p in o]
            groups[g].append((
                why,
                f"框{max(xs)-min(xs):.2f}x{max(ys)-min(ys):.2f}",
                str(row["title"] or "")[:16], im))
            taken += 1
        try:
            doc.close()
        except Exception:
            pass

    print({k: len(v) for k, v in groups.items()}, flush=True)
    try:
        font = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 19)
    except Exception:
        font = ImageFont.load_default()
    CELL, COLS = 330, 5
    for g, items in groups.items():
        if not items:
            continue
        rn = (len(items) + COLS - 1) // COLS
        sh = Image.new("RGB", (CELL * COLS, (CELL + 50) * rn), "white")
        dd = ImageDraw.Draw(sh)
        for i, (why, size, title, im) in enumerate(items):
            cx, cy = (i % COLS) * CELL, (i // COLS) * (CELL + 50)
            dd.text((cx + 6, cy + 3), f"{i+1}. {why}", fill="black", font=font)
            dd.text((cx + 6, cy + 25), f"{size} {title}", fill="#444", font=font)
            sh.paste(im.resize((CELL, CELL)), (cx, cy + 50))
            dd.rectangle([cx, cy, cx + CELL - 1, cy + CELL + 49], outline="#888")
        p = f"{OUT}/gate_{g}.png"
        sh.save(p); print(" ", p, flush=True)
    await db.disconnect()

asyncio.run(main())
