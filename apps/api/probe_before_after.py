"""改前/改后逐图对比：真实尺寸判据到底删掉多少柱候选。

**同一进程、同一张图、只换一个函数**：把 `element_recognizer.min_area_rect`
换成返回轴对齐包围盒的版本，就精确复现改前行为 —— 因为该函数在识别器里
的唯一用途就是给多边形分支提供 (宽, 高)，而下游两个窗口检查对两者对称。
这样得到的差值可以完全归因于本次改动，不掺别的变量。

按图种分组统计 —— 落库柱的分布显示**柱配筋详图**这类详图贡献了大量候选
（单张 1202 个、96% 是三角形），与结构平面图不是一个总体，不能混着报。

抽样源默认取 `DRAWING_IDS`（逗号分隔或 @文件路径）。**默认应当传入
「已建场景里真正产出过柱的那 152 张图」** —— 在全部结构/建筑图里随机抽，
多数图一根柱都没有，样本效率极低（实测 15 分钟只跑出 5 张、其中 3 张
改前改后完全相同）。要量一道柱的闸，就得在出柱的图上量。
"""
import asyncio, collections, gc, json, os, random, time
import databases as databases_lib
import core.model3d.element_recognizer as er
from core.config import settings
from core.model3d.geometry_extractor import extract_pdf_geometry
from core.storage import get_file_bytes

OUT = os.environ.get("OUT", "/tmp/before_after.jsonl")
N_DRAWINGS = int(os.environ.get("N_DRAWINGS", "60"))
BUDGET_SEC = float(os.environ.get("BUDGET_SEC", "2400"))
_ids_arg = os.environ.get("DRAWING_IDS", "")
if _ids_arg.startswith("@"):
    _ids_arg = open(_ids_arg[1:]).read()
ONLY_IDS = [s.strip() for s in _ids_arg.replace("\n", ",").split(",") if s.strip()]
random.seed(20260924)

_new = er.min_area_rect


def _aabb_extent(poly):
    """改前口径：轴对齐包围盒的 (长, 短)。退化时不返回 None —— 改前不筛退化。"""
    xs = [p[0] for p in poly]; ys = [p[1] for p in poly]
    w, h = max(xs) - min(xs), max(ys) - min(ys)
    return max(w, h), min(w, h)


def _kind(title: str) -> str:
    t = title or ""
    if "详图" in t or "大样" in t:
        return "详图/大样"
    if "剖面" in t:
        return "剖面"
    if "平面图" in t:
        return "平面图"
    return "其他"


async def main():
    db = databases_lib.Database(settings.database_url); await db.connect()
    if ONLY_IDS:
        rows = list(await db.fetch_all(
            "SELECT id,title,discipline,file_key FROM drawings "
            "WHERE id::text = ANY(:ids) AND file_key IS NOT NULL",
            {"ids": ONLY_IDS}))
    else:
        rows = list(await db.fetch_all(
            "SELECT d.id,d.title,d.discipline,d.file_key FROM drawings d "
            "WHERE d.discipline IN ('structure','architecture') "
            "AND d.file_key IS NOT NULL"))
    random.shuffle(rows); rows = rows[:N_DRAWINGS]
    deadline = time.monotonic() + BUDGET_SEC
    agg = collections.defaultdict(lambda: [0, 0, 0])   # kind -> [图, 改前, 改后]
    n_ok = 0
    with open(OUT, "w") as fh:
        for row in rows:
            if time.monotonic() > deadline:
                print(f"[预算到点] 已扫 {n_ok} 张", flush=True); break
            did = str(row["id"])
            try:
                geom = extract_pdf_geometry(get_file_bytes(row["file_key"]))
                tr = await db.fetch_one("SELECT scale_m_pt FROM drawing_transform "
                                        "WHERE drawing_id=:d", {"d": did})
                sc = float(tr["scale_m_pt"]) if tr else None
                er.min_area_rect = _aabb_extent
                before = len(er.recognize(geom, row["discipline"], did,
                                          drawing_title=row["title"],
                                          scale_override=sc).columns)
                er.min_area_rect = _new
                after = len(er.recognize(geom, row["discipline"], did,
                                         drawing_title=row["title"],
                                         scale_override=sc).columns)
            except Exception as exc:
                er.min_area_rect = _new
                print(f"  跳过 {did}: {type(exc).__name__}", flush=True)
                continue
            n_ok += 1
            k = _kind(str(row["title"] or ""))
            a = agg[k]; a[0] += 1; a[1] += before; a[2] += after
            fh.write(json.dumps({
                "drawing_id": did, "title": str(row["title"] or ""),
                "discipline": str(row["discipline"]), "kind": k,
                "before": before, "after": after,
            }, ensure_ascii=False) + "\n")
            fh.flush(); gc.collect()
    print(f"\n扫描 {n_ok} 张", flush=True)
    tb = ta = 0
    for k, (n, b, a) in sorted(agg.items()):
        tb += b; ta += a
        d = b - a
        print(f"  {k:<10} 图 {n:>3} · 改前 {b:>6} → 改后 {a:>6} · 删 {d:>6} "
              f"({d/max(b,1):>6.1%})", flush=True)
    print(f"  {'合计':<10} 图 {n_ok:>3} · 改前 {tb:>6} → 改后 {ta:>6} · "
          f"删 {tb-ta:>6} ({(tb-ta)/max(tb,1):>6.1%})", flush=True)
    await db.disconnect()

asyncio.run(main())
