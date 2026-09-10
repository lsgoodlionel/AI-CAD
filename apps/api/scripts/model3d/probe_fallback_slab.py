"""兜底板普查：量「面积最大的多边形」到底是什么。

**为什么要量**：`_find_slabs` 无图层命中时走兜底，按面积降序取前 N，
且「被已选中套住就跳过」。若最大那个是**图框**，它套住图上所有东西，
于是只剩一块、而那一块是纸不是楼。这些板经 `services/model_qto.py`
进算量，汇总进创效提案的 description，是三审审批人看的数字。

**A/B 同源**：改前/改后只差「兜底挑板时传不传图层」这一个参数，
其余（几何抽取、比例、面积口径）完全同一次调用的产物，不存在两棵
代码树对比时的无关差异。

**本探针不判对错，只报事实**：每张图面积 top-K 的多边形各自的
面积（米²）、占图幅的比例、点数、是否轴对齐矩形、图层名，以及图层
是否已被 `is_non_component_layer` 认出。判据要从这些分布里读出来，
不是先设一个面积上限再去凑。

口径与识别器**同源**：面积用包围盒（与 `_find_slabs` 的 `_area_m2`
一致，不是鞋带面积），比例用 `recognize()` 解出的 `result.scale`。

用法（容器内）：
    python -m scripts.model3d.probe_fallback_slab --limit 50 --out /tmp/x.json
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter


def _bbox(poly) -> tuple[float, float, float, float]:
    xs = [float(p[0]) for p in poly if len(p) >= 2]
    ys = [float(p[1]) for p in poly if len(p) >= 2]
    if not xs or not ys:
        return (0.0, 0.0, 0.0, 0.0)
    return (min(xs), min(ys), max(xs), max(ys))


def _is_axis_rect(poly, tol: float = 1.0) -> bool:
    """轮廓是否就是它自己的轴对齐包围盒（点全落在四条边上，且四角都占齐）。"""
    pts = [(float(p[0]), float(p[1])) for p in poly if len(p) >= 2]
    if len(pts) < 4:
        return False
    x0, y0, x1, y1 = _bbox(pts)
    if x1 - x0 < tol or y1 - y0 < tol:
        return False
    corners = {(round(x0), round(y0)), (round(x1), round(y0)),
               (round(x1), round(y1)), (round(x0), round(y1))}
    seen = set()
    for x, y in pts:
        on_v = abs(x - x0) <= tol or abs(x - x1) <= tol
        on_h = abs(y - y0) <= tol or abs(y - y1) <= tol
        if not (on_v or on_h):
            return False
        if on_v and on_h:
            seen.add((round(min(max(x, x0), x1)), round(min(max(y, y0), y1))))
    return len(seen) >= 3


def _fetch(limit: int, disciplines: tuple[str, ...], title_like: str) -> list[dict]:
    import asyncio
    import os

    import asyncpg

    dsn = os.getenv("DATABASE_URL", "")
    if not dsn:
        raise SystemExit("缺少 DATABASE_URL")

    async def _run() -> list[dict]:
        conn = await asyncpg.connect(dsn.replace("postgresql+asyncpg://", "postgresql://"))
        try:
            rows = await conn.fetch(
                "SELECT id::text AS id, drawing_no, title, discipline, file_key "
                "FROM drawings WHERE file_key IS NOT NULL "
                "AND discipline = ANY($1::text[]) AND title LIKE $2 "
                "ORDER BY id LIMIT $3",
                list(disciplines), title_like, limit,
            )
            return [dict(r) for r in rows]
        finally:
            await conn.close()

    return asyncio.run(_run())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--disciplines", default="structure,architecture")
    ap.add_argument("--title-like", default="%平面图%",
                    help="按标题筛图（view_type 不在库里，由 classifier 现算）")
    ap.add_argument("--top", type=int, default=10, help="每图报告面积前 K 个多边形")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    from core.model3d.element_recognizer import (
        _SLAB_MIN_AREA_M2, SLAB_BASIS_LARGEST_POLYGON,
        pick_fallback_slab_polygons, recognize)
    from core.model3d.geometry_extractor import (budget_for,
                                                 extract_pdf_geometry)
    from core.model3d.layer_conventions import (classify_by_layer,
                                                is_non_component_layer)
    from core.storage import get_file_bytes

    from services.drawing_view_classifier import classify_view_type

    rows = _fetch(args.limit,
                  tuple(d.strip() for d in args.disciplines.split(",")),
                  args.title_like)
    print(f"抽样图纸 {len(rows)} 张", flush=True)

    SLAB_T = 0.12          # `_SLAB_THICKNESS_M`，只用于把块数差换算成方量差
    per_drawing: list[dict] = []
    dropped_layers: Counter = Counter()
    ab_total = {"before": 0, "after": 0, "vol_before": 0.0, "vol_after": 0.0}
    layer_hits: Counter = Counter()   # 兜底路径上，最大那块的图层
    cover_hist: Counter = Counter()   # 最大那块占图幅的比例分档

    for idx, row in enumerate(rows, 1):
        try:
            data = get_file_bytes(row["file_key"])
            geom = extract_pdf_geometry(data)
            view = classify_view_type({**row, "geometry": geom}).view_type
            elems = recognize(geom, row["discipline"] or "structure", row["id"],
                              drawing_title=row["title"], view_type=view)
        except Exception as exc:  # noqa: BLE001 — 单图失败不打断普查
            print(f"  [{idx}] 跳过 {row['drawing_no']}: {exc}", flush=True)
            continue

        payload = elems.as_dict() if hasattr(elems, "as_dict") else elems
        slabs = payload.get("slabs") or []
        bases = [s.get("basis") for s in slabs]
        is_fallback = bool(bases) and all(b == SLAB_BASIS_LARGEST_POLYGON for b in bases)

        scale = float(payload.get("scale") or elems.scale)
        page_area_pt = float(geom.page_w) * float(geom.page_h)
        page_area_m2 = page_area_pt * scale * scale

        scored = []
        for i, poly in enumerate(geom.polys):
            x0, y0, x1, y1 = _bbox(poly)
            w_pt, h_pt = x1 - x0, y1 - y0
            area_m2 = (w_pt * scale) * (h_pt * scale)
            scored.append((area_m2, i, poly, w_pt * h_pt))
        scored.sort(key=lambda t: -t[0])

        tops = []
        for area_m2, i, poly, area_pt in scored[:args.top]:
            layer = geom.poly_layers[i] if i < len(geom.poly_layers) else ""
            block = geom.poly_blocks[i] if i < len(geom.poly_blocks) else ""
            tops.append({
                "area_m2": round(area_m2, 1),
                "cover": round(area_pt / page_area_pt, 4) if page_area_pt else 0.0,
                "pts": len(poly),
                "rect": _is_axis_rect(poly),
                "layer": str(layer or ""),
                "block": str(block or ""),
                "non_component": is_non_component_layer(layer),
                "class": classify_by_layer(layer, block or ""),
            })

        # ── A/B：兜底挑板，只切「传不传图层」这一个参数 ──────────────
        ab = None
        if is_fallback:
            polys_b = geom.polys[:budget_for("polys")]
            layers_b = geom.poly_layers[:budget_for("polys")]

            def _area(poly):
                x0, y0, x1, y1 = _bbox(poly)
                return ((x1 - x0) * scale) * ((y1 - y0) * scale)

            before = pick_fallback_slab_polygons(
                polys_b, area_of=_area, min_area=_SLAB_MIN_AREA_M2)
            after = pick_fallback_slab_polygons(
                polys_b, area_of=_area, min_area=_SLAB_MIN_AREA_M2,
                layers=layers_b)
            kept = {id(p) for p in after}
            dropped = []
            for poly in before:
                if id(poly) in kept:
                    continue
                try:
                    i = next(j for j, q in enumerate(polys_b) if q is poly)
                except StopIteration:
                    continue
                x0, y0, x1, y1 = _bbox(poly)
                dropped.append({
                    "area_m2": round(_area(poly), 1),
                    "cover": round(((x1 - x0) * (y1 - y0)) / page_area_pt, 4)
                             if page_area_pt else 0.0,
                    "pts": len(poly), "rect": _is_axis_rect(poly),
                    "layer": str(layers_b[i] if i < len(layers_b) else ""),
                })
            ab = {"before": len(before), "after": len(after),
                  "dropped": dropped,
                  "vol_before": round(sum(_area(p) for p in before) * SLAB_T, 1),
                  "vol_after": round(sum(_area(p) for p in after) * SLAB_T, 1)}
            for d in dropped:
                dropped_layers[d["layer"]] += 1
            ab_total["before"] += ab["before"]
            ab_total["after"] += ab["after"]
            ab_total["vol_before"] += ab["vol_before"]
            ab_total["vol_after"] += ab["vol_after"]

        axes = payload.get("axes") or {}
        xs = [p for _l, p in axes.get("x") or []]
        ys = [p for _l, p in axes.get("y") or []]
        axis_area_m2 = (((max(xs) - min(xs)) * scale) * ((max(ys) - min(ys)) * scale)
                        if len(xs) >= 2 and len(ys) >= 2 else None)

        rec = {
            "drawing_no": row["drawing_no"], "title": row["title"],
            "discipline": row["discipline"], "view_type": view, "scale": scale,
            "n_polys": len(geom.polys), "page_area_m2": round(page_area_m2, 1),
            "axis_area_m2": round(axis_area_m2, 1) if axis_area_m2 else None,
            "n_slabs": len(slabs), "fallback": is_fallback, "ab": ab,
            "bases": sorted(set(b for b in bases if b)),
            "top": tops,
        }
        per_drawing.append(rec)

        if is_fallback and tops:
            layer_hits[f"{tops[0]['layer']}|non_comp={tops[0]['non_component']}"] += 1
            c = tops[0]["cover"]
            bucket = ">=0.9" if c >= 0.9 else (">=0.7" if c >= 0.7 else
                                              (">=0.5" if c >= 0.5 else "<0.5"))
            cover_hist[bucket] += 1
        if idx % 10 == 0:
            print(f"  ...{idx}/{len(rows)}", flush=True)

    fb = [d for d in per_drawing if d["fallback"]]
    print(f"\n===== 兜底板普查 =====")
    print(f"成功识别 {len(per_drawing)} 张，其中全部走兜底 {len(fb)} 张")
    print(f"\n最大多边形占图幅比例（仅兜底图，{len(fb)} 张）：{dict(cover_hist)}")
    print(f"\n最大多边形的图层（仅兜底图）Top10：")
    for k, v in layer_hits.most_common(10):
        print(f"  {v:4d}  {k}")

    print(f"\n===== A/B：只切「兜底挑板传不传图层」=====")
    print(f"兜底板块数   改前 {ab_total['before']:5d}  →  改后 {ab_total['after']:5d}")
    print(f"折算混凝土量 改前 {ab_total['vol_before']:14,.1f} m³  →  "
          f"改后 {ab_total['vol_after']:12,.1f} m³   "
          f"（板厚 {SLAB_T}m，抽样内逐图相加，仅供改前改后对比）")
    print(f"\n被排除的多边形，其图层：")
    for k, v in dropped_layers.most_common(15):
        print(f"  {v:4d}  {k!r}")

    print(f"\n逐图（兜底，按最大块面积降序，前 20）：")
    for d in sorted(fb, key=lambda x: -(x["top"][0]["area_m2"] if x["top"] else 0))[:20]:
        t = d["top"][0] if d["top"] else {}
        print(f"  {t.get('area_m2', 0):10,.0f} m² cover={t.get('cover', 0):.2f} "
              f"rect={t.get('rect')} pts={t.get('pts')} "
              f"图幅={d['page_area_m2']:,.0f} 轴网={d['axis_area_m2']} "
              f"板数={d['n_slabs']} 兜底{(d.get('ab') or {}).get('before')}"
              f"→{(d.get('ab') or {}).get('after')} "
              f"layer={t.get('layer','')!r} | {d['drawing_no']} {d['title']}")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump({"per_drawing": per_drawing,
                       "layer_hits": dict(layer_hits),
                       "cover_hist": dict(cover_hist),
                       "dropped_layers": dict(dropped_layers),
                       "ab_total": ab_total},
                      fh, ensure_ascii=False, indent=2)
        print(f"\n明细写入 {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
