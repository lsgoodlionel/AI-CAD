"""座椅闸的**场景级**影响：进到模型里的柱少了多少根。

`d7002dc` 的教训：识别器层面的增减进不到模型 —— 中间隔着建模的图纸
筛选（角色/楼层归属/尺度离群/轴网自洽），实测衰减 **70 倍**。所以
「对模型的影响」必须跑 `build_scene`，不能拿 `recognize()` 的数换算。

用法：python probe_seat_scenelevel.py <project_id> <on|off>
  on  = 座椅闸照常
  off = 把 `find_dense_array_flags` 打成恒 False（等价于闸不存在）

**不落库** —— 只在内存里建，结果写 /tmp/seat_scene_<pid>_<mode>.json。
两个模式分两个进程跑：一次建模内存占用高，同进程跑两遍容易被 OOM。
"""
import asyncio, json, sys, time
import databases as dbl
from core.config import settings

PID, MODE = sys.argv[1], sys.argv[2]
assert MODE in ("on", "off")

if MODE == "off":
    # 闸「不存在」的对照：识别器是 `from .dense_array_filter import ...`，
    # 所以要替换识别器模块里的那个名字，改原模块无效。
    import core.model3d.element_recognizer as er
    er.find_dense_array_flags = lambda els, **kw: [False] * len(els or [])

from services.model_builder import build_scene


async def main():
    db = dbl.Database(settings.database_url, min_size=1, max_size=4)
    await db.connect()
    t0 = time.time()
    scene, _meta = await build_scene(db, PID)
    dt = time.time() - t0
    kinds = ("columns", "walls", "beams", "slabs", "pipes", "equipment")
    tot = {k: 0 for k in kinds}
    for fl in scene.get("floors") or []:
        els = fl.get("elements") or {}
        for k in kinds:
            tot[k] += len(els.get(k) or [])
    out = {"pid": PID, "mode": MODE, "seconds": round(dt, 1),
           "floors": len(scene.get("floors") or []), "totals": tot}
    json.dump(out, open(f"/tmp/seat_scene_{PID[:8]}_{MODE}.json", "w"))
    print(json.dumps(out, ensure_ascii=False))
    await db.disconnect()

asyncio.run(main())
