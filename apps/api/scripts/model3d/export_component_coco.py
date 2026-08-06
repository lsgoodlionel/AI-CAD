"""Phase H 构件金标签 COCO 导出 CLI —— 人审 confirmed 构件 → COCO,喂 C-09 微调。

数据飞轮:H4+ 人审 confirm/reclass → review_state='confirmed' → 本脚本导出归一化
bbox 金标签(仅有 drawing_transform 的图可算 bbox)。与 services.component_repository
/ services.component_coco 共用逻辑(DRY)。

用法:
    cd apps/api && python scripts/model3d/export_component_coco.py \
        --project-id <uuid> --out /tmp/component_gold.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from databases import Database  # noqa: E402

from services.component_coco import to_coco  # noqa: E402
from services.component_repository import fetch_component_gold_labels  # noqa: E402


async def _run(args: argparse.Namespace) -> int:
    database_url = args.database_url or os.environ.get("DATABASE_URL")
    if not database_url:
        print("缺少 DATABASE_URL（--database-url 或环境变量）", file=sys.stderr)
        return 2
    db = Database(database_url)
    await db.connect()
    try:
        version = args.version
        if version is None:
            row = await db.fetch_one(
                "SELECT version FROM project_models WHERE project_id=:p",
                {"p": args.project_id})
            if row is None:
                print("项目无模型", file=sys.stderr)
                return 1
            version = row["version"]
        labels = await fetch_component_gold_labels(db, args.project_id, version)
    finally:
        await db.disconnect()

    stamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    dataset = to_coco(labels, project_id=args.project_id, exported_at=stamp)
    out_path = Path(args.out)
    out_path.write_text(json.dumps(dataset, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"导出 {len(dataset['annotations'])} 条构件金标签 / "
          f"{len(dataset['images'])} 张图纸 → {out_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="构件金标签 COCO 导出（喂 C-09）")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--out", required=True, help="输出 COCO JSON 路径")
    parser.add_argument("--version", type=int, default=None, help="模型版本（默认最新）")
    parser.add_argument("--database-url", default=None)
    return asyncio.run(_run(parser.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
