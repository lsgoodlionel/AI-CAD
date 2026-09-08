"""从图纸信息档案（OCR 文本）里量出每张图的 `1:N` 比例标注（纯测量）。

**为什么用档案而不是矢量文字**：实测 56 张金标准图里只有 5 张的矢量文字
含 `1:N`，且 4 张是误命中（`1:5`/`1:8`/`1:2` 来自别的数字）——
这批 PDF 的文字是轮廓化的，`element_recognizer._detect_scale` 的
「读图上明写比例」那条路**几乎从来没走通过**。
而档案层的 OCR 覆盖了 54/60 张。

输出每张图的候选票（分母、出现次数、是否在图框带内），
不做任何判断——票怎么合成、阈值取多少，要等分布量出来再定。

用法：
    python -m scripts.model3d.probe_scale_archive <manifest.tsv> <out.json>
    python -m scripts.model3d.probe_scale_archive --all <out.json>
"""
from __future__ import annotations

import asyncio
import json
import re
import sys

import databases as databases_lib

from core.config import settings

SCALE_RE = re.compile(r"1\s*[:：]\s*(\d{1,7})")

_SQL = """
SELECT drawing_id, category, extractor, content, location_json
FROM drawing_extracted_info
WHERE drawing_id = ANY(:ids) AND is_active
"""

_SQL_ALL_IDS = """
SELECT t.drawing_id FROM drawing_transform t
JOIN drawings d ON d.id = t.drawing_id
ORDER BY t.drawing_id
"""


def _xy(location_json) -> tuple[float | None, float | None]:
    """档案位置 → (x, y)。bbox 与 x/y 两种形状都吃，取不到给 (None, None)。"""
    if not location_json:
        return None, None
    loc = location_json
    if isinstance(loc, str):
        try:
            loc = json.loads(loc)
        except Exception:  # noqa: BLE001
            return None, None
    if not isinstance(loc, dict):
        return None, None
    bbox = loc.get("bbox")
    if isinstance(bbox, (list, tuple)) and len(bbox) >= 4:
        return (float(bbox[0]) + float(bbox[2])) / 2, (float(bbox[1]) + float(bbox[3])) / 2
    if "x" in loc and "y" in loc:
        try:
            return float(loc["x"]), float(loc["y"])
        except (TypeError, ValueError):
            return None, None
    return None, None


async def main() -> int:
    args = sys.argv[1:]
    db = databases_lib.Database(settings.database_url)
    await db.connect()
    if args[0] == "--all":
        out_path = args[1]
        rows = await db.fetch_all(_SQL_ALL_IDS)
        ids = [r["drawing_id"] for r in rows]
    else:
        out_path = args[1]
        ids = [line.split("\t")[2]
               for line in open(args[0]).read().splitlines()[1:]]

    out: dict[str, dict] = {}
    step = 200
    for i in range(0, len(ids), step):
        chunk = ids[i:i + step]
        rows = await db.fetch_all(_SQL, {"ids": chunk})
        for r in rows:
            did = str(r["drawing_id"])
            rec = out.setdefault(did, {"votes": [], "extent": [0.0, 0.0], "n_rows": 0})
            rec["n_rows"] += 1
            x, y = _xy(r["location_json"])
            if x is not None:
                rec["extent"][0] = max(rec["extent"][0], x)
                rec["extent"][1] = max(rec["extent"][1], y)
            for m in SCALE_RE.finditer(str(r["content"] or "")):
                rec["votes"].append({
                    "n": int(m.group(1)),
                    "cat": str(r["category"]),
                    "ext": str(r["extractor"]),
                    "x": None if x is None else round(x, 1),
                    "y": None if y is None else round(y, 1),
                })
        print(f"  {min(i + step, len(ids))}/{len(ids)}", flush=True)
    for did in ids:
        out.setdefault(str(did), {"votes": [], "extent": [0.0, 0.0], "n_rows": 0})
    await db.disconnect()
    json.dump(out, open(out_path, "w"), ensure_ascii=False)
    print(f"写出 {out_path}（{len(out)} 张）")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
