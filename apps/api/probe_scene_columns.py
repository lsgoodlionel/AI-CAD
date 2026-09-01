"""全库形状分布与删除量：直接读**已建场景里落库的柱**，不重新解析 PDF。

逐张 PDF 跑识别约 30 秒，量全库要几十小时；而 `project_models.scene`
里已经存着两个真实工程的全部柱轮廓（24664 根），够回答两个问题：

1. 去重后**真有多少三角形** —— 用来复核 2026-08-28 那条
   「候选里三顶点多边形占 0%」的结论（那是原始点数口径，见
   `core/model3d/true_extent.py` 模块文档）。
2. 真实尺寸判据的**删除量上下界** —— 图层路径与猜测路径窗口不同，
   而场景里没记每根柱走的哪条路，所以只能给区间。
   精确值由 `probe_before_after.py` 在真图上逐张对比得出。

**口径提醒**：该场景建于 2026-08-20，**早于密排阵列闸**，所以这里的
柱数含座椅。它衡量的是判据单独作用在旧总体上的量，不是「座椅闸之后
再删多少」。

用法（在能连库的容器里）::

    python probe_scene_columns.py
"""
import asyncio, collections, json

import databases as databases_lib

from core.config import settings
from core.model3d.element_recognizer import (
    _COLUMN_ABSURD_MAX_M, _COLUMN_ABSURD_MIN_M, _COLUMN_LAYER_MAX_ASPECT,
    _COLUMN_MAX_ASPECT, _COLUMN_SIZE,
)
from core.model3d.true_extent import min_area_rect

SQL = """
SELECT f->'elements'->'columns' AS cols
FROM project_models m
JOIN projects p ON p.id = m.project_id,
     jsonb_array_elements(m.scene->'floors') f
WHERE m.scene IS NOT NULL
"""


def _window(outlines, lo, hi, asp, label):
    dropped = collections.Counter()
    total = drop = 0
    for o in outlines:
        total += 1
        extent = min_area_rect(o)
        if extent is None:
            drop += 1; dropped["退化(共线/点不足)"] += 1; continue
        long_m, short_m = extent
        if not (lo <= short_m <= hi and lo <= long_m <= hi):
            drop += 1; dropped["真尺寸出窗口"] += 1
        elif long_m / max(short_m, 1e-9) >= asp:
            drop += 1; dropped["真长宽比超限"] += 1
    print(f"  {label}: 删 {drop}/{total} = {drop/max(total,1):.1%}  {dict(dropped)}")


async def main():
    db = databases_lib.Database(settings.database_url); await db.connect()
    outlines, corner_hist, aabb_square_but_slender = [], collections.Counter(), 0
    for row in await db.fetch_all(SQL):
        # jsonb 经 `databases` 回来是 str，也可能已解析 —— 两种都收
        cols = row["cols"]
        if isinstance(cols, str):
            cols = json.loads(cols)
        for c in cols or []:
            o = [(float(x), float(y)) for x, y in (c.get("outline") or [])]
            if len(o) < 3:
                continue
            outlines.append(o)
            corner_hist[len({(round(x, 6), round(y, 6)) for x, y in o})] += 1
            xs = [p[0] for p in o]; ys = [p[1] for p in o]
            aw, ah = max(xs) - min(xs), max(ys) - min(ys)
            extent = min_area_rect(o)
            if (extent and max(aw, ah) / max(min(aw, ah), 1e-9) < 1.3
                    and extent[0] / max(extent[1], 1e-9) >= _COLUMN_MAX_ASPECT):
                aabb_square_but_slender += 1

    n = len(outlines)
    print(f"落库柱 {n}")
    print("\n--- 去重后相异顶点数（三角形口径的复核） ---")
    for k in sorted(corner_hist):
        print(f"  {k:>2} 顶点: {corner_hist[k]} ({corner_hist[k]/max(n,1):.1%})")
    print(f"\n包围盒近方(<1.3)但真实细长(>={_COLUMN_MAX_ASPECT}): "
          f"{aabb_square_but_slender} ({aabb_square_but_slender/max(n,1):.1%})"
          " —— 现有尺寸判据结构性看不见的一类")
    print("\n--- 真实尺寸判据的删除量区间 ---")
    lo, hi = _COLUMN_SIZE
    _window(outlines, lo, hi, _COLUMN_MAX_ASPECT,
            f"全按【猜测路径】窗口({lo}~{hi}m, {_COLUMN_MAX_ASPECT}:1) 上界")
    _window(outlines, _COLUMN_ABSURD_MIN_M, _COLUMN_ABSURD_MAX_M,
            _COLUMN_LAYER_MAX_ASPECT,
            f"全按【图层路径】窗口({_COLUMN_ABSURD_MIN_M}~{_COLUMN_ABSURD_MAX_M}m,"
            f" {_COLUMN_LAYER_MAX_ASPECT}:1) 下界")
    await db.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
