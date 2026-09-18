#!/usr/bin/env python3
"""数理化教材 → 可核对全文（`data/knowledge/math_physics/<key>/book.md`）。

与识图标准那条管线同一套构件（`core.knowledge.text_extract` /
`markdown_writer`），只换清单与输出目录。**不新造抽取逻辑** ——
页码锚点（`## p.N` / `## 节N`）正是引用要落到的地方，格式必须一致。

用法：
    python scripts/knowledge/build_math_markdown.py            # 全部可直取的
    python scripts/knowledge/build_math_markdown.py --key strang-la
    python scripts/knowledge/build_math_markdown.py --pages 1-120   # 只抽这一段

**扫描件（`extract_method='ocr'`）默认跳过**：Knight《Physics》1450 页全量
OCR 代价高，要按章节定向做（`--key knight-physics --pages a-b --ocr`）。
跳过时会打印出来，不静默略过。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.knowledge import math_sources as ms            # noqa: E402
from core.knowledge.markdown_writer import write_book    # noqa: E402
from core.knowledge.text_extract import (                # noqa: E402
    extract_epub, extract_text_layer,
)

OUT_ROOT = Path(__file__).resolve().parents[2] / "data" / "knowledge" / ms.CACHE_DIRNAME


def _pages_spans(spec: str | None) -> list[tuple[int, int]]:
    """`183-190,391-398` → [(182, 190), (390, 398)]（0-based, 右开）。

    **逐段传给 `extract_ocr`，不取外包区间**：四段合计 29 页，外包却是 320 页 ——
    扫描件 OCR 一页以秒计，差的是十倍工夫。
    """
    spans: list[tuple[int, int]] = []
    for part in (spec or "").split(","):
        part = part.strip()
        if not part:
            continue
        lo, _, hi = part.partition("-")
        spans.append((int(lo) - 1, int(hi or lo)))
    return spans


def _pages_filter(spec: str | None):
    """`--pages 10-200,380-384` → 判定函数（1-based 闭区间，可多段）。

    多段是给扫描件用的：全书 OCR 代价高，而要取证的只是几节
    （Knight《Physics》实测 §6.1 平衡、§12.4 惯性矩、§12.8 静力平衡
    各占几页）。一次指定几段，比跑四遍再手工拼稳。
    """
    if not spec:
        return lambda _index: True
    ranges = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        lo, _, hi = part.partition("-")
        ranges.append((int(lo), int(hi or lo)))
    return lambda index: any(lo <= index + 1 <= hi for lo, hi in ranges)


def _extract(source, keep, spans: list[tuple[int, int]]):
    """按来路取页。`span` 是要 OCR 的**连续区间**（0-based, 右开）。

    **必须把区间下推给 `extract_ocr`**：它默认逐页 OCR 全书，在外面过滤
    等于把 1450 页全扫一遍再扔掉 1420 页 —— 实测那是 40 分钟起步的活，
    而真正要取证的只有几节。文本层来路没有这个问题（抽取本身很便宜）。
    """
    if source.extract_method == "epub":
        stream = extract_epub(source.path)
    elif source.extract_method == "ocr":
        from core.knowledge.text_extract import extract_ocr
        for span in (spans or [(0, source.pages)]):
            for page in extract_ocr(source.path, page_range=span):
                if keep(page.index):
                    yield page
        return
    else:
        stream = extract_text_layer(source.path)
    for page in stream:
        if keep(page.index):
            yield page


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", action="append", help="只处理这些 key（可重复）")
    ap.add_argument("--pages", help="页范围，如 200-320（1-based 闭区间）")
    ap.add_argument("--ocr", action="store_true", help="允许处理扫描件")
    ap.add_argument("--out", default=str(OUT_ROOT))
    args = ap.parse_args()

    out_root = Path(args.out)
    keep = _pages_filter(args.pages)
    chosen = [s for s in ms.SOURCES if not args.key or s.key in args.key]
    for source in chosen:
        if source.is_scanned and not args.ocr:
            print(f"⏭  {source.key}：扫描件，需 --ocr 且宜按章节定向（{source.pages} 页）")
            continue
        print(f"▶  {source.key}（{source.pages} 页，{source.extract_method}）", flush=True)
        pages = list(_extract(source, keep, _pages_spans(args.pages)))
        stats = write_book(source, pages, out_root)
        print(f"   抽到 {stats['extracted_pages']} 页 / {stats['char_count']} 字"
              f"，近空页 {len(stats['near_empty_pages'])}")
    print(f"\n输出：{out_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
