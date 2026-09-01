#!/usr/bin/env python3
"""资料 → Markdown（第②层「识别全文」）。

文本层 / EPUB 现抽现写；扫描件从 OCR 缓存读（`ocr_cache.py` 先跑）。
缓存不全时**照样出文件**，但在 frontmatter 与正文开头如实标出缺页 ——
半成品要能看出是半成品，不能看起来像完本。

用法：
    python scripts/knowledge/build_markdown.py --all
    python scripts/knowledge/build_markdown.py --key 22G101-1
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.knowledge import markdown_writer, source_registry as sr, text_extract as te  # noqa: E402
from scripts.knowledge.ocr_cache import load_cache                                    # noqa: E402

OUT_ROOT = Path(__file__).resolve().parents[2] / "data" / "knowledge" / "drawing_standards"


def pages_for(source) -> list:
    if source.is_scanned:
        return load_cache(source.key)
    return list(te.extract(source))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", action="append", default=[])
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--out", default=str(OUT_ROOT))
    args = ap.parse_args()
    logging.disable(logging.INFO)

    keys = args.key or ([s.key for s in sr.all_sources()] if args.all else [])
    if not keys:
        ap.error("需要 --key 或 --all")

    out_root = Path(args.out)
    for key in keys:
        source = sr.get(key)
        pages = pages_for(source)
        if not pages:
            print(f"跳过 {key}：没有可用页（扫描件请先跑 ocr_cache.py）", flush=True)
            continue
        stats = markdown_writer.write_book(source, pages, out_root)
        miss = len(stats["missing_pages"])
        conf = stats["mean_confidence"]
        print(f"{key:<28} {stats['extracted_pages']:>4}/{source.pages} 页 "
              f"{stats['char_count']:>7} 字 "
              f"conf={conf if conf is None else round(conf, 3)} "
              f"缺{miss} 低置信{len(stats['low_confidence_pages'])} "
              f"纯图页{len(stats['near_empty_pages'])}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
