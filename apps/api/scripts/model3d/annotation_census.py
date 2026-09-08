"""图面标注/非实体过滤器 —— **先量后改** 的第一步：几何形状普查。

只统计、不改任何识别结果。输出 jsonl，每行一个候选的几何特征：
原始点数 / 去重后真实角点数 / 轴对齐包围盒 / 最小面积外接矩形（真实范围）/ 填充率。

用法（容器内）：
    python scripts/model3d/annotation_census.py [图纸数] [输出路径]
"""
from __future__ import annotations

import asyncio
import gc
import json
import os
import random
import sys

import databases as databases_lib

sys.path.insert(0, os.getcwd())

from core.config import settings  # noqa: E402
from core.model3d.annotation_filter import (  # noqa: E402
    corner_points,
    min_area_rect,
    polygon_area,
)
from core.model3d.element_recognizer import recognize  # noqa: E402
from core.model3d.geometry_extractor import extract_pdf_geometry  # noqa: E402
from core.storage import get_file_bytes  # noqa: E402

N_DRAWINGS = int(sys.argv[1]) if len(sys.argv) > 1 else 60
OUT = sys.argv[2] if len(sys.argv) > 2 else "/tmp/annot_census.jsonl"
random.seed(20260908)


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
    done = 0
    with open(OUT, "w") as fh:
        for row in rows:
            if done >= N_DRAWINGS:
                break
            did = str(row["id"])
            try:
                geom = extract_pdf_geometry(get_file_bytes(row["file_key"]))
                tr = await db.fetch_one(
                    "SELECT scale_m_pt FROM drawing_transform WHERE drawing_id=:d",
                    {"d": did})
                fe = recognize(geom, row["discipline"], did,
                               drawing_title=row["title"],
                               scale_override=float(tr["scale_m_pt"]) if tr else None)
            except Exception as exc:  # noqa: BLE001 —— 单图失败不阻断普查
                print("skip", did, exc)
                continue
            done += 1
            # 原始多边形（未降点）——`_downsample_ring(poly, 8)` 会把点数抹平到 8，
            # 「顶点数」这一类判据在构件字典上根本量不出来，必须在这里量。
            scale = fe.scale or 0.0
            for poly in (geom.polys or []):
                if len(poly) < 3 or not scale:
                    continue
                xs = [float(p[0]) for p in poly]
                ys = [float(p[1]) for p in poly]
                w = (max(xs) - min(xs)) * scale
                h = (max(ys) - min(ys)) * scale
                if not (0.1 <= w <= 3.0 and 0.1 <= h <= 3.0):
                    continue
                pts = [(float(p[0]) * scale, float(p[1]) * scale) for p in poly]
                lo, sh = min_area_rect(pts)
                fh.write(json.dumps({
                    "drawing_id": did, "title": row["title"],
                    "discipline": row["discipline"], "kind": "raw_poly",
                    "n_pts": len(poly), "corners": len(corner_points(pts)),
                    "w_m": round(w, 4), "h_m": round(h, 4),
                    "aabb_aspect": round(max(w, h) / max(min(w, h), 1e-6), 3),
                    "long_m": round(lo, 4), "short_m": round(sh, 4),
                    "true_aspect": round(lo / max(sh, 1e-6), 3),
                    "fill": round(polygon_area(pts) / max(lo * sh, 1e-9), 4),
                }, ensure_ascii=False) + "\n")
            for kind in ("columns", "equipment", "dense_arrays"):
                for el in getattr(fe, kind, None) or []:
                    o = el.get("outline") or []
                    if len(o) < 3:
                        continue
                    pts = [(float(p[0]), float(p[1])) for p in o]
                    xs = [p[0] for p in pts]
                    ys = [p[1] for p in pts]
                    w = max(xs) - min(xs)
                    h = max(ys) - min(ys)
                    lo, sh = min_area_rect(pts)
                    fh.write(json.dumps({
                        "drawing_id": did, "title": row["title"],
                        "discipline": row["discipline"], "kind": kind,
                        "n_pts": len(o), "corners": len(corner_points(pts)),
                        "w_m": round(w, 4), "h_m": round(h, 4),
                        "aabb_aspect": round(max(w, h) / max(min(w, h), 1e-6), 3),
                        "long_m": round(lo, 4), "short_m": round(sh, 4),
                        "true_aspect": round(lo / max(sh, 1e-6), 3),
                        "fill": round(polygon_area(pts) / max(lo * sh, 1e-9), 4),
                    }, ensure_ascii=False) + "\n")
            gc.collect()
    print("扫描图纸", done, "→", OUT)
    await db.disconnect()


asyncio.run(main())
