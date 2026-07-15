#!/usr/bin/env python3
"""D-16 OCR 后端评测 CLI（离线可跑）。

两档模式：

- **有金标签**（``--demo`` / ``--manifest``）：对一组图纸 + 金标签跑多后端 OCR
  对比，算标高/轴号/图名·房间名的 Precision/Recall/F1 + 置信标定。
- **无金标签**（``--demo-unlabeled`` / ``--dir``）：图纸目录没有人工标注真值时
  的默认模式（如上海大歌剧院全量真实图纸）——产出后端间一致性（重合率，非
  准确率）、识别量与置信分布、consume.py 三馈线产出量，横向比较后端强弱。

真实识别需装 paddleocr/paddlepaddle 或 rapidocr_onnxruntime；未装的后端在
报告中如实标注为不可用，不参与打分、不用 0 分冒充「跑过」。

用法：
  # 有金标签合成 demo（mock 后端，验证基座端到端，无需真实图纸/依赖）
  python scripts/model3d/ocr_eval.py --demo

  # 有金标签真实评测：manifest JSON = {"samples":[{"sample_id","path","gold":{
  #   "elevations":[0.0,3.6], "axes":["1","A"], "titles":["首层平面图"]}}]}
  # path 相对 manifest 所在目录解析（也接受绝对路径）。
  python scripts/model3d/ocr_eval.py --manifest manifest.json \\
      --backends paddleocr,rapidocr,paddleocr_vl --out docs/ocr_eval_report.md

  # 无金标签合成 demo（两个 mock 后端，验证一致性度量端到端）
  python scripts/model3d/ocr_eval.py --demo-unlabeled

  # 无金标签真实评测：目录下所有 PDF 图纸（如上海大歌剧院全量图纸目录）
  python scripts/model3d/ocr_eval.py --dir /data/drawings/sgoh \\
      --backends paddleocr,rapidocr,paddleocr_vl --limit 50 \\
      --out docs/ocr_eval_sgoh_report.md
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 允许从 apps/api 根导入
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.model3d.ocr.eval.harness import OcrEvalSample, run_backend_comparison  # noqa: E402
from core.model3d.ocr.eval.metrics import GoldLabels  # noqa: E402
from core.model3d.ocr.eval.report import render_markdown, render_unlabeled_markdown  # noqa: E402
from core.model3d.ocr.eval.unlabeled import (  # noqa: E402
    UnlabeledSample,
    run_unlabeled_comparison,
)
from core.model3d.ocr.mock_backend import MockOcrBackend  # noqa: E402
from core.model3d.ocr.paddle_backend import PaddleOcrBackend  # noqa: E402
from core.model3d.ocr.paddleocr_vl_backend import PaddleOcrVlBackend  # noqa: E402
from core.model3d.ocr.rapid_backend import RapidOcrBackend  # noqa: E402

_BACKEND_REGISTRY = {
    "paddleocr": PaddleOcrBackend,
    "rapidocr": RapidOcrBackend,
    "paddleocr_vl": PaddleOcrVlBackend,
    "mock": MockOcrBackend,
}


def _demo_samples() -> tuple[list[OcrEvalSample], dict]:
    """合成 demo：mock 后端 seed 与金标签部分重合（含命中/漏检/误报样例）。"""
    seed = [
        ("±0.000", (100, 200, 160, 220), 0.98),   # 命中
        ("+3.600", (100, 400, 160, 420), 0.91),   # 命中
        ("A", (50, 50, 70, 70), 0.55),            # 命中（低置信）
        # 漏检示例：classify_text 对含"首层"的串按 level_name 规则优先匹配（
        # 见 classify.py 判定优先级"标高>楼层名>轴号>尺寸>标题"），故此文本被
        # 分到 level_name 而非 title，title 类不会命中——如实保留该行为，
        # 不为凑"标题命中"演示而回避 classify.py 的真实优先级规则。
        ("首层平面图", (300, 300, 600, 340), 0.93),
        ("地下室结构平面布置图", (300, 500, 700, 540), 0.90),  # title 命中（不含楼层名关键词）
        ("9.000", (700, 700, 760, 720), 0.40),    # 误报：金标签无此标高
    ]
    gold = GoldLabels(
        elevations=(0.0, 3.6), axes=("A", "B"),
        titles=("首层平面图", "地下室结构平面布置图"),
    )
    sample = OcrEvalSample(
        file_bytes=b"not-a-real-pdf", file_ext="pdf", gold=gold, sample_id="demo-1"
    )
    backends = {"mock": MockOcrBackend(seed=seed)}
    return [sample], backends


def _demo_unlabeled_samples() -> tuple[list[UnlabeledSample], dict]:
    """无金标签合成 demo：两个 mock 后端，一半命中一半分歧，验证一致性度量。"""
    seed_a = [
        ("±0.000", (100, 200, 160, 220), 0.98),
        ("+3.600", (100, 400, 160, 420), 0.91),
        ("A", (50, 50, 70, 70), 0.55),
        ("首层平面图", (300, 300, 600, 340), 0.93),
    ]
    seed_b = [
        ("±0.000", (100, 200, 160, 220), 0.95),  # 与 A 一致
        ("+3.601", (100, 400, 160, 420), 0.80),  # 与 A 差 1mm，容差内一致
        ("B", (500, 50, 520, 70), 0.60),         # 仅 B 识别到
    ]
    sample = UnlabeledSample(file_bytes=b"not-a-real-pdf", file_ext="pdf", sample_id="demo-1")
    backends = {"mock_a": MockOcrBackend(seed=seed_a), "mock_b": MockOcrBackend(seed=seed_b)}
    return [sample], backends


def _iter_pdf_files(dir_path: Path, *, recursive: bool) -> list[Path]:
    """目录内所有 PDF 图纸（大小写不敏感），按文件名排序保证可复现。"""
    it = dir_path.rglob("*") if recursive else dir_path.iterdir()
    return sorted(p for p in it if p.is_file() and p.suffix.lower() == ".pdf")


def _load_dir_samples(
    dir_path: Path, backend_names: list[str], *, recursive: bool, limit: int | None
) -> tuple[list[UnlabeledSample], dict]:
    """无金标签模式：目录下的图纸直接当样本，无需 manifest/标注。"""
    files = _iter_pdf_files(dir_path, recursive=recursive)
    if limit is not None:
        files = files[:limit]
    samples = [
        UnlabeledSample(file_bytes=f.read_bytes(), file_ext=f.suffix, sample_id=f.name)
        for f in files
    ]
    backends = {name: _BACKEND_REGISTRY[name]() for name in backend_names}
    return samples, backends


def _load_manifest(path: Path, backend_names: list[str]) -> tuple[list[OcrEvalSample], dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    base = path.parent
    samples: list[OcrEvalSample] = []
    for s in data.get("samples", []):
        drawing_path = Path(s["path"])
        if not drawing_path.is_absolute():
            drawing_path = base / drawing_path
        file_bytes = drawing_path.read_bytes()
        g = s.get("gold", {})
        gold = GoldLabels(
            elevations=tuple(float(v) for v in g.get("elevations", [])),
            axes=tuple(str(v) for v in g.get("axes", [])),
            titles=tuple(str(v) for v in g.get("titles", [])),
        )
        samples.append(
            OcrEvalSample(
                file_bytes=file_bytes, file_ext=drawing_path.suffix,
                gold=gold, sample_id=str(s.get("sample_id", drawing_path.name)),
            )
        )
    backends = {name: _BACKEND_REGISTRY[name]() for name in backend_names}
    return samples, backends


def _parse_backend_names(raw: str) -> list[str] | None:
    """校验 ``--backends`` 逗号分隔列表；非法/为空时打印错误并返回 ``None``。"""
    names = [n.strip() for n in raw.split(",") if n.strip()]
    unknown = [n for n in names if n not in _BACKEND_REGISTRY]
    if unknown:
        print(f"错误：未知后端 {unknown}，可选 {sorted(_BACKEND_REGISTRY)}", file=sys.stderr)
        return None
    if not names:
        print("错误：--backends 未指定任何后端", file=sys.stderr)
        return None
    return names


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="D-16 OCR 后端评测基座（有金标签 / 无金标签两档）")
    p.add_argument("--manifest", default=None, help="有金标签评测清单 JSON")
    p.add_argument("--demo", action="store_true", help="有金标签合成 demo（mock 后端，无需真实数据/依赖）")
    p.add_argument("--dir", default=None, help="无金标签模式：图纸目录（横向比较后端，无需标注）")
    p.add_argument(
        "--demo-unlabeled", action="store_true",
        help="无金标签合成 demo（两个 mock 后端，验证一致性度量端到端）",
    )
    p.add_argument("--recursive", action="store_true", help="--dir 模式递归子目录")
    p.add_argument("--limit", type=int, default=None, help="--dir 模式最多取前 N 个文件（大批量图纸调试用）")
    p.add_argument(
        "--backends", default="paddleocr,rapidocr,paddleocr_vl",
        help=f"逗号分隔后端名，可选 {sorted(_BACKEND_REGISTRY)}",
    )
    p.add_argument("--dpi", type=int, default=200)
    p.add_argument("--elevation-tolerance", type=float, default=0.05, help="标高匹配容差（米）")
    p.add_argument("--out", default=None, help="Markdown 报告输出路径")
    p.add_argument("--json", default=None, help="指标 JSON 输出路径")
    args = p.parse_args(argv)

    unlabeled = False
    if args.demo:
        samples, backends = _demo_samples()
    elif args.manifest:
        mpath = Path(args.manifest)
        if not mpath.exists():
            print(f"错误：清单不存在 {mpath}", file=sys.stderr)
            return 2
        names = _parse_backend_names(args.backends)
        if names is None:
            return 2
        samples, backends = _load_manifest(mpath, names)
    elif args.demo_unlabeled:
        unlabeled = True
        samples, backends = _demo_unlabeled_samples()
    elif args.dir:
        unlabeled = True
        dpath = Path(args.dir)
        if not dpath.is_dir():
            print(f"错误：目录不存在 {dpath}", file=sys.stderr)
            return 2
        names = _parse_backend_names(args.backends)
        if names is None:
            return 2
        samples, backends = _load_dir_samples(
            dpath, names, recursive=args.recursive, limit=args.limit
        )
        if not samples:
            print(f"错误：目录内未找到 PDF 图纸 {dpath}", file=sys.stderr)
            return 2
    else:
        print("需 --demo / --manifest / --demo-unlabeled / --dir 之一", file=sys.stderr)
        return 2

    if unlabeled:
        report = run_unlabeled_comparison(
            samples, backends, dpi=args.dpi, elevation_tolerance_m=args.elevation_tolerance
        )
        md = render_unlabeled_markdown(report)
    else:
        report = run_backend_comparison(
            samples, backends, dpi=args.dpi, elevation_tolerance_m=args.elevation_tolerance
        )
        md = render_markdown(report)

    if args.out:
        Path(args.out).write_text(md, encoding="utf-8")
        print(f"报告 → {args.out}")
    else:
        print(md)
    if args.json:
        Path(args.json).write_text(
            json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"指标 JSON → {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
