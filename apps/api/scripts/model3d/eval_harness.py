#!/usr/bin/env python3
"""C-14 统一评测基座 CLI：纯规则 vs 学习模型 vs 融合 一键对比。

用法：
  # 合成 demo（无需真实数据，验证基座端到端）
  python eval_harness.py --demo --out report.md

  # 真实评测：manifest JSON = {"samples":[{"sample_id","gt":[{category,bbox}],
  #                                        "primitives":[{id,type,points,layer,block}]}]}
  python eval_harness.py --manifest manifest.json --out docs/PHASE_C_EVAL_REPORT.md

test 集须按 C-07 项目切分冻结，仅 C-18 终评解冻一次（本 CLI 不解冻，评测用固定切分）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_API_ROOT = Path(__file__).resolve().parents[2]
if str(_API_ROOT) not in sys.path:
    sys.path.insert(0, str(_API_ROOT))

from core.model3d.eval.harness import EvalSample, run_comparison  # noqa: E402
from core.model3d.eval.metrics import GtBox  # noqa: E402
from core.model3d.eval.report import render_markdown  # noqa: E402
from core.model3d.preprocess.schema import Primitive, PrimitiveDoc  # noqa: E402


def _load_manifest(path: Path) -> list[EvalSample]:
    data = json.loads(path.read_text(encoding="utf-8"))
    samples: list[EvalSample] = []
    for s in data.get("samples", []):
        prims = tuple(
            Primitive(
                id=int(p["id"]), type=p["type"],
                points=tuple(tuple(pt) for pt in p.get("points", [])),
                layer=p.get("layer", ""), block=p.get("block", ""),
            )
            for p in s.get("primitives", [])
        )
        gt = tuple(
            GtBox(category=g["category"], bbox=tuple(g["bbox"]),
                  mep_system=g.get("mep_system"))
            for g in s.get("gt", [])
        )
        samples.append(EvalSample(doc=PrimitiveDoc(primitives=prims), gt=gt,
                                  sample_id=str(s.get("sample_id", ""))))
    return samples


def _demo_samples() -> list[EvalSample]:
    """合成一张含柱/梁/管的图，真值与图元对齐，验证基座端到端。"""
    prims = (
        Primitive(id=0, type="polyline",
                  points=((0, 0), (400, 0), (400, 400), (0, 400), (0, 0)),
                  layer="S-COLU", block="KZ1", closed=True),
        Primitive(id=1, type="line", points=((0, 0), (2000, 0)), layer="S-BEAM"),
        Primitive(id=2, type="line", points=((0, 500), (500, 500)), layer="M-DUCT"),
    )
    gt = (
        GtBox(category="column", bbox=(0, 0, 400, 400)),
        GtBox(category="beam", bbox=(0, 0, 2000, 0)),
        GtBox(category="pipe", bbox=(0, 500, 500, 500), mep_system="暖通"),
    )
    return [EvalSample(doc=PrimitiveDoc(primitives=prims), gt=gt, sample_id="demo-1")]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Phase C 统一评测基座")
    p.add_argument("--manifest", default=None, help="评测清单 JSON")
    p.add_argument("--demo", action="store_true", help="合成 demo（无需真实数据）")
    p.add_argument("--iou", type=float, default=0.5)
    p.add_argument("--out", default=None, help="Markdown 报告输出路径")
    p.add_argument("--json", default=None, help="指标 JSON 输出路径")
    args = p.parse_args(argv)

    if args.demo:
        samples = _demo_samples()
    elif args.manifest:
        mpath = Path(args.manifest)
        if not mpath.exists():
            print(f"错误：清单不存在 {mpath}", file=sys.stderr)
            return 2
        samples = _load_manifest(mpath)
    else:
        print("需 --demo 或 --manifest", file=sys.stderr)
        return 2

    report = run_comparison(samples, iou_thr=args.iou)
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
