#!/usr/bin/env python3
"""图纸全文 OCR CLI（离线可跑）。

对本地图纸文件跑 OCR，输出结构化 token（JSON）+ 下游馈入摘要。
真实识别需安装 paddleocr/paddlepaddle；未安装则 backend=none（优雅降级）。

用法：
  python scripts/model3d/ocr_drawing.py <图纸.pdf|.png> [--dpi 200] [--min-conf 0.6] [--json]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 允许从 apps/api 根导入
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.model3d.ocr import run_ocr  # noqa: E402
from core.model3d.ocr.consume import (  # noqa: E402
    axis_anchors,
    elevation_candidates,
    space_labels,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="图纸全文 OCR")
    parser.add_argument("path", help="图纸文件路径（pdf/png/jpg）")
    parser.add_argument("--dpi", type=int, default=200)
    parser.add_argument("--min-conf", type=float, default=0.6)
    parser.add_argument("--json", action="store_true", help="输出完整 token JSON")
    args = parser.parse_args()

    path = Path(args.path)
    if not path.exists():
        print(f"文件不存在: {path}", file=sys.stderr)
        return 2

    data = path.read_bytes()
    result = run_ocr(data, path.suffix, dpi=args.dpi, min_confidence=args.min_conf)

    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return 0

    print(f"后端: {result.backend}  可用: {result.available}  DPI: {result.dpi}")
    if result.warnings:
        print("告警:", "; ".join(result.warnings))
    print("分类计数:", result.kind_counts)
    print("标高候选:", elevation_candidates(result))
    print("轴号锚点:", [a["label"] for a in axis_anchors(result)])
    print("空间/图名:", [l["text"] for l in space_labels(result)])
    if not result.available:
        print("\n提示: 未识别到文本。真实识别需安装 paddleocr/paddlepaddle（见 requirements.txt）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
