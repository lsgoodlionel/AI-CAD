"""输出 PROGRESS.md 表格所需的数字（JSON），供宿主机填占位符。

数字必须来自 `probe_seat_scan.py` 的落盘结果，不手抄 —— 手抄过一次就会漂。
（docs/ 不在容器镜像内，故只出数不写文件。）
"""
import asyncio, collections, glob, json, sys

import databases as dbl

sys.path.insert(0, "/app")
from core.config import settings
from core.model3d.dense_array_filter import find_dense_array_flags

# 按 did 去重：分片本身不重叠，但补扫的图会与后续分片撞上，
# 重复计数会把删除率算高。
_seen = {}
for f in sorted(glob.glob("/tmp/seat_scan/shard_*.json")):
    try:
        for r in json.load(open(f)):
            _seen.setdefault(r["did"], r)
    except Exception:
        pass
recs = list(_seen.values())
async def _projects() -> dict:
    db = dbl.Database(settings.database_url, min_size=1, max_size=2)
    await db.connect()
    rows = await db.fetch_all(
        "SELECT d.id::text AS id, d.project_id::text AS pid, p.name AS pname "
        "FROM drawings d LEFT JOIN projects p ON p.id = d.project_id")
    await db.disconnect()
    return {r["id"]: (r["pname"] or (r["pid"] or "?")[:8]) for r in rows}


proj = asyncio.run(_projects())
by_proj = collections.defaultdict(lambda: [0, 0])
by = collections.defaultdict(lambda: [0, 0])
for r in recs:
    if not r["cols"]:
        continue
    cols = [{"outline": [[c[0] - c[2] / 2, c[1] - c[3] / 2],
                         [c[0] + c[2] / 2, c[1] - c[3] / 2],
                         [c[0] + c[2] / 2, c[1] + c[3] / 2],
                         [c[0] - c[2] / 2, c[1] + c[3] / 2]]} for c in r["cols"]]
    n_cut = sum(find_dense_array_flags(cols))
    b = by[r["discipline"] or "?"]
    b[0] += len(cols); b[1] += n_cut
    q = by_proj[proj.get(r["did"], "?")]
    q[0] += len(cols); q[1] += n_cut
tt = sum(b[0] for b in by.values()); tc = sum(b[1] for b in by.values())
pct = lambda a, b: f"{a/b:.1%}" if b else "—"
vals = {"COVER": f"随机样本 {len(recs)} / 4109 张 = {len(recs)/4109:.0%}",
        "TT": tt, "TC": tc, "TP": pct(tc, tt)}
vals["PROJ"] = "\n".join(
    f"    {str(name)[:22]:24s} 候选 {b[0]:6d}  删 {b[1]:5d} = {pct(b[1], b[0])}"
    for name, b in sorted(by_proj.items(), key=lambda kv: -kv[1][0]))
for key, disc in (("ST", "structure"), ("AR", "architecture"), ("DE", "decoration")):
    b = by[disc]
    vals[f"{key}_T"], vals[f"{key}_C"] = b[0], b[1]
    vals[f"{key}_P"] = pct(b[1], b[0])
print(json.dumps(vals, ensure_ascii=False))
