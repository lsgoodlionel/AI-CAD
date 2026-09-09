#!/usr/bin/env python3
"""扫描件逐页 OCR → 缓存 jsonl（可断点续跑、可并行）。

**为什么要缓存层**：全批 1359 页 OCR 是小时量级的活，而 md 生成、图例切分、
条款抽取都要反复读这些文字。识别一次落盘，后续全部从缓存读 ——
否则每调一次格式就重跑三小时。

**断点续跑**：已有缓存的页直接跳过。中断后重跑只补缺页。
**并行**：按页分片多进程，每进程独立持有 RapidOCR 实例
（onnxruntime session 不可跨进程共享）。

**并行走进程级分片而非 ProcessPoolExecutor**：实测后者在 macOS 上
与 onnxruntime 同用时整池死锁（4 个 worker 一个没起来，主进程 0% CPU 挂住）。
分片是 `--shard i/N`（第 i 个进程只做 `页码 % N == i` 的页），
各写各的缓存文件，由 shell 并起 N 个进程 —— 不共享任何状态，无从死锁。

用法：
    scripts/knowledge/ocr_all.sh                       # 全批并行
    python scripts/knowledge/ocr_cache.py --key 22G101-1 --shard 0/4
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.knowledge import source_registry as sr   # noqa: E402

#: 缓存放在**仓库之外**。它是可再生的中间产物，不该进版本库；
#: 更要紧的是，开发用的 `uvicorn --reload` 监视整个 `apps/api/` 目录 ——
#: 往里面高频追加 jsonl 会让文件监视器持续唤醒（实测 cad_api 容器
#: 因此长期占用 868% CPU，把 OCR 自己饿到 40%）。
CACHE_DIR = Path(
    os.environ.get("CAD_KNOWLEDGE_CACHE")
    or (Path.home() / ".cache" / "cad-knowledge" / "ocr")
)


def cache_path(key: str, shard: int | None = None) -> Path:
    if shard is None:
        return CACHE_DIR / f"{key}.pages.jsonl"
    return CACHE_DIR / f"{key}.pages.part{shard}.jsonl"


def shard_paths(key: str) -> list[Path]:
    """本 key 的全部缓存分片（含未分片的主文件）。"""
    return sorted(CACHE_DIR.glob(f"{key}.pages*.jsonl"))


def _done_pages(path: Path) -> set[int]:
    """已完成的页码。**逐行容错**：进程被杀时最后一行可能截断，
    整份缓存不该因此报废。"""
    done: set[int] = set()
    if not path.exists():
        return done
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            try:
                done.add(json.loads(line)["index"])
            except Exception:                       # noqa: BLE001
                continue
    return done


def run_source(key: str, *, dpi: int, shard: int = 0,
               shard_count: int = 1) -> dict:
    """识别本分片负责的页并追加到分片缓存。已识别的页跳过。"""
    logging.disable(logging.INFO)
    import fitz

    from core.knowledge import text_extract as te

    source = sr.get(key)
    path = cache_path(key, shard if shard_count > 1 else None)
    path.parent.mkdir(parents=True, exist_ok=True)
    done = _done_pages(path)
    todo = [i for i in range(source.pages)
            if i % shard_count == shard and i not in done]
    if not todo:
        return {"key": key, "shard": shard, "new": 0, "skipped": len(done)}

    doc = fitz.open(source.path)
    written = 0
    try:
        with path.open("a", encoding="utf-8") as fh:
            for i in todo:
                page = doc[i]
                tokens, scores = te.ocr_page(page, dpi=dpi)
                rect = page.rect
                text, side = te._tokens_to_text(tokens, rect.width, rect.height)
                fh.write(json.dumps({
                    "index": i,
                    "text": text,
                    "sidebar": side,
                    "confidence": (sum(scores) / len(scores)) if scores else None,
                    "token_count": len(tokens),
                    "low_conf": [t["text"] for t in tokens
                                 if t.get("confidence", 1.0) < te.LOW_CONFIDENCE],
                    "dpi": dpi,
                    # bbox 一并留存：图例切分与符号定位要用，重跑 OCR 代价太大。
                    "tokens": [{"t": t["text"],
                                "b": [round(v, 1) for v in t["bbox"]],
                                "c": round(t.get("confidence", 1.0), 3)}
                               for t in tokens if t.get("bbox")],
                }, ensure_ascii=False) + "\n")
                fh.flush()
                written += 1
                if written % 5 == 0:
                    print(f"  [{key}#{shard}] {written}/{len(todo)}", flush=True)
    finally:
        doc.close()
    return {"key": key, "shard": shard, "new": written, "skipped": len(done)}


def load_cache(key: str) -> list[dict]:
    """读缓存，按页码排序。缺页如实缺 —— 不用空串填充冒充完整。"""
    rows: dict[int, dict] = {}
    for path in shard_paths(key):
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except Exception:                   # noqa: BLE001
                    continue
                rows[row["index"]] = row
    return [rows[i] for i in sorted(rows)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", action="append", default=[])
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--shard", default="0/1",
                    help="i/N —— 本进程只做页码 %% N == i 的页")
    ap.add_argument("--dpi", type=int, default=200)
    args = ap.parse_args()

    keys = args.key or ([s.key for s in sr.all_sources() if s.is_scanned]
                        if args.all else [])
    if not keys:
        ap.error("需要 --key 或 --all")

    shard, _, shard_count = args.shard.partition("/")
    shard, shard_count = int(shard), int(shard_count or 1)
    os.environ.setdefault("OMP_NUM_THREADS", "2")
    for key in keys:
        if not sr.get(key).is_scanned:
            print(f"跳过 {key}：非扫描件（extract_method="
                  f"{sr.get(key).extract_method}）", flush=True)
            continue
        print(f"== OCR {key} ({sr.get(key).pages}页) shard {shard}/{shard_count}",
              flush=True)
        print("  ", run_source(key, dpi=args.dpi, shard=shard,
                               shard_count=shard_count), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
