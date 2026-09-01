"""三道候选闸的全库量：各删多少、彼此重叠多少、风险区在哪。

闸 A（图种）  剖面图不产平面柱截面。
闸 B（顶点数）填充路径顶点数过多 → 汉字笔画/复杂图元，不是构件截面。
              实测：3结构详图（五）图签栏内候选顶点数中位 **96**，
              而真柱（二层结构平面图）中位 **6**、真构造柱（北立面定位图）**4**。
闸 C（比例）  比例是「按 8.4m 轴距倒推」猜出来的，且换算图宽落在
              `PLAN_EXTENT_RANGE_M` 之外 → 尺寸判据整体不成立。

只报数，不下结论 —— 结论要配叠框核验。
"""
import collections, glob, json

FULL = 4109
PLAN_EXTENT_RANGE_M = (10.0, 300.0)
VTX_LIMITS = (12, 16, 24, 32, 48, 64)

_seen = {}
for f in sorted(glob.glob("/tmp/fp_scan/shard_*.json")):
    try:
        for r in json.load(open(f)):
            _seen.setdefault(r["did"], r)
    except Exception:
        pass
recs = list(_seen.values())
tot = sum(len(r["cols"]) for r in recs)
lay = sum(int(r.get("nlayer") or 0) for r in recs)
unknown_vtx = sum(1 for r in recs for v in r.get("vtx") or [] if not v)
print(f"样本 {len(recs)} 张 / 全库 {FULL} = {len(recs)/FULL:.1%}   "
      f"柱候选 {tot}（图层判定 {lay}，猜测 {tot-lay}）   "
      f"顶点数查不到 {unknown_vtx}（{unknown_vtx/max(tot,1):.1%}，"
      f"矩形分支或去重合并过）")

# ── 顶点数分布 ──────────────────────────────────────────────────
buckets = [(0, 0, "查不到"), (1, 8, "≤8"), (9, 12, "9~12"), (13, 16, "13~16"),
           (17, 24, "17~24"), (25, 48, "25~48"), (49, 96, "49~96"),
           (97, 10**9, ">96")]
hist = collections.Counter()
for r in recs:
    for v in r.get("vtx") or []:
        for lo, hi, name in buckets:
            if lo <= v <= hi:
                hist[name] += 1
                break
print("\n候选的源多边形顶点数分布：")
for _lo, _hi, name in buckets:
    c = hist[name]
    print(f"  {name:>8s} {c:7d} {c/max(tot,1):6.1%}")


def wide(r):
    """换算图宽（米）。0 = 算不出。"""
    return (r.get("pw") or 0.0) * (r.get("scale") or 0.0)


def gate_a(r):
    return r["view"] == "section"


def gate_c(r):
    w = wide(r)
    return bool(r.get("guess")) and w > 0 and not (
        PLAN_EXTENT_RANGE_M[0] <= w <= PLAN_EXTENT_RANGE_M[1])


def cut_b(r, limit):
    return sum(1 for v in r.get("vtx") or [] if v >= limit)


print("\n闸 A（剖面图）与闸 C（比例说不通）：")
for name, fn in (("A 剖面图", gate_a), ("C 比例说不通", gate_c),
                 ("A 或 C", lambda r: gate_a(r) or gate_c(r))):
    cut = sum(max(len(r["cols"]) - int(r.get("nlayer") or 0), 0)
              for r in recs if fn(r))
    pages = sum(1 for r in recs if fn(r) and r["cols"])
    st = sum(max(len(r["cols"]) - int(r.get("nlayer") or 0), 0)
             for r in recs if fn(r) and r["discipline"] == "structure")
    pl = sum(max(len(r["cols"]) - int(r.get("nlayer") or 0), 0)
             for r in recs if fn(r) and r["view"] == "plan")
    print(f"  {name:14s} 删 {cut:6d} = {cut/max(tot,1):5.1%}   命中 {pages:4d} 张"
          f"   结构专业 {st:6d}   平面图 {pl:6d}")

print("\n闸 B（顶点数 ≥ 阈值）：")
print(f"  {'阈值':>5s} {'删':>7s} {'占比':>7s} {'命中图':>6s} {'其中平面图删':>11s}")
for limit in VTX_LIMITS:
    cut = sum(cut_b(r, limit) for r in recs)
    pages = sum(1 for r in recs if cut_b(r, limit))
    pl = sum(cut_b(r, limit) for r in recs if r["view"] == "plan")
    print(f"  {limit:5d} {cut:7d} {cut/max(tot,1):7.1%} {pages:6d} {pl:11d}")

print("\n闸 B(≥16) 删得最多的图：")
rows = sorted(recs, key=lambda r: -cut_b(r, 16))[:12]
for r in rows:
    if not cut_b(r, 16):
        break
    print(f"  {cut_b(r,16):5d}/{len(r['cols']):<5d} {r['view']:9s} "
          f"图宽{wide(r):7.0f}m guess={str(r.get('guess')):5s} "
          f"{r['discipline'] or '?':12s} {str(r['title'])[:34]}")

print("\n闸 C 命中且候选最多的图：")
rows = sorted((r for r in recs if gate_c(r) and r["cols"]),
              key=lambda r: -len(r["cols"]))[:12]
for r in rows:
    print(f"  {len(r['cols']):5d}  {r['view']:9s} 图宽{wide(r):8.0f}m "
          f"{r['discipline'] or '?':12s} {str(r['title'])[:34]}")

print("\n**本轮实际落地的两道闸**（A 剖面图 + B 顶点数 >48）：")
cut_view = sum(max(len(r["cols"]) - int(r.get("nlayer") or 0), 0)
               for r in recs if gate_a(r))
cut_shape = sum(cut_b(r, 49) for r in recs if not gate_a(r))
pages = sum(1 for r in recs if gate_a(r) and r["cols"]) + \
    sum(1 for r in recs if not gate_a(r) and cut_b(r, 49))
pl = sum(cut_b(r, 49) for r in recs if r["view"] == "plan" and not gate_a(r))
print(f"  A 剖面图 删 {cut_view}   B 形状 删 {cut_shape}   "
      f"合计 {cut_view + cut_shape} / {tot} = {(cut_view+cut_shape)/max(tot,1):.1%}"
      f"   命中 {pages} 张   其中平面图上删 {pl}")

print("\n（未落地）闸 C 若也上：")
cut_c = sum(max(len(r["cols"]) - int(r.get("nlayer") or 0), 0)
            for r in recs if gate_c(r) and not gate_a(r))
print(f"  再删 {cut_c} = {cut_c/max(tot,1):.1%}")
