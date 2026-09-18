#!/usr/bin/env python3
"""数理化教材全文 → `regulation_books`（`doc_kind='textbook'`）。

**为什么标 textbook**：migration 050 加 `doc_kind` 就是为了这件事 ——
教材不是审图判据。RAG 检索到「梁的挠曲微分方程」时必须知道它来自教材，
而不是当成规范条文去指控图纸违规。

**不上传原件**：识图标准那批把图集 PDF 传进了 MinIO（施工现场要看原件）。
这批是数理教材，平台上没有「看原件」的场景，派生全文够用于取证与核对，
少一份分发就少一分版权风险。

**幂等**：按 `std_no`（`KB-<key>`）判重，重跑只更新。

用法：
    python scripts/knowledge/ingest_math_to_db.py --dry-run
    python scripts/knowledge/ingest_math_to_db.py
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.knowledge import math_sources as ms                      # noqa: E402
from scripts.knowledge.build_math_markdown import OUT_ROOT         # noqa: E402


def _full_text(source) -> tuple[str, int]:
    """读已生成的 book.md。**没有就不编** —— 返回空，并由调用方点名跳过。"""
    path = OUT_ROOT / source.key / "book.md"
    if not path.exists():
        return "", 0
    text = path.read_text(encoding="utf-8")
    return text, len(text)


async def _run(args) -> int:
    import asyncpg

    from core.config import settings

    dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(dsn)
    skipped: list[str] = []
    try:
        rows = 0
        for source in ms.SOURCES:
            text, chars = _full_text(source)
            if not text:
                skipped.append(f"{source.key}（没有 book.md，先跑 build_math_markdown）")
                continue
            print(f"  {source.key:26} {chars:>9} 字 / {source.pages} 页")
            if args.dry_run:
                rows += 1
                continue
            await conn.execute(
                """
                INSERT INTO regulation_books
                    (title, std_no, discipline, status, source_type, doc_kind,
                     full_text, text_chars, page_count, extract_method)
                VALUES ($1, $2, $3, 'active', 'file_import', 'textbook',
                        $4, $5, $6, $7)
                ON CONFLICT (std_no) DO UPDATE SET
                    title = EXCLUDED.title,
                    doc_kind = EXCLUDED.doc_kind,
                    full_text = EXCLUDED.full_text,
                    text_chars = EXCLUDED.text_chars,
                    page_count = EXCLUDED.page_count,
                    extract_method = EXCLUDED.extract_method,
                    updated_at = now()
                """,
                source.title, source.std_no, source.discipline,
                text, chars, source.pages, source.extract_method)
            rows += 1
    finally:
        await conn.close()
    for note in skipped:
        print(f"  ⏭  {note}")
    print(f"\n{'（dry-run）' if args.dry_run else ''}入库 {rows} 本"
          f"，跳过 {len(skipped)} 本")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    return asyncio.run(_run(ap.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
