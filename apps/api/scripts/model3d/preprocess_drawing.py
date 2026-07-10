#!/usr/bin/env python3
"""C-02 预处理器 CLI：图纸 → SVG + 图元 JSON。

用法：
  python preprocess_drawing.py INPUT.dxf --out-dir OUT
  python preprocess_drawing.py INPUT.pdf --json-only

产出 ``<name>.primitives.json`` 与 ``<name>.svg``（除非 --json-only / --svg-only）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_API_ROOT = Path(__file__).resolve().parents[2]
if str(_API_ROOT) not in sys.path:
    sys.path.insert(0, str(_API_ROOT))

from core.model3d.preprocess import preprocess_drawing  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="DXF/DWG/PDF → SVG + 图元 JSON 预处理器")
    p.add_argument("input", help="输入图纸路径（.dxf/.dwg/.pdf）")
    p.add_argument("--out-dir", default=None, help="输出目录（默认与输入同目录）")
    p.add_argument("--json-only", action="store_true")
    p.add_argument("--svg-only", action="store_true")
    args = p.parse_args(argv)

    src = Path(args.input)
    if not src.exists():
        print(f"错误：输入不存在 {src}", file=sys.stderr)
        return 2

    ext = src.suffix.lstrip(".").lower()
    result = preprocess_drawing(src.read_bytes(), ext)

    out_dir = Path(args.out_dir) if args.out_dir else src.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = src.stem

    if not args.svg_only:
        json_path = out_dir / f"{stem}.primitives.json"
        json_path.write_text(
            json.dumps(result.doc.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"图元 JSON → {json_path}")
    if not args.json_only:
        svg_path = out_dir / f"{stem}.svg"
        svg_path.write_text(result.svg, encoding="utf-8")
        print(f"SVG → {svg_path}")

    counts = result.doc.counts
    print(f"图元统计: {counts}   warnings: {list(result.doc.warnings)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
