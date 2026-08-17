"""档案原地重分类 —— 分类规则改进后回灌,不重跑 OCR。

用法（**默认 dry-run,不写库**）:

    python scripts/model3d/reclassify_archive.py <project_id>
    python scripts/model3d/reclassify_archive.py <project_id> --apply

判据见 `services/archive_reclassify.plan_reclassify`:
只动 `extractor ∈ {ocr, vector_text}` 且 `source_kind != 'verified'` 的行。
"""
from __future__ import annotations

import argparse
import asyncio
import collections
import sys

from core.database import database
from services.archive_reclassify import (
    PROTECTED_SOURCE_KINDS, RECLASSIFIABLE_EXTRACTORS, plan_reclassify,
)

_FETCH = """
SELECT e.id, e.content, e.category, e.extractor, e.source_kind
FROM drawing_extracted_info e
WHERE e.project_id = CAST(:pid AS uuid) AND e.is_active
ORDER BY e.id
LIMIT :limit OFFSET :offset
"""

_UPDATE = """
UPDATE drawing_extracted_info SET category = :category WHERE id = :id
"""

PAGE = 20000


async def run(project_id: str, apply: bool) -> int:
    await database.connect()
    moves: collections.Counter = collections.Counter()
    total = 0
    changed = 0
    try:
        offset = 0
        while True:
            rows = await database.fetch_all(
                _FETCH, {"pid": project_id, "limit": PAGE, "offset": offset})
            if not rows:
                break
            total += len(rows)
            plan = plan_reclassify([dict(r) for r in rows])
            changed += len(plan)
            for item in plan:
                moves[f"{item['was']} → {item['category']}"] += 1
            if apply and plan:
                # 逐条更新：类别是单列小写入，批量语句反而更难在失败时定位
                for item in plan:
                    await database.execute(
                        _UPDATE, {"id": item["id"], "category": item["category"]})
            offset += PAGE
            print(f"  已扫描 {total} 行，累计待改 {changed}", flush=True)
    finally:
        await database.disconnect()

    print(f"\n扫描 {total} 行，需要改类别 {changed} 行"
          f"（{'已写库' if apply else 'dry-run，未写库'}）")
    print(f"只动 extractor∈{sorted(RECLASSIFIABLE_EXTRACTORS)}、"
          f"source_kind∉{sorted(PROTECTED_SOURCE_KINDS)}")
    print("\n改动分布（前 20）:")
    for move, count in moves.most_common(20):
        print(f"  {move:<28} {count:>8}")
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project_id")
    parser.add_argument("--apply", action="store_true",
                        help="真正写库（默认只 dry-run）")
    args = parser.parse_args()
    asyncio.run(run(args.project_id, args.apply))
    return 0


if __name__ == "__main__":
    sys.exit(main())
