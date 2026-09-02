#!/usr/bin/env python3
"""识图标准资料 → 规范知识库（三层可读）+ 标准图集登记。

落地到既有结构，不另起炉灶：
- `regulation_books`：第②层「识别全文」（`full_text` / `extract_method` /
  `page_count`），并按 migration 050 标 `doc_kind`
  （atlas=图集做法、textbook=教材，**都不是审图判据**）；
- `standard_drawings`：10 本标准设计图集的登记表（此前是空表）；
- MinIO `atlases` 桶：第①层「PDF 原件」，`file_key` 回写两张表。

**幂等**：按 `std_no`（图集）/ `title`（教材）判重，重跑只更新不重复插入。

用法：
    python scripts/knowledge/ingest_to_db.py --upload      # 含原件上传
    python scripts/knowledge/ingest_to_db.py --dry-run
"""
from __future__ import annotations

import argparse
import asyncio
import os
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.knowledge import source_registry as sr        # noqa: E402
from scripts.knowledge.build_markdown import OUT_ROOT   # noqa: E402

ATLAS_BUCKET = "atlases"


class _DryRun(Exception):
    """dry-run 用异常回滚事务 —— 比「先查后写」少一条代码路径。"""


def _full_text(source) -> tuple[str, int]:
    """读已生成的 book.md 作为第②层全文。**没有就不编** —— 返回空。"""
    path = OUT_ROOT / source.key / "book.md"
    if not path.exists():
        return "", 0
    text = path.read_text(encoding="utf-8")
    return text, len(text)


def _upload(source, conn_note: list[str]) -> str | None:
    from core.storage import upload_file

    suffix = source.path.suffix.lower()
    key = f"knowledge/{source.key}{suffix}"
    try:
        # `upload_file` 收的是 bytes，不是文件对象。
        upload_file(source.path.read_bytes(), key, bucket=ATLAS_BUCKET,
                    content_type="application/pdf" if suffix == ".pdf"
                    else "application/epub+zip")
        return key
    except Exception as exc:                            # noqa: BLE001
        # 上传失败不该让整批入库失败，但**必须说出来**，
        # 否则第①层（原件）会静默缺失而无人知道。
        conn_note.append(f"{source.key} 原件上传失败：{exc}")
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--upload", action="store_true", help="同时上传原件到 MinIO")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    logging.disable(logging.INFO)

    return asyncio.run(_run(args))


async def _run(args) -> int:
    import asyncpg

    from core.config import settings

    # 本机开发栈把 Postgres 重映射到 5434（见 infra/docker-compose.dev.yml），
    # 而 settings 里写的是容器内的 5432。允许用环境变量覆盖，别硬改配置。
    dsn = os.environ.get("CAD_KNOWLEDGE_DSN") or settings.database_url
    dsn = dsn.replace("+asyncpg", "").replace("+psycopg2", "")
    problems: list[str] = []
    rows = 0
    atlases = 0

    conn = await asyncpg.connect(dsn)
    try:
        async with conn.transaction():
            for source in sr.all_sources():
                text, chars = _full_text(source)
                if not text:
                    problems.append(
                        f"{source.key} 无 book.md，跳过（先跑 build_markdown）")
                    continue
                file_key = (_upload(source, problems)
                            if args.upload and not args.dry_run else None)
                doc_kind = "atlas" if source.kind == "atlas" else "textbook"
                # 书名已自带《》时不再套一层（`《钢结构设计标准》图示`）。
                title = source.title if not source.std_no else (
                    f"{source.title} {source.std_no}"
                    if source.title.startswith("《")
                    else f"《{source.title}》{source.std_no}")
                if args.dry_run:
                    print(f"  [dry] {doc_kind:<8} {source.std_no or '-':<12}"
                          f" {chars:>7} 字  {title[:44]}")
                    rows += 1
                    atlases += 1 if source.kind == "atlas" else 0
                    continue

                await conn.execute(
                    """
                    INSERT INTO regulation_books
                        (title, std_no, discipline, publisher, status,
                         source_type, doc_kind, file_key, full_text,
                         text_chars, page_count, extract_method)
                    VALUES ($1, $2, $3, $4, 'active', 'file_import', $5, $6,
                            $7, $8, $9, $10)
                    ON CONFLICT (std_no) DO UPDATE SET
                        title = EXCLUDED.title,
                        doc_kind = EXCLUDED.doc_kind,
                        full_text = EXCLUDED.full_text,
                        text_chars = EXCLUDED.text_chars,
                        page_count = EXCLUDED.page_count,
                        extract_method = EXCLUDED.extract_method,
                        file_key = COALESCE(EXCLUDED.file_key,
                                            regulation_books.file_key),
                        updated_at = now()
                    """,
                    title, source.std_no or f"KB-{source.key}",
                    source.discipline,
                    "中国建筑标准设计研究院" if source.kind == "atlas" else None,
                    doc_kind, file_key, text, chars, source.pages,
                    source.extract_method)
                rows += 1

                if source.kind == "atlas":
                    await conn.execute(
                        """
                        INSERT INTO standard_drawings
                            (code, title, category, version, status, file_key)
                        VALUES ($1, $2, $3, $4, 'published', $5)
                        ON CONFLICT (code) DO UPDATE SET
                            title = EXCLUDED.title,
                            category = EXCLUDED.category,
                            file_key = COALESCE(EXCLUDED.file_key,
                                                standard_drawings.file_key)
                        """,
                        source.std_no, source.title, source.discipline,
                        source.std_no, file_key)
                    atlases += 1
            if args.dry_run:
                raise _DryRun()
    except _DryRun:
        pass
    finally:
        await conn.close()

    print(f"\n入库 {rows} 本（其中标准图集 {atlases} 本）"
          f"{'（dry-run，未提交）' if args.dry_run else ''}")
    if problems:
        print("如实报出的问题：")
        for p in problems:
            print("  -", p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
