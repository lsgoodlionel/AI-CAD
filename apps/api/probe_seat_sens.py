"""座椅误判 · 阈值敏感性：判据离「换个座椅布局就失效」有多远。

`DEFAULT_RUN_MIN=5` 是从实测排布读出来的（座椅 5 个一组被走道打断），
不是留了余量的安全值。这一步量：动阈值时全库删除量怎么变、
结构专业（误伤红线）怎么变。抖得越厉害，说明判据越依赖具体布局。
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
pages = []
for r in recs:
    if not r["cols"]:
        continue
    pages.append((r["discipline"] or "?", [
        {"outline": [[c[0] - c[2] / 2, c[1] - c[3] / 2],
                     [c[0] + c[2] / 2, c[1] - c[3] / 2],
                     [c[0] + c[2] / 2, c[1] + c[3] / 2],
                     [c[0] - c[2] / 2, c[1] + c[3] / 2]]} for c in r["cols"]]))
print(f"样本 {len(recs)} 张（有候选 {len(pages)} 张）\n")
print(f"{'gap':>5s} {'run':>4s} {'even':>5s} | {'总删':>7s} {'总占比':>7s} "
      f"| {'结构删':>7s} {'结构占比':>8s} | {'命中图':>6s}")
print("-" * 74)
for gap in (1.2, 1.5, 2.0):
    for run in (4, 5, 6):
        tot = cut = st_t = st_c = hit = 0
        for disc, cols in pages:
            f = find_dense_array_flags(cols, gap_ratio_max=gap, run_min=run)
            c = sum(f)
            tot += len(cols); cut += c
            if disc == "structure":
                st_t += len(cols); st_c += c
            if c:
                hit += 1
        print(f"{gap:5.1f} {run:4d} {1.3:5.1f} | {cut:7d} "
              f"{cut/max(tot,1):7.1%} | {st_c:7d} {st_c/max(st_t,1):8.1%} | {hit:6d}"
              + ("   ← 默认" if (gap, run) == (1.5, 5) else ""))
