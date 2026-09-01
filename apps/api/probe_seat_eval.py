"""座椅误判 · 全库评估：判据在存量图上删掉多少、删在哪。

读 `probe_seat_scan.py` 落盘的候选（**改动前**的识别器输出），离线套判据。
可随扫描推进反复运行 —— 每次都报当前样本量，趋势稳不稳定一眼看得出。

误伤的主要风险区是**结构专业的平面图**（那里的密排小方块最可能是真柱），
所以按专业分列，不看合计一个数。
"""
import collections, glob, json, sys

sys.path.insert(0, "/app")
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
print(f"样本 {len(recs)} 张 / 全库 4109 = {len(recs)/4109:.1%}"
      f"   解析失败 {sum(1 for r in recs if r.get('err'))} 张")

by = collections.defaultdict(lambda: [0, 0, 0, 0])   # 图数, 有柱图数, 候选, 删
hit_pages = []
for r in recs:
    cols = [{"outline": [[c[0] - c[2] / 2, c[1] - c[3] / 2],
                         [c[0] + c[2] / 2, c[1] - c[3] / 2],
                         [c[0] + c[2] / 2, c[1] + c[3] / 2],
                         [c[0] - c[2] / 2, c[1] + c[3] / 2]]}
            for c in r["cols"]]
    d = r["discipline"] or "?"
    b = by[d]
    b[0] += 1
    if not cols:
        continue
    b[1] += 1
    flags = find_dense_array_flags(cols)
    cut = sum(flags)
    b[2] += len(cols); b[3] += cut
    if cut:
        hit_pages.append((cut, len(cols), d, r["title"]))

print(f"\n{'专业':12s} {'图':>5s} {'有候选图':>7s} {'候选':>8s} {'删':>7s} {'删占比':>7s}")
print("-" * 56)
T = [0, 0]
for d, b in sorted(by.items(), key=lambda kv: -kv[1][2]):
    T[0] += b[2]; T[1] += b[3]
    print(f"{d:12s} {b[0]:5d} {b[1]:7d} {b[2]:8d} {b[3]:7d} "
          f"{(b[3]/b[2] if b[2] else 0):7.1%}")
print(f"{'合计':12s} {len(recs):5d} {'':7s} {T[0]:8d} {T[1]:7d} "
      f"{(T[1]/T[0] if T[0] else 0):7.1%}")

print(f"\n被命中的图 {len(hit_pages)} 张（占有候选图 "
      f"{len(hit_pages)/max(sum(b[1] for b in by.values()),1):.1%}）")
print("删得最多的 15 张：")
for cut, n, d, t in sorted(hit_pages, reverse=True)[:15]:
    print(f"  {cut:5d}/{n:<5d} {d:12s} {str(t)[:44]}")
print("\n结构专业里被命中的图（误伤风险区）：")
st = [h for h in hit_pages if h[2] == "structure"]
print(f"  共 {len(st)} 张")
for cut, n, d, t in sorted(st, reverse=True)[:12]:
    print(f"  {cut:5d}/{n:<5d} {str(t)[:52]}")
