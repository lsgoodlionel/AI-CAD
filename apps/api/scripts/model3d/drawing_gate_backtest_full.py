"""扩展回测 v2 —— **逐判据对齐各自的真值维度**。

v1 把五批真值混成一个「有问题」，得出 non_geometric 误伤 56% —— 那是错的：
`drawing_scale` 的 not-ok 意思是「比例不合理」，与「这张图该不该建模」是两回事。
把不同问题的真值当同一个用，与「判据不固定」是同一类错误。

对齐关系：
  角色类判据（non_geometric / detail / unknown_role / coordinate_base）
      → 真值取 `drawing_usable`（能否用于建模）与 `has_discipline`（有无专业）
  scale_not_authoritative → 真值只取 `drawing_scale`（比例合不合理）
  elevation_reference     → 真值只取 `axis_grid_presence`（有无轴网）
"""
import asyncio, collections, json, sys
sys.path.insert(0, "/app")
import databases as dbl
from core.config import settings
from services.drawing_gate import evaluate_drawing, DrawingSignals

GOLD, SHEETS = "/app/data/model3d/gold", "/app/gold_sheets"
MF = {"drawing_usable": "usable", "has_discipline": "disc2", "drawing_scale": "scale",
      "floor_assignment": "floor", "axis_grid_presence": "axis"}

def tag2did(name):
    L = open(f"{SHEETS}/{name}/manifest.tsv").read().splitlines()
    h = L[0].split("\t"); ti, di = h.index("tag"), h.index("drawing_id")
    return {l.split("\t")[ti]: l.split("\t")[di] for l in L[1:] if l.strip()}

def truth(cls):
    """→ {drawing_id: ok}  ok=True 表示该批的真值是「正常/合理」。"""
    d = json.load(open(f"{GOLD}/{cls}_v1.json")); m = tag2did(MF[cls]); out = {}
    for u in d["units"]:
        for c in u.get("classes", {}).values():
            vs = c.get("verdicts", [])
            if vs:
                for v in vs:
                    did = m.get(str(v.get("ref")))
                    if did: out[did] = bool(v.get("ok"))
            else:
                did = u.get("source", {}).get("drawing_id") or ""
                if did: out[did] = c.get("fields", {}).get("usable") == "true"
    return out

async def main():
    db = dbl.Database(settings.database_url); await db.connect()
    T = {c: truth(c) for c in MF}
    # 角色维度真值：可建模 = drawing_usable 可用 或 has_discipline 有专业
    role_truth = {}
    for c in ("drawing_usable", "has_discipline"):
        for d, ok in T[c].items(): role_truth.setdefault(d, []).append(ok)
    role_truth = {d: any(v) for d, v in role_truth.items()}
    dids = sorted(set().union(*[set(t) for t in T.values()]))

    sig = {}
    for did in dids:
        r = await db.fetch_one(
            "SELECT d.title,d.drawing_no,d.discipline,t.scale_m_pt,t.confidence,"
            " COALESCE(a.axis_count,0) ac, COALESCE(a.circle_count,0) cc "
            "FROM drawings d LEFT JOIN drawing_transform t ON t.drawing_id=d.id "
            "LEFT JOIN axis_recognition a ON a.drawing_id=d.id WHERE d.id::text=:d", {"d": did})
        if r: sig[did] = DrawingSignals(
            drawing_id=did, title=str(r["title"] or ""), drawing_no=str(r["drawing_no"] or ""),
            discipline=str(r["discipline"] or ""),
            scale_m_pt=float(r["scale_m_pt"]) if r["scale_m_pt"] is not None else None,
            transform_confidence=float(r["confidence"]) if r["confidence"] is not None else None,
            axis_count=int(r["ac"]), circle_count=int(r["cc"]))
    res = {d: evaluate_drawing(s) for d, s in sig.items()}
    print(f"=== 逐判据对齐各自真值（{len(res)} 张有信号）===\n")

    ALIGN = {"non_geometric": role_truth, "detail": T["floor_assignment"],
             "unknown_role": role_truth, "coordinate_base": role_truth,
             "scale_not_authoritative": T["drawing_scale"],
             "elevation_reference": T["axis_grid_presence"]}
    print(f"   {'判据':26s} {'可判':>4s} {'真该拦':>6s} {'误伤':>5s} {'误伤率':>7s}  对齐的真值")
    NAME = {"non_geometric": "usable+discipline", "detail": "floor_assignment",
            "unknown_role": "usable+discipline", "coordinate_base": "usable+discipline",
            "scale_not_authoritative": "drawing_scale", "elevation_reference": "axis_grid"}
    for code, tv in ALIGN.items():
        hit = [d for d, r in res.items() if any(x.code == code for x in r.reasons)]
        judged = [d for d in hit if d in tv]
        bad = sum(1 for d in judged if tv[d])      # 真值说「正常」却被这条判据命中 = 误伤
        good = len(judged) - bad
        if not judged:
            print(f"   {code:26s} {0:4d} {'—':>6s} {'—':>5s} {'—':>7s}  {NAME[code]}"); continue
        print(f"   {code:26s} {len(judged):4d} {good:6d} {bad:5d} {bad/len(judged):7.0%}  {NAME[code]}")

    print("\n【对照：基线率】各真值维度里「有问题」的自然占比")
    for c in MF:
        t = T[c]; bad = sum(1 for v in t.values() if not v)
        print(f"   {c:20s} {bad}/{len(t)} = {bad/len(t):.0%}")
    rb = sum(1 for v in role_truth.values() if not v)
    print(f"   {'role_truth(合并)':20s} {rb}/{len(role_truth)} = {rb/len(role_truth):.0%}")
    await db.disconnect()

asyncio.run(main())
