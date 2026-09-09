"""交叉验证：判据的命中量，是否跟着**档案里标高条目的多少**走。

## 为什么不用档案的坐标

`drawing_extracted_info` 里 `category='elevation'` 有 2.48 万条，带 bbox。
但那批 bbox 是在 `core/model3d/ocr/service.py` 的 `effective_dpi` 修好之前
写的（大图降采样比例每张不同，档案坐标 90% 落空），**位置不可信**。
所以这里只用**计数**：一张图上标高条目越多，图上标高符号的笔画就越多，
标注判据的命中量应当越大。计数与 DPI 换算无关，绕开了那个已知缺陷。

相关不等于因果：条目多的图往往也更大更密，候选本身就多。所以同时报
**命中率**（除以候选数）与**命中量**，两者结论不一致就说不一致。

用法（容器内，需先跑 annotation_backtest.py 拿到 stats.json）：
    python scripts/model3d/annotation_crosscheck.py /tmp/annot_bt/stats.json
"""
from __future__ import annotations

import asyncio
import json
import os
import statistics
import sys

import databases as databases_lib

sys.path.insert(0, os.getcwd())

from core.config import settings  # noqa: E402

STATS = sys.argv[1] if len(sys.argv) > 1 else "/tmp/annot_bt/stats.json"


def _spearman(xs: list[float], ys: list[float]) -> float:
    """斯皮尔曼秩相关 —— 用秩而不是值，免得被少数超大图纸拽着走。"""
    def rank(vals):
        order = sorted(range(len(vals)), key=lambda i: vals[i])
        r = [0.0] * len(vals)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and vals[order[j + 1]] == vals[order[i]]:
                j += 1
            avg = (i + j) / 2 + 1
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r

    rx, ry = rank(xs), rank(ys)
    n = len(xs)
    if n < 3:
        return float("nan")
    mx, my = statistics.mean(rx), statistics.mean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = (sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)) ** 0.5
    return num / den if den else float("nan")


async def main() -> None:
    stats = json.load(open(STATS))
    per = [d for d in stats["per_drawing"] if d["n"] > 0]
    db = databases_lib.Database(settings.database_url)
    await db.connect()
    rows = await db.fetch_all(
        "SELECT drawing_id::text AS did, count(*) AS c FROM drawing_extracted_info "
        "WHERE is_active AND category='elevation' AND drawing_id = ANY(:ids) "
        "GROUP BY 1", {"ids": [d["drawing_id"] for d in per]})
    counts = {r["did"]: int(r["c"]) for r in rows}
    have = [d for d in per if d["drawing_id"] in counts]
    print(f"回测图 {len(per)} 张，其中 {len(have)} 张有档案标高条目")
    if len(have) >= 3:
        elev = [float(counts[d["drawing_id"]]) for d in have]
        hit_n = [float(d["flagged"]) for d in have]
        hit_r = [d["flagged"] / d["n"] for d in have]
        print(f"  标高条目数 × 命中量  斯皮尔曼 rho = {_spearman(elev, hit_n):+.2f}")
        print(f"  标高条目数 × 命中率  斯皮尔曼 rho = {_spearman(elev, hit_r):+.2f}")
    zero = [d for d in per if counts.get(d["drawing_id"], 0) == 0]
    nonzero = [d for d in per if counts.get(d["drawing_id"], 0) > 0]
    for name, group in (("无标高条目", zero), ("有标高条目", nonzero)):
        if not group:
            continue
        rate = sum(d["flagged"] for d in group) / sum(d["n"] for d in group)
        print(f"  {name}：{len(group)} 张，合计命中率 {rate:.1%}")
    await db.disconnect()


asyncio.run(main())
