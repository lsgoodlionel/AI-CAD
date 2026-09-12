"""给没记候选数的旧批次回填 `drawing_candidates`。

用法（容器内、注入当前代码的目录里）：

    # 用已算好的计数（如 /tmp/col3_counts.tsv）
    python -m scripts.model3d.gold_backfill_counts --batch-dir /tmp/gold_col3 \\
        --counts /tmp/col3_counts.tsv
    # 或现场重算（与生成器同一个识别调用）
    python -m scripts.model3d.gold_backfill_counts --batch-dir /tmp/gold_wall3

col3、wall3、beam3 是在生成器记下候选数之前出的批。没有这一列，回收端的
层内加权（`ingest._cell_weights`）只能整批退回等权 —— 而等权会让稀疏图在
层内被严重高估（每图至多 2 格，不论该图有 3 个还是 800 个候选）。

重算用与生成器**完全相同**的调用（`view_type="plan"`、transform 表的比例），
识别器未改动时结果可复现。数不出来的图写 -1，回收端据此退回等权 ——
不写 0：0 会被当成「这张图没有候选」，那是另一回事。
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
from pathlib import Path

from scripts.model3d.gold_batch import KIND_SPEC, MANIFEST_HEADER

COUNT_COL = "drawing_candidates"


def merge_counts(header: list[str], rows: list[dict],
                 counts: dict[str, int]) -> tuple[list[str], list[dict]]:
    """把计数并进 manifest：列序与生成器一致；已有该列时以新数为准（幂等）。"""
    target = MANIFEST_HEADER.split("\t")
    new_header = ([c for c in target if c in header or c == COUNT_COL]
                  + [c for c in header if c not in target])
    merged = []
    for row in rows:
        n = counts.get(row.get("drawing_id"))
        value = str(n) if n is not None else row.get(COUNT_COL) or "-1"
        merged.append({**row, COUNT_COL: value})
    return new_header, merged


def _read_manifest(path: Path) -> tuple[list[str], list[dict]]:
    with path.open(encoding="utf-8") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        return list(reader.fieldnames or []), list(reader)


def _write_manifest(path: Path, header: list[str], rows: list[dict]) -> None:
    lines = ["\t".join(header)] + ["\t".join(str(r.get(c, "")) for c in header) for r in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _read_counts(path: Path) -> dict[str, int]:
    with path.open(encoding="utf-8") as fh:
        return {r["drawing_id"]: int(r["drawing_candidates"])
                for r in csv.DictReader(fh, delimiter="\t")}


async def _recount(drawing_ids: list[str], attr: str) -> dict[str, int]:
    import databases as databases_lib

    from core.config import settings
    from core.model3d.element_recognizer import recognize
    from core.model3d.geometry_extractor import extract_pdf_geometry
    from core.storage import get_file_bytes

    db = databases_lib.Database(settings.database_url); await db.connect()
    counts: dict[str, int] = {}
    try:
        for did in drawing_ids:
            r = await db.fetch_one(
                "SELECT d.file_key, d.title, d.discipline, t.scale_m_pt FROM drawings d "
                "LEFT JOIN drawing_transform t ON t.drawing_id = d.id WHERE d.id = :d", {"d": did})
            try:
                geom = extract_pdf_geometry(get_file_bytes(r["file_key"]))
                fe = recognize(geom, r["discipline"], did, drawing_title=r["title"],
                               scale_override=r["scale_m_pt"], view_type="plan")
                counts[did] = len(getattr(fe, attr))
            except Exception as exc:  # noqa: BLE001 - 单图失败写 -1，但要说出来
                counts[did] = -1
                print(f"  ✗ {did[:8]} {type(exc).__name__}", flush=True)
            print(f"  {did[:8]} {counts[did]:6d}", flush=True)
    finally:
        await db.disconnect()
    return counts


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch-dir", type=Path, required=True)
    ap.add_argument("--counts", type=Path, default=None, help="预先算好的 drawing_id→候选数 TSV")
    args = ap.parse_args()
    manifest = args.batch_dir / "manifest.tsv"
    meta = json.loads((args.batch_dir / "meta.json").read_text(encoding="utf-8"))
    header, rows = _read_manifest(manifest)
    if args.counts:
        counts = _read_counts(args.counts)
    else:
        dids = sorted({r["drawing_id"] for r in rows if r.get("group") in ("kept", "dup")})
        counts = await _recount(dids, KIND_SPEC[meta["kind"]]["attr"])
    new_header, merged = merge_counts(header, rows, counts)
    _write_manifest(manifest, new_header, merged)
    kept = [r for r in merged if r.get("group") == "kept"]
    unknown = sum(1 for r in kept if int(r[COUNT_COL]) <= 0)
    print(f"回填完成：{len(kept)} 个被测格，缺数 {unknown} 格"
          + ("（回收时将整批退回等权）" if unknown else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
