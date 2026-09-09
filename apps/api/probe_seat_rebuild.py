"""带座椅闸重建 scene 并**落库**，然后量重建后的残留。

为什么必须重建：`scripts/model3d/export_yolo_dataset.py` 从
`project_models` 最新 version 的 scene 取标注，识别器修好**不会**改写
存量 scene。实测第二工程 v7（座椅闸落地后建的，但当时容器里没有闸）
仍有 420 根密排阵列 —— 不重建就重新导出训练集，模型继续学座椅。

走 `tasks.model_build._do_build`（不经 Celery），与线上同一条落库路径：
version+1、status/built_at 一并更新，不是手写 UPDATE。

用法：python probe_seat_rebuild.py <project_id>
"""
import asyncio, json, sys
import databases as dbl
from core.config import settings
from core.model3d.dense_array_filter import find_dense_array_flags
from tasks.model_build import _do_build


KINDS = ("columns", "walls", "beams", "slabs", "pipes", "equipment")


async def residue(pid: str) -> tuple[int, int, int, dict]:
    db = dbl.Database(settings.database_url, min_size=1, max_size=2)
    await db.connect()
    r = await db.fetch_one(
        "SELECT scene, version FROM project_models WHERE project_id=:p "
        "ORDER BY version DESC LIMIT 1", {"p": pid})
    sc = r["scene"] if isinstance(r["scene"], dict) else json.loads(r["scene"])
    tot = cut = 0
    totals = {k: 0 for k in KINDS}
    for fl in sc.get("floors") or []:
        els = fl.get("elements") or {}
        for k in KINDS:
            totals[k] += len(els.get(k) or [])
        cols = els.get("columns") or []
        items = [{"outline": c.get("outline") or []} for c in cols]
        if not items:
            continue
        tot += len(items); cut += sum(find_dense_array_flags(items))
    await db.disconnect()
    return r["version"], tot, cut, totals


async def main():
    pid = sys.argv[1]
    v0, t0, c0, g0 = await residue(pid)
    print(f"重建前  v{v0}  柱 {t0}  密排阵列 {c0} = {(c0/t0 if t0 else 0):.1%}", flush=True)
    out = await _do_build(pid)
    print("build:", json.dumps(out, ensure_ascii=False), flush=True)
    v1, t1, c1, g1 = await residue(pid)
    print(f"重建后  v{v1}  柱 {t1}  密排阵列 {c1} = {(c1/t1 if t1 else 0):.1%}", flush=True)
    print(f"柱 {t0} → {t1}（{t1-t0:+d}）；残留密排 {c0} → {c1}（{c1-c0:+d}）")
    print("各类构件：")
    for k in KINDS:
        print(f"  {k:10s} {g0[k]:7d} → {g1[k]:7d}  ({g1[k]-g0[k]:+d})")
    json.dump({"pid": pid, "v0": v0, "v1": v1, "before": g0, "after": g1,
               "residue_before": c0, "residue_after": c1},
              open(f"/tmp/seat_rebuild_{pid[:8]}.json", "w"))

asyncio.run(main())
