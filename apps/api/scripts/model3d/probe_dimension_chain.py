"""量「尺寸链」能不能反推比例（纯测量）。

施工图的尺寸标注写的是**实际毫米数**，标注文字画在它所标注的那一段中间。
于是相邻两个标注文字的中心距 ≈ (前值 + 后值) / 2 毫米 —— 这给出一条
与「图上印刷的 1:N」和「系统变换」都**互相独立**的比例通道。

`services/scale_candidates.py` 记着这条路只有 3.6% 准确率，但那次的「真值」
是 `drawing_transform` 本身，而金标准后来测出变换只有 30% 站得住 ——
**拿一个 30% 的东西当真值，去否定另一条通道，结论不成立**。这里重测。

输出每图的原始配对，判据与阈值留到离线分析。

用法：
    python -m scripts.model3d.probe_dimension_chain <manifest.tsv> <out.json>
"""
from __future__ import annotations

import asyncio
import json
import math
import sys

import databases as databases_lib

from core.config import settings

_SQL = """
SELECT drawing_id, content, location_json
FROM drawing_extracted_info
WHERE drawing_id = ANY(:ids) AND is_active AND category = 'dimension'
"""

_SQL_PAGE = """
SELECT t.drawing_id, t.page_h, t.scale_m_pt FROM drawing_transform t
WHERE t.drawing_id = ANY(:ids)
"""


def _center(loc) -> tuple[float, float] | None:
    if isinstance(loc, str):
        try:
            loc = json.loads(loc)
        except Exception:  # noqa: BLE001
            return None
    if not isinstance(loc, dict):
        return None
    b = loc.get("bbox")
    if isinstance(b, (list, tuple)) and len(b) >= 4:
        return (float(b[0]) + float(b[2])) / 2, (float(b[1]) + float(b[3])) / 2
    if "x" in loc and "y" in loc:
        return float(loc["x"]), float(loc["y"])
    return None


async def main() -> int:
    man_path, out_path = sys.argv[1], sys.argv[2]
    ids = [line.split("\t")[2]
           for line in open(man_path).read().splitlines()[1:]]
    db = databases_lib.Database(settings.database_url)
    await db.connect()
    rows = await db.fetch_all(_SQL, {"ids": ids})
    pages = {str(r["drawing_id"]): (float(r["page_h"]), float(r["scale_m_pt"]))
             for r in await db.fetch_all(_SQL_PAGE, {"ids": ids})}
    await db.disconnect()

    out: dict[str, dict] = {}
    for r in rows:
        did = str(r["drawing_id"])
        c = _center(r["location_json"])
        if c is None:
            continue
        text = str(r["content"] or "").strip()
        if not text.isdigit():
            continue
        value = int(text)
        rec = out.setdefault(did, {"dims": [], "page_h": pages.get(did, (0, 0))[0],
                                   "scale_m_pt": pages.get(did, (0, 0))[1]})
        rec["dims"].append([value, round(c[0], 1), round(c[1], 1)])
    for did in ids:
        out.setdefault(str(did), {"dims": [], "page_h": pages.get(str(did), (0, 0))[0],
                                  "scale_m_pt": pages.get(str(did), (0, 0))[1]})
    json.dump(out, open(out_path, "w"))
    n = sum(1 for v in out.values() if len(v["dims"]) >= 4)
    print(f"写出 {out_path}；{n}/{len(out)} 张有 >=4 条尺寸标注")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
