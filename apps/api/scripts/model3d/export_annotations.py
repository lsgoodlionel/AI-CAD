#!/usr/bin/env python3
"""导出符号级金标签为训练格式（Phase C · C-16 → C-09）。

把 ``model_symbol_annotations`` 中 status='confirmed' 的金标签导出为 COCO-like
JSON，直接喂 C-09（CADTransformer 微调）。序列化逻辑与
``routers.model_annotations.serialize_coco`` 共用（DRY），确保 API 端点与离线
CLI 产出一致。

用法：
    cd apps/api && python scripts/model3d/export_annotations.py \
        --project-id <PROJECT_ID> [--out gold_labels.json] [--database-url ...]

未指定 ``--out`` 时打印到 stdout。``--database-url`` 缺省取环境变量
``DATABASE_URL``（回退本地开发库）。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

# 让脚本可从 apps/api 根目录独立运行（补齐 import 路径）。
_API_ROOT = Path(__file__).resolve().parents[2]
if str(_API_ROOT) not in sys.path:
    sys.path.insert(0, str(_API_ROOT))

from routers.model_annotations import _EXPORT_SQL, serialize_coco  # noqa: E402

_DEFAULT_DB_URL = "postgresql://cad_user:cad_pass@127.0.0.1:5432/cad_db"


async def _fetch_confirmed(database_url: str, project_id: str) -> list[dict]:
    """拉取该项目 confirmed 金标签行（asyncpg，纯只读）。"""
    import asyncpg

    conn = await asyncpg.connect(database_url)
    try:
        rows = await conn.fetch(_EXPORT_SQL, project_id)
        return [dict(row) for row in rows]
    finally:
        await conn.close()


async def _run(args: argparse.Namespace) -> int:
    database_url = args.database_url or os.getenv("DATABASE_URL") or _DEFAULT_DB_URL
    rows = await _fetch_confirmed(database_url, args.project_id)
    dataset = serialize_coco(rows, project_id=args.project_id)

    payload = json.dumps(dataset, ensure_ascii=False, indent=2)
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(payload, encoding="utf-8")
        print(
            f"导出 {len(dataset['annotations'])} 条金标签 / "
            f"{len(dataset['images'])} 张图纸 → {out_path}"
        )
    else:
        print(payload)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="导出符号级 confirmed 金标签为 COCO-like 训练格式（C-16 → C-09）"
    )
    parser.add_argument("--project-id", required=True, help="项目 ID")
    parser.add_argument("--out", default=None, help="输出 JSON 路径（默认打印到 stdout）")
    parser.add_argument(
        "--database-url",
        default=None,
        help="PostgreSQL 连接串（默认取环境变量 DATABASE_URL）",
    )
    parser.add_argument(
        "--format", default="coco", choices=["coco"], help="导出格式（目前仅 coco）"
    )
    args = parser.parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
