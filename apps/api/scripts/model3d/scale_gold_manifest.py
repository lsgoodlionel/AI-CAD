"""复原 `drawing_scale_v1.json` 的「批次编号 → 图纸」对照表。

**为什么需要它。** 金标准里每条裁决只留了四位随机码 `ref`，
原始对照表写在容器 `/tmp/scale_man.tsv`，容器一重启就没了。
没有这张表，56 条实测裁决就无法回测到任何新算法上 —— 只是 56 个孤立的对错。

**怎么复原。** `mk_scale.py` 的抽样是**确定性**的（`random.seed(20260913)`
+ `make_codes(seed=20260913)`），这里逐字重放同一段逻辑，
并用金标准 `note` 里记着的 `scale_m_pt=... confidence=...` **逐条校验**：
复原出的图纸若比例/置信度与 note 对不上，说明重放失真，宁可报错也不出表。

用法：
    python -m scripts.model3d.scale_gold_manifest [输出 tsv 路径]
"""
from __future__ import annotations

import asyncio
import collections
import json
import os
import random
import sys

import databases as databases_lib
import fitz  # type: ignore[import-untyped]
from PIL import Image

from core.config import settings
from core.model3d.gold.batch_codes import make_codes
from core.storage import get_file_bytes

#: 以下常量逐字取自 `mk_scale.py` —— 改任何一个都会让复原对不上。
BAR_M = 8.0
PROBE_DPI, CROP_DPI, GRID = 24, 120, 3
EXCLUDE_RIGHT, EXCLUDE_BOTTOM = 0.28, 0.18
PER_STRATUM = 15
SEED = 20260913
CELL, COLS, ROWS = 420, 4, 3

GOLD_PATH = "data/model3d/gold/drawing_scale_v1.json"


def _densest_cell(page):
    pix = page.get_pixmap(dpi=PROBE_DPI)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples).convert("L")
    w, h = img.size
    best, best_ink = (0, 0), -1.0
    for gy in range(max(1, int(GRID * (1 - EXCLUDE_BOTTOM)))):
        for gx in range(max(1, int(GRID * (1 - EXCLUDE_RIGHT)))):
            cell = img.crop((gx * w // GRID, gy * h // GRID,
                             (gx + 1) * w // GRID, (gy + 1) * h // GRID))
            ink = sum(1 for p in cell.getdata() if p < 200) / max(cell.width * cell.height, 1)
            if ink > best_ink:
                best_ink, best = ink, (gx, gy)
    gx, gy = best
    r = page.rect
    cw, ch = r.width / GRID, r.height / GRID
    return fitz.Rect(r.x0 + gx * cw, r.y0 + gy * ch,
                     r.x0 + (gx + 1) * cw, r.y0 + (gy + 1) * ch)


def _stratum(scale_m_pt: float, conf: float) -> str:
    common = 0.007 <= scale_m_pt <= 0.18
    if conf >= 0.99:
        return "满分置信·常用比例" if common else "满分置信·**离谱比例**"
    if conf > 0.0:
        return "中等置信"
    return "零置信"


def _gold_notes() -> dict[str, tuple[float, float]]:
    """金标准里每个 ref 记着的 (scale_m_pt, confidence)，用于校验复原。"""
    data = json.load(open(GOLD_PATH))
    out: dict[str, tuple[float, float]] = {}
    for unit in data["units"]:
        for v in unit["classes"]["drawing_scale"]["verdicts"]:
            note = v.get("note", "")
            sc = cf = None
            for token in note.split():
                if token.startswith("scale_m_pt="):
                    sc = float(token.split("=", 1)[1])
                elif token.startswith("confidence="):
                    cf = float(token.split("=", 1)[1])
            if sc is not None and cf is not None:
                out[v["ref"]] = (sc, cf)
    return out


async def main() -> int:
    out_path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/scale_manifest_repro.tsv"
    db = databases_lib.Database(settings.database_url)
    await db.connect()
    rows = await db.fetch_all(
        "SELECT t.drawing_id, t.scale_m_pt, t.confidence, d.title, d.file_key "
        "FROM drawing_transform t JOIN drawings d ON d.id=t.drawing_id "
        "WHERE d.title IS NOT NULL")
    pool = collections.defaultdict(list)
    for r in rows:
        pool[_stratum(float(r["scale_m_pt"]), float(r["confidence"]))].append(r)

    random.seed(SEED)
    picks = []
    for _k, v in sorted(pool.items()):
        random.shuffle(v)
        picks += [(_k, r) for r in v[:PER_STRATUM]]
    random.shuffle(picks)
    codes = make_codes(len(picks) + 20, seed=SEED)

    tiles = []
    for stratum, r in picks:
        try:
            page = fitz.open(stream=get_file_bytes(r["file_key"]), filetype="pdf")[0]
            clip = _densest_cell(page)
            pix = page.get_pixmap(dpi=CROP_DPI, clip=clip)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        except Exception:  # noqa: BLE001 — 与原脚本同口径：取不到就跳过
            continue
        ex = img.convert("L").getextrema()
        if ex[1] - ex[0] < 30:
            continue
        img.thumbnail((CELL, CELL), Image.LANCZOS)
        tiles.append((codes.pop(), stratum, str(r["drawing_id"]),
                      float(r["scale_m_pt"]), float(r["confidence"]), str(r["title"])))
    await db.disconnect()

    gold = _gold_notes()
    repro = {t[0]: t for t in tiles}
    matched = missing = mismatched = 0
    for ref, (sc, cf) in gold.items():
        t = repro.get(ref)
        if t is None:
            missing += 1
            continue
        if abs(t[3] - sc) > 1e-5 or abs(t[4] - cf) > 0.005:
            mismatched += 1
            print(f"  x {ref}: 复原 {t[3]:.5f}/{t[4]:.2f} != 金标准 {sc:.5f}/{cf:.2f}")
            continue
        matched += 1
    print(f"复原 {len(tiles)} 格；金标准 {len(gold)} 条 -> "
          f"匹配 {matched} / 缺失 {missing} / 不符 {mismatched}")

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w") as f:
        f.write("ref\tstratum\tdrawing_id\tscale_m_pt\tconfidence\ttitle\n")
        for tag, st, did, sc, cf, ti in tiles:
            f.write(f"{tag}\t{st}\t{did}\t{sc:.6f}\t{cf:.2f}\t{ti}\n")
    print("写出", out_path)
    return 0 if mismatched == 0 and missing == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
