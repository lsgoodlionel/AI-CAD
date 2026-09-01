"""座椅误判 · 判据的实测依据：比值与排布。

复现 `core/model3d/dense_array_filter.py` 模块文档里的两组数字：

1. **比值**（最近邻中心距 ÷ 自身较大边长）—— 座椅 ≈1、真柱网 ≈6~7。
   这是把「密排阵列」与「柱网」分开的唯一量，且量纲无关。
2. **排布** —— 座椅一行 30 个、间距 `0.36×5 → 1.15(走道) → 0.36×5`，
   `DEFAULT_RUN_MIN=5` 就是从这里读出来的。

数据来自 `probe_seat_scan.py` 落盘的全库候选（识别器**改动前**的输出）。
"""
import glob, json, statistics as st

SEATS = {"建筑-竣工图--三层平面图(三)", "建筑-竣工图--二层平面图(五)"}
GRIDS = {"结构-竣工图--南区（大、中歌剧厅）一层结构平面图（四）",
         "结构-竣工图--南区（大、中歌剧厅）地下一层结构平面图（四）"}

recs = {}
for f in sorted(glob.glob("/tmp/seat_scan/shard_*.json")):
    try:
        for r in json.load(open(f)):
            if r["title"] in SEATS | GRIDS:
                recs[r["title"]] = r
    except Exception:
        pass
if not recs:
    raise SystemExit("先跑 probe_seat_scan.py（本脚本读它的落盘结果）")


def ratios(cols):
    out = []
    for i, a in enumerate(cols):
        d = min((((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5
                 for j, b in enumerate(cols) if i != j), default=None)
        if d is not None:
            out.append(d / max(a[2], a[3], 1e-6))
    return out


print("=== 1. 比值：最近邻中心距 ÷ 自身边长 ===")
print(f"{'图纸':36s} {'n':>5s} {'中位':>6s} {'10分位':>7s} {'<1.5占比':>9s}  类型")
for t, r in sorted(recs.items(), key=lambda kv: kv[0] in SEATS, reverse=True):
    v = ratios(r["cols"])
    if not v:
        continue
    print(f"{t[:34]:35s} {len(v):5d} {st.median(v):6.2f} "
          f"{sorted(v)[len(v)//10]:7.2f} {sum(1 for x in v if x < 1.5)/len(v):8.0%}  "
          f"{'座椅' if t in SEATS else '真柱网'}")

print("\n=== 2. 排布：按行看间距（RUN_MIN=5 的来源）===")
for t in SEATS:
    r = recs.get(t)
    if not r:
        continue
    rows = {}
    for c in r["cols"]:
        rows.setdefault(round(c[1] / 0.3), []).append(c)
    longest = max(rows.values(), key=len)
    longest.sort()
    gaps = [round(longest[i + 1][0] - longest[i][0], 2)
            for i in range(len(longest) - 1)]
    print(f"{t[:34]:35s} 最长一行 {len(longest):3d} 个，"
          f"边长 {max(longest[0][2], longest[0][3]):.2f}")
    print(f"{'':35s} 间距前 12：{gaps[:12]}")
