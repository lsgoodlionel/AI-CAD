#!/usr/bin/env python3
"""金标准常设报表 CLI —— 一条命令看齐全部度量指标。

用法：
  # 打印 Markdown 报表
  python scripts/model3d/gold_report.py

  # 写文件
  python scripts/model3d/gold_report.py --out docs/GOLD_STANDARD_REPORT.md

  # 只跑结构校验（CI 用：有 error 就非零退出）
  python scripts/model3d/gold_report.py --check-only

  # 机器可读
  python scripts/model3d/gold_report.py --json

报表口径固定在 `core/model3d/gold/report.py` 的模块文档里：分组拆开、
两极分化按图纸算、没有精确率的类报「不适用」。**不要在这里另写一套。**
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_API_ROOT = Path(__file__).resolve().parents[2]
if str(_API_ROOT) not in sys.path:
    sys.path.insert(0, str(_API_ROOT))

from core.model3d.gold.report import (  # noqa: E402
    build_report, render_markdown)
from core.model3d.gold.schema_check import (  # noqa: E402
    check_gold_dir, gold_dir, load_gold_files)


def _as_json(rep, issues) -> str:
    return json.dumps({
        "total_files": rep.total_files,
        "total_units": rep.total_units,
        "total_verdicts": rep.total_verdicts,
        "total_ok": rep.total_ok,
        "criteria_coverage": round(rep.criteria_coverage, 4),
        "classes": [{
            "file": c.file, "class": c.object_class, "method": c.method,
            "units": c.units, "verdicts": c.verdicts,
            "main_ok": c.main_ok, "main_verdicts": c.main_verdicts,
            "main_rate": c.main_rate, "overall_rate": c.overall_rate,
            "has_criteria": c.has_criteria, "criteria": c.criteria,
            "groups": [{"name": g.name, "ok": g.ok, "verdicts": g.verdicts,
                        "auxiliary": g.auxiliary} for g in c.groups],
        } for c in rep.classes],
        "polarization_by_drawing": {
            k: {"drawings": v.drawings, "all_ok": v.all_ok,
                "all_bad": v.all_bad, "mixed": v.mixed,
                "share": v.share, "resolution": v.resolution}
            for k, v in rep.polarization_by_drawing.items()},
        "polarization_by_unit": {
            k: {"drawings": v.drawings, "share": v.share}
            for k, v in rep.polarization_by_unit.items()},
        "taxonomy": {"total": rep.taxonomy.total,
                     "buckets": rep.taxonomy.buckets,
                     "labels": rep.taxonomy.labels,
                     "unmapped": rep.taxonomy.unmapped},
        "issues": issues,
    }, ensure_ascii=False, indent=2)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="金标准常设报表")
    ap.add_argument("--dir", type=Path, default=None, help="金标准目录")
    ap.add_argument("--out", type=Path, default=None, help="报表输出路径")
    ap.add_argument("--json", action="store_true", help="输出 JSON 而非 Markdown")
    ap.add_argument("--check-only", action="store_true",
                    help="只跑结构校验；有 error 时退出码 1")
    args = ap.parse_args(argv)

    directory = args.dir or gold_dir()
    if not directory.is_dir():
        print(f"金标准目录不存在：{directory}", file=sys.stderr)
        return 2

    issues = check_gold_dir(directory)
    errors = [i for i in issues if i["level"] == "error"]

    if args.check_only:
        for i in issues:
            print(f"[{i['level']}] {i['file']} {i['code']}: {i['message']}")
        print(f"\nerror {len(errors)} 条 · warn {len(issues) - len(errors)} 条")
        return 1 if errors else 0

    rep = build_report(load_gold_files(directory))
    text = (_as_json(rep, issues) if args.json
            else render_markdown(rep, issues=issues))
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
        print(f"已写入 {args.out}（{rep.total_verdicts} 条裁决，"
              f"error {len(errors)} 条）")
    else:
        print(text)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
