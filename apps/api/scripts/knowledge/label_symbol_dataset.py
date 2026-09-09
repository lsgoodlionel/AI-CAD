#!/usr/bin/env python3
"""给符号清单补上分类映射，并导出数据卡所需的统计。

清单本身只记「符号图 + 中文名 + 出处」（那是从图集里**读出来**的事实）；
分类是**我们的解释**，所以单独一步、单独存档 —— 改了映射规则重跑即可，
不必重新切图。

用法：python scripts/knowledge/label_symbol_dataset.py
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.knowledge.label_map import map_label, summarize   # noqa: E402

ROOT = (Path(__file__).resolve().parents[2] / "data" / "model3d"
        / "dataset" / "reference")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(ROOT))
    args = ap.parse_args()
    root = Path(args.root)
    src = root / "symbols_manifest.jsonl"
    if not src.exists():
        print(f"清单不存在：{src}")
        return 1

    rows = [json.loads(line) for line in src.open(encoding="utf-8")]
    mappings = []
    out = root / "symbols_labeled.jsonl"
    with out.open("w", encoding="utf-8") as fh:
        for row in rows:
            m = map_label(row.get("name", ""), note=row.get("note", ""))
            mappings.append(m)
            fh.write(json.dumps({**row, **m.to_dict()}, ensure_ascii=False) + "\n")

    stats = summarize(mappings)
    clean = sum(1 for r in rows if not r.get("warnings"))
    note_col = sum(1 for r in rows if r.get("name_role") == "note_column")
    stats.update({
        "entries": len(rows),
        "entries_without_warning": clean,
        "entries_name_from_note_column": note_col,
        "rotated_entries": sum(1 for r in rows if r.get("rotated")),
        "by_source": {},
    })
    for row in rows:
        key = row.get("source_key", "?")
        stats["by_source"][key] = stats["by_source"].get(key, 0) + 1

    (root / "symbols_stats.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"已标注 {len(rows)} 条 → {out}")
    print("按 domain：", stats["by_domain"])
    print("按 taxonomy：", stats["by_taxonomy"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
