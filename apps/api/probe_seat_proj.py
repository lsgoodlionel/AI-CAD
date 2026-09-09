"""座椅误判 · 按项目分层：没有剧院座椅的项目，判据删掉了什么？

歌剧院有整片观众席，删得多是应该的。轨道交通项目**没有剧院座席**，
那里的删除量更接近纯误伤指标 —— 如果它也删掉一大片，说明判据抓的
不只是座椅。
"""
import asyncio, collections, glob, json
import databases as dbl
from core.config import settings
from core.model3d.dense_array_filter import find_dense_array_flags


async def main():
    db = dbl.Database(settings.database_url, min_size=1, max_size=2)
    await db.connect()
    proj = {r["id"]: (r["pname"] or r["pid"][:8])
            for r in await db.fetch_all(
                "SELECT d.id::text AS id, d.project_id::text AS pid, "
                "       p.name AS pname "
                "FROM drawings d LEFT JOIN projects p ON p.id = d.project_id")}
    seen = {}
    for f in sorted(glob.glob("/tmp/seat_scan/shard_*.json")):
        try:
            for r in json.load(open(f)):
                seen.setdefault(r["did"], r)
        except Exception:
            pass
    by = collections.defaultdict(lambda: [0, 0, 0, 0])   # 图, 有候选图, 候选, 删
    worst = collections.defaultdict(list)
    for r in seen.values():
        p = proj.get(r["did"], "?")
        b = by[p]; b[0] += 1
        if not r["cols"]:
            continue
        b[1] += 1
        cols = [{"outline": [[c[0] - c[2] / 2, c[1] - c[3] / 2],
                             [c[0] + c[2] / 2, c[1] - c[3] / 2],
                             [c[0] + c[2] / 2, c[1] + c[3] / 2],
                             [c[0] - c[2] / 2, c[1] + c[3] / 2]]} for c in r["cols"]]
        cut = sum(find_dense_array_flags(cols))
        b[2] += len(cols); b[3] += cut
        if cut:
            worst[p].append((cut, len(cols), r["title"]))
    print(f"{'项目':26s} {'图':>5s} {'有候选':>6s} {'候选':>7s} {'删':>6s} {'占比':>7s}")
    print("-" * 62)
    for p, b in sorted(by.items(), key=lambda kv: -kv[1][2]):
        print(f"{str(p)[:24]:25s} {b[0]:5d} {b[1]:6d} {b[2]:7d} {b[3]:6d} "
              f"{(b[3]/b[2] if b[2] else 0):7.1%}")
        for c, n, t in sorted(worst[p], reverse=True)[:6]:
            print(f"      {c:5d}/{n:<6d} {str(t)[:46]}")
    await db.disconnect()

asyncio.run(main())
