"""柱轮廓环序普查：量「鞋带面积为 0 / 远小于包围盒」的柱占比及其算量影响。

**为什么单独量这个**：`services/model_qto.py:_column_quantity` 用
`_polygon_area(outline)` 乘层高得混凝土体积，用 `_polygon_perimeter`
得模板面积。轮廓若是自交环（bowtie），鞋带面积恰好为 0 —— 该柱混凝土
量为 0、模板面积失真，而算量经 `/model/quantities/to-proposal` 喂进
创效提案，是要走三审的经济数字。

同一份代码、同一批图纸，只切换 `--no-repair` 开关取「改前/改后」两个数，
避免两棵代码树对比时混入无关差异。算量口径直接调下游
`services.model_qto.compute_quantities`，不自己另算一遍。

**这不是识别准确率**，与 `data/model3d/gold/CRITERIA.md` 的金标准判读无关：
那里问的是「这个框算不算柱」（人工判读、有判据之争），这里问的是
「识别器已经吐出来的这个轮廓，环序在几何上是否成立」—— 纯确定性、
无判读、无阈值（鞋带面积是否**恰好**为 0）。两组数字不可混谈。

用法（容器内）：
    python -m scripts.model3d.column_outline_census --limit 200
    python -m scripts.model3d.column_outline_census --limit 200 --no-repair
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter

def _shoelace(outline) -> float:
    if not outline or len(outline) < 3:
        return 0.0
    total = 0.0
    n = len(outline)
    for i in range(n):
        x0, y0 = float(outline[i][0]), float(outline[i][1])
        x1, y1 = float(outline[(i + 1) % n][0]), float(outline[(i + 1) % n][1])
        total += x0 * y1 - x1 * y0
    return abs(total) / 2.0


def _bbox_area(outline) -> tuple[float, float, float]:
    xs = [float(p[0]) for p in outline]
    ys = [float(p[1]) for p in outline]
    w, h = max(xs) - min(xs), max(ys) - min(ys)
    return w, h, w * h


def _fetch(limit: int, disciplines: tuple[str, ...]) -> list[dict]:
    """直连 PG 抽样图纸。用 asyncpg 与 `export_annotations.py` 同构。"""
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
                "SELECT id::text AS id, project_id::text AS project_id, drawing_no, "
                "title, discipline, file_key FROM drawings "
                "WHERE file_key IS NOT NULL AND discipline = ANY($1::text[]) "
                "ORDER BY id LIMIT $2",
                list(disciplines), limit,
            )
            return [dict(r) for r in rows]
        finally:
            await conn.close()

    return asyncio.run(_run())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--disciplines", default="structure,architecture")
    ap.add_argument("--out", default="")
    ap.add_argument("--no-repair", action="store_true",
                    help="停用环序修复，量「改前」基线")
    args = ap.parse_args()

    if args.no_repair:
        # 把修复退化为恒等：同一条代码路径，只有这一处不同
        import core.model3d.element_recognizer as _er
        _er.repair_ring = lambda ring: ring

    from core.model3d.element_recognizer import recognize
    from core.model3d.geometry_extractor import extract_pdf_geometry
    from core.storage import get_file_bytes

    rows = _fetch(args.limit, tuple(d.strip() for d in args.disciplines.split(",")))
    print(f"抽样图纸 {len(rows)} 张", flush=True)

    from services.model_qto import compute_quantities

    STORY_H = 4.5
    stats = {k: {"total": 0, "zero": 0} for k in ("columns", "slabs")}
    pt_counts: Counter = Counter()
    zero_pt_counts: Counter = Counter()
    zero_bbox: Counter = Counter()
    per_drawing: list[dict] = []
    volume = {"column": 0.0, "slab": 0.0, "wall": 0.0, "beam": 0.0}

    for idx, row in enumerate(rows, 1):
        try:
            data = get_file_bytes(row["file_key"])
            geom = extract_pdf_geometry(data)
            elems = recognize(geom, row["discipline"] or "structure",
                              row["id"], drawing_title=row["title"])
        except Exception as exc:  # noqa: BLE001 — 单图失败不打断普查
            print(f"  [{idx}] 跳过 {row['drawing_no']}: {exc}", flush=True)
            continue
        payload = elems.as_dict() if hasattr(elems, "as_dict") else elems
        d_zero = 0
        for kind in ("columns", "slabs"):
            for item in payload.get(kind) or []:
                outline = item.get("outline") or []
                if len(outline) < 3:
                    continue
                stats[kind]["total"] += 1
                area = _shoelace(outline)
                w, h, _bba = _bbox_area(outline)
                if kind == "columns":
                    pt_counts[len(outline)] += 1
                if area == 0.0:
                    stats[kind]["zero"] += 1
                    d_zero += 1
                    if kind == "columns":
                        zero_pt_counts[len(outline)] += 1
                        zero_bbox[f"{w:.2f}x{h:.2f}"] += 1
        for q in compute_quantities(payload, story_height_m=STORY_H):
            if q.element_type in volume:
                volume[q.element_type] += q.net_volume_m3
        if stats["columns"]["total"] or stats["slabs"]["total"]:
            per_drawing.append({"drawing_no": row["drawing_no"], "title": row["title"],
                                "zero": d_zero})
        if idx % 20 == 0:
            print(f"  ...{idx}/{len(rows)} 柱 {stats['columns']['total']}"
                  f" 零面积 {stats['columns']['zero']}", flush=True)

    mode = "改前(无修复)" if args.no_repair else "改后(环序修复)"
    print(f"\n===== 轮廓环序普查 · {mode} =====")
    for kind, label in (("columns", "柱"), ("slabs", "板")):
        t, z = stats[kind]["total"], stats[kind]["zero"]
        share = f"{z / t:.1%}" if t else "—"
        print(f"{label}候选(点数>=3) {t:6d}   鞋带面积为 0 {z:6d}  ({share})")
    print(f"柱轮廓点数分布      : {dict(sorted(pt_counts.items()))}")
    print(f"面积为0柱的点数分布 : {dict(sorted(zero_pt_counts.items()))}")
    print(f"面积为0柱的包围盒Top: {zero_bbox.most_common(5)}")
    # ⚠ 这是**抽样内逐图相加**，不是工程实际方量：同一层楼常有多张图
    # （分区图/分幅图/竣工图各一份），相加会重复计入。只有**改前/改后的
    # 相对差**有意义，绝对值不可当工程量用。
    print(f"\n下游算量净体积（compute_quantities，层高{STORY_H}m，"
          f"抽样内逐图相加·仅供改前改后对比）：")
    for kind in ("column", "wall", "beam", "slab"):
        print(f"  {kind:8s} {volume[kind]:14,.1f} m³")
    print(f"  {'合计':8s} {sum(volume.values()):14,.1f} m³")
    worst = sorted(per_drawing, key=lambda d: -d["zero"])[:8]
    print("\n面积为0最多的图：")
    for d in worst:
        print(f"  {d['zero']:5d}  {d['drawing_no']} {d['title']}")
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump({"mode": mode, "stats": stats, "volume": volume,
                       "pt_counts": dict(pt_counts),
                       "zero_pt_counts": dict(zero_pt_counts),
                       "zero_bbox": dict(zero_bbox),
                       "per_drawing": per_drawing}, fh, ensure_ascii=False, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
