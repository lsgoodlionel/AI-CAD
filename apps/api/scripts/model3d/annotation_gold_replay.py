"""在柱定案批（`data/model3d/gold/columns_final_v1.json`）**保留组的那 15 张图**上
复跑标注判据，看命中率是否跟着判读标签走。

## 这是弱证据，先说清楚

金标准记的是 `ref → ok/what`，**不记候选的几何**（批次的 `/tmp` 清单里只有
`ref → 图纸`）。所以做不到「逐格回测」，只能做**图纸级的方向性检验**：

    判读答「标高符号/尺寸/文字」的那些格所在的图 —— 命中率应当更高
    判读答「柱」的那些格所在的图         —— 命中率应当更低

每张图只有 1~2 格判读，图上其余候选没有标签，所以这**不是精确率**，
只是「判据有没有指向正确方向」。样本 26 格 / 15 张图，量级极小。

下表 `KEPT_CELLS` 从批次清单（`/tmp/col2_man.tsv` + `/tmp/col2.json`，
`mk_col2.py` 那一轮）誊出，之所以内嵌是因为那两份清单在容器 `/tmp` 里、
不在仓库中，而金标准目录不归本工作包改。

用法（容器内）：
    python scripts/model3d/annotation_gold_replay.py
"""
from __future__ import annotations

import asyncio
import collections
import gc
import os
import sys

import databases as databases_lib

sys.path.insert(0, os.getcwd())

from core.config import settings  # noqa: E402
from core.model3d.annotation_filter import find_annotation_flags  # noqa: E402
from core.model3d.element_recognizer import recognize  # noqa: E402
from core.model3d.geometry_extractor import extract_pdf_geometry  # noqa: E402
from core.storage import get_file_bytes  # noqa: E402

#: (批次编号, 图纸 id, 判读标签, 判读是否有把握)
KEPT_CELLS = [
    ("3FCC", "d462c95e-8593-4396-8684-9bd0562ce456", "column", True),  # S--六层平面图
    ("3KUX", "2ce1b37f-7917-4d52-8102-0022d257823e", "window", True),  # 建筑-竣工图--大歌剧厅台仓隔声隔振平面图
    ("3L3C", "742f2841-0f11-4150-b4ea-73719538655c", "dimension", True),  # 建筑-竣工图--二层平面图(四)
    ("3YCW", "ff6ab536-dbf3-43d5-af5b-abc9f5db2009", "dimension", True),  # 三层防火分区平面图
    ("4KAH", "a5cdefbf-9883-4b5a-8ee0-e76944b4e921", "beam", False),  # 建筑-竣工图--地下一层隔声隔振平面图（三）
    ("4YCX", "d7409798-555a-44f9-a4ec-1b3305336278", "text", True),  # 结构-竣工图--北区（小歌剧厅）三层结构平面图
    ("4YWM", "a8b1c8c3-b2d4-47e6-a1e8-c486fdf09315", "elevation_mark", True),  # 建筑-竣工图--二层平面图(三)
    ("93AY", "99a3f1f7-df23-4abb-bf94-ddac5b1e5c57", "elevation_mark", True),  # 建筑-竣工图--一层平面图(一)
    ("AJHN", "194f877d-cf7a-466a-8376-c10fbda0baff", "beam", False),  # 九层结构平面图
    ("ARWJ", "24369b7d-80a6-464b-9c8e-dfb9d91467dd", "text", True),  # 周边环境总平面图
    ("AYE4", "194f877d-cf7a-466a-8376-c10fbda0baff", "beam", True),  # 九层结构平面图
    ("E9WX", "0a8488ee-09a0-4713-ab23-73e229804add", "elevation_mark", True),  # 建筑-竣工图--一层平面图(三)
    ("EAFJ", "2ce1b37f-7917-4d52-8102-0022d257823e", "window", True),  # 建筑-竣工图--大歌剧厅台仓隔声隔振平面图
    ("ECPV", "a8b1c8c3-b2d4-47e6-a1e8-c486fdf09315", "dimension", True),  # 建筑-竣工图--二层平面图(三)
    ("FK9M", "26ce158f-f789-402c-8e4a-fda2908bce80", "elevation_mark", True),  # 六层防火分区平面图
    ("H4Y4", "99a3f1f7-df23-4abb-bf94-ddac5b1e5c57", "dimension", True),  # 建筑-竣工图--一层平面图(一)
    ("M9VA", "964a7d69-dd9e-4aca-bd3d-2089adb1d453", "hatch", False),  # S--三层平面图
    ("RCUH", "5fca2fc3-f891-4212-8962-6b43bd4d45a8", "column", True),  # 结构-竣工图--南区（大、中歌剧厅）一层结构平面图（二）
    ("RN33", "964a7d69-dd9e-4aca-bd3d-2089adb1d453", "text", True),  # S--三层平面图
    ("UHHA", "26ce158f-f789-402c-8e4a-fda2908bce80", "elevation_mark", True),  # 六层防火分区平面图
    ("VJRN", "742f2841-0f11-4150-b4ea-73719538655c", "elevation_mark", True),  # 建筑-竣工图--二层平面图(四)
    ("WKJJ", "d7409798-555a-44f9-a4ec-1b3305336278", "elevation_mark", True),  # 结构-竣工图--北区（小歌剧厅）三层结构平面图
    ("XKLM", "24369b7d-80a6-464b-9c8e-dfb9d91467dd", "equipment", False),  # 周边环境总平面图
    ("XMJM", "ff6ab536-dbf3-43d5-af5b-abc9f5db2009", "text", True),  # 三层防火分区平面图
    ("YAUM", "a5cdefbf-9883-4b5a-8ee0-e76944b4e921", "beam", False),  # 建筑-竣工图--地下一层隔声隔振平面图（三）
    ("YWFV", "d462c95e-8593-4396-8684-9bd0562ce456", "column", True),  # S--六层平面图
]

#: 判读标签里属于「图面标注/非实体」的那几个（`CRITERIA.md#columns` 的不算柱清单）
ANNOTATION_LABELS = {"elevation_mark", "dimension", "text", "hatch", "nothing"}


async def main() -> None:
    db = databases_lib.Database(settings.database_url)
    await db.connect()
    by_drawing: dict[str, list[str]] = collections.defaultdict(list)
    for _ref, did, what, _conf in KEPT_CELLS:
        by_drawing[did].append(what)

    rows = {str(r["id"]): r for r in await db.fetch_all(
        "SELECT id,title,discipline,file_key FROM drawings WHERE id = ANY(:ids)",
        {"ids": list(by_drawing)})}
    missing = [d for d in by_drawing if d not in rows]
    if missing:
        print("库里找不到的图（清单誊写误差或图已删）:", len(missing))

    groups: dict[str, list[float]] = collections.defaultdict(list)
    for did, labels in by_drawing.items():
        row = rows.get(did)
        if row is None:
            continue
        try:
            geom = extract_pdf_geometry(get_file_bytes(row["file_key"]))
            tr = await db.fetch_one(
                "SELECT scale_m_pt FROM drawing_transform WHERE drawing_id=:d",
                {"d": did})
            fe = recognize(geom, row["discipline"], did, drawing_title=row["title"],
                           scale_override=float(tr["scale_m_pt"]) if tr else None)
        except Exception as exc:  # noqa: BLE001
            print("skip", did, exc)
            continue
        cands = [c for c in fe.columns if len(c.get("outline") or []) >= 3]
        flags = find_annotation_flags(cands)
        rate = sum(1 for f in flags if f) / max(len(cands), 1)
        hits = [x in ANNOTATION_LABELS for x in labels]
        # 一张图上的几格标签可能不一致 —— **混合的单列一组**，不并进任何一边，
        # 否则「任一格是标注就算标注图」会把结论做出来。
        kind = "纯标注" if all(hits) else ("纯构件" if not any(hits) else "混合")
        groups[kind].append(rate)
        print(f"  {rate:6.1%}  {len(cands):5d} 候选  [{kind}] {labels}  {row['title'][:32]}")
        gc.collect()
    print()
    for kind, rates in sorted(groups.items()):
        avg = sum(rates) / max(len(rates), 1)
        print(f"{kind}组：{len(rates)} 张图，命中率均值 {avg:.1%}")
    await db.disconnect()


asyncio.run(main())
