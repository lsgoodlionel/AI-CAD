"""探针：柱候选的**实际形状**分布 —— 三角形到底有没有进识别器。

## 为什么要重量一次

2026-08-28 的判读把「按三角形顶点数过滤」否掉了，理由是
「候选里三顶点多边形占 0%，标高三角没有以三角形的形式进入识别器」。
但提取链路显示这个 0% 可能是**计数口径**造成的：标高三角在 PDF 里由
3 条 `l` 线段画成，`_collect_pdf_drawings` 把每条线段的**两个端点**都
塞进 `path_points`，于是 `_add_poly` 收到的是 **6 个点**（端点两两重复），
顶点数恒不等于 3。若如此，「三顶点占 0%」测的是编码方式，不是形状。

本探针对同一批候选给出两个口径：
  raw    —— outline 原始点数（旧口径）
  uniq   —— **按位置去重后**的相异顶点数（新口径）
两者若分岔，旧结论即被证伪；若 uniq 仍无 3，则三角形确实没进来，
判据到此为止。

抽样源与柱定案金标准（`gold/columns_final_v1.json`）**同一population**：
平面图 · structure/architecture · 排除详图/大样/剖面。

**结论（并注意它的口径）**：本探针实测三角形占 **2.7%**（21 张图 767 个候选），
足以证伪「0%」；但**别把 2.7% 当成全库的数**。它排除了详图，而三角形的
最大来源恰恰是详图 —— 单张 `柱配筋详图（七）` 就贡献 1202 个候选、96% 是
三角形。全库口径见 `probe_scene_columns.py`：24717 根落库柱里 **55.1%**。
一个判据的分母是什么，决定了它的数字意味着什么。
"""
import asyncio, collections, gc, os, random, time
import databases as databases_lib
from core.config import settings
from core.model3d.element_recognizer import recognize
from core.model3d.geometry_extractor import extract_pdf_geometry
from core.storage import get_file_bytes

N_DRAWINGS = int(os.environ.get("N_DRAWINGS", "120"))
#: 墙钟预算（秒）。**到点就用手上的量出表** —— 宁可样本小，也不要跑满
#: 超时一张表都没有（`mk_col2.py` 吃过这个亏）。
BUDGET_SEC = float(os.environ.get("BUDGET_SEC", "420"))
#: 顶点合并容差（米）。图纸尺度下 1mm 内的两点是同一个角点。
EPS_M = 1e-3
random.seed(20260922)


def uniq_vertices(outline, eps=EPS_M):
    """按位置去重的相异顶点序列（环状，首尾也算重复）。"""
    pts = []
    for x, y in outline:
        if not any(abs(x - px) <= eps and abs(y - py) <= eps for px, py in pts):
            pts.append((float(x), float(y)))
    return pts


def bbox_aspect(outline):
    xs = [p[0] for p in outline]; ys = [p[1] for p in outline]
    w, h = max(xs) - min(xs), max(ys) - min(ys)
    if min(w, h) <= 0:
        return None
    return max(w, h) / min(w, h)


def is_axis_rect(pts, eps=EPS_M):
    """4 个相异顶点且构成轴对齐矩形 —— 矩形分支的指纹（bbox 化后的产物）。"""
    if len(pts) != 4:
        return False
    xs = sorted({round(p[0] / eps) for p in pts})
    ys = sorted({round(p[1] / eps) for p in pts})
    return len(xs) == 2 and len(ys) == 2


async def main():
    db = databases_lib.Database(settings.database_url); await db.connect()
    rows = list(await db.fetch_all(
        "SELECT d.id,d.title,d.discipline,d.file_key FROM drawings d "
        "WHERE d.title LIKE '%平面图%' "
        "AND d.discipline IN ('structure','architecture') "
        "AND d.title NOT LIKE '%详图%' AND d.title NOT LIKE '%大样%' "
        "AND d.title NOT LIKE '%剖面%'"))
    random.shuffle(rows)
    rows = rows[:N_DRAWINGS]

    raw_hist = collections.Counter()
    uniq_hist = collections.Counter()
    shape_hist = collections.Counter()
    tri_aspects, rect_aspects = [], []
    tri_examples = []
    n_ok = n_cols = 0
    deadline = time.monotonic() + BUDGET_SEC

    for row in rows:
        if time.monotonic() > deadline:
            print(f"[预算到点] 已扫 {n_ok} 张，停止取样")
            break
        did = str(row["id"])
        try:
            geom = extract_pdf_geometry(get_file_bytes(row["file_key"]))
            tr = await db.fetch_one("SELECT scale_m_pt FROM drawing_transform "
                                    "WHERE drawing_id=:d", {"d": did})
            fe = recognize(geom, row["discipline"], did, drawing_title=row["title"],
                           scale_override=float(tr["scale_m_pt"]) if tr else None)
        except Exception:
            continue
        n_ok += 1
        for c in fe.columns:
            o = c.get("outline") or []
            if len(o) < 3:
                continue
            n_cols += 1
            pts = uniq_vertices(o)
            raw_hist[len(o)] += 1
            uniq_hist[len(pts)] += 1
            asp = bbox_aspect(o)
            if len(pts) == 3:
                shape_hist["triangle"] += 1
                if asp: tri_aspects.append(asp)
                if len(tri_examples) < 12:
                    tri_examples.append((str(row["title"])[:30], len(o), pts))
            elif is_axis_rect(pts):
                shape_hist["axis_rect"] += 1
                if asp: rect_aspects.append(asp)
            else:
                shape_hist[f"other_{len(pts)}"] += 1
        gc.collect()

    def pct(n): return f"{n} ({n / max(n_cols,1):.1%})"
    print(f"扫描图纸 {n_ok}/{len(rows)} · 柱候选 {n_cols}")
    print("\n--- outline 原始点数（旧口径） ---")
    for k in sorted(raw_hist): print(f"  {k:>3} 点: {pct(raw_hist[k])}")
    print("\n--- 去重后相异顶点数（新口径） ---")
    for k in sorted(uniq_hist): print(f"  {k:>3} 顶点: {pct(uniq_hist[k])}")
    print("\n--- 形状分类 ---")
    for k, v in shape_hist.most_common(): print(f"  {k}: {pct(v)}")

    def stats(name, xs):
        if not xs: print(f"  {name}: 无样本"); return
        xs = sorted(xs); n = len(xs)
        print(f"  {name}: n={n} 中位 {xs[n//2]:.2f} "
              f"10分位 {xs[n//10]:.2f} 90分位 {xs[(n*9)//10]:.2f} "
              f"近方(<1.3) {sum(1 for x in xs if x < 1.3)/n:.0%}")
    print("\n--- bbox 长宽比（验证「标高三角 bbox 近方形」） ---")
    stats("三角形", tri_aspects); stats("轴对齐矩形", rect_aspects)
    print("\n--- 三角形样例 ---")
    for t, nraw, pts in tri_examples:
        print(f"  [{t}] raw={nraw} pts={[(round(x,3),round(y,3)) for x,y in pts]}")
    await db.disconnect()

asyncio.run(main())
