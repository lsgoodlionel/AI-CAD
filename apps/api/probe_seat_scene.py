"""座椅误判 · YOLO 训练标签的污染量。

`scripts/model3d/export_yolo_dataset.py` 优先从 `project_models.scene`
取标注（兜底才实时 recognize）。scene 是**历史**建模结果 —— 识别器修好
不会自动改写它。所以要量：存量 scene 的 columns 里有多少是密排阵列。
"""
import asyncio, json
import databases as dbl
from core.config import settings
from core.model3d.dense_array_filter import find_dense_array_flags


async def main():
    # `databases` 默认 pool min_size=10；并行跑几个探测脚本就会撞
    # PG 的 max_connections（实测报 TooManyConnectionsError）。这里只做
    # 一次查询，给最小池。
    db = dbl.Database(settings.database_url, min_size=1, max_size=2)
    await db.connect()
    rows = await db.fetch_all(
        "SELECT project_id::text AS pid, scene FROM project_models")
    print(f"存量模型 {len(rows)} 个")
    g_tot = g_cut = 0
    for r in rows:
        sc = r["scene"] if isinstance(r["scene"], dict) else json.loads(r["scene"] or "{}")
        tot = cut = 0
        worst = []
        for f in sc.get("floors") or []:
            cols = (f.get("elements") or {}).get("columns") or []
            items = [{"outline": c.get("outline") or []} for c in cols]
            if not items:
                continue
            flags = find_dense_array_flags(items)
            tot += len(items); cut += sum(flags)
            if sum(flags):
                worst.append((sum(flags), len(items), f.get("name") or f.get("level")))
        g_tot += tot; g_cut += cut
        print(f"  项目 {r['pid'][:8]}  柱 {tot:6d}  密排阵列 {cut:6d} "
              f"= {(cut/tot if tot else 0):5.1%}")
        for c, n, name in sorted(worst, reverse=True)[:5]:
            print(f"      {c:5d}/{n:<6d} {name}")
    print(f"\n合计 柱 {g_tot} 根，其中密排阵列 {g_cut} = "
          f"{(g_cut/g_tot if g_tot else 0):.1%}")
    print("→ 这部分若不重建 scene 就重新导出训练集，模型会继续学到它们。")
    await db.disconnect()

asyncio.run(main())
