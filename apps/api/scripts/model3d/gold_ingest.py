"""把判读者返回的 JSON 回收成金标准（`gold_batch` 的另一半）。

用法：

    python -m scripts.model3d.gold_ingest --batch-dir gold_sheets/col3 \\
        --answers /path/to/answers.json

步骤：读 manifest.tsv + meta.json → 编号纠错 → 有效性检查 → 分层加权汇总 →
写 `data/model3d/gold/<batch>_v1.json`，并把 manifest 复制进
`data/model3d/gold/manifests/`（B 曾因 manifest 丢在 /tmp 而不得不重建对照表）。

**空白对照不通过就拒绝落库**（`--force` 可越过，但会在 note 里写明）——
判读者连空白都答成构件，其余格子的答案没有意义。
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import sys
from pathlib import Path

from core.model3d.gold.batch_codes import repair_code
from core.model3d.gold.ingest import summarize
from core.model3d.gold.validity import blank_control_ok

GOLD = Path(__file__).resolve().parents[2] / "data/model3d/gold"


def _load_answers(path: Path) -> list[dict]:
    """判读者常在 JSON 前后加说明文字 —— 只取第一个 `[` 到最后一个 `]`。"""
    text = path.read_text(encoding="utf-8")
    match = re.search(r"\[.*\]", text, re.S)
    if not match:
        raise ValueError(f"{path} 里找不到 JSON 数组")
    return json.loads(match.group(0))


def _full_weights(meta: dict) -> dict[str, float]:
    """扫到的层用实测分量；没扫到的层按全局平均候选数估，只为算覆盖率。"""
    weights = dict(meta.get("weights") or {})
    counts = [c for v in (meta.get("scanned_counts") or {}).values() for c in v]
    mean = sum(counts) / len(counts) if counts else 0.0
    for stratum, n in (meta.get("strata_drawings") or {}).items():
        weights.setdefault(stratum, n * mean)
    return weights


def _gold_doc(batch: str, kind: str, rows: list[dict], answers: dict, field: str,
              summary: dict, blank_detail: str) -> dict:
    units = {}
    for row in rows:
        ans = answers.get(row["code"])
        if ans is None:
            continue
        judged = bool(ans.get(field)) if isinstance(ans.get(field), bool) \
            else str(ans.get(field)).lower() == "true"
        group = {"kept": "kept", "blank": "blank_control", "dup": "retest_dup"}[row["group"]]
        # 空白对照组沿用 columns_final 的约定：ok = 仪器表现正确（判为「不是」）
        ok = (not judged) if group == "blank_control" else judged
        note = ans.get("what") or ans.get("saw") or ""
        if row.get("dup_of"):
            note = f"重测副本，原格 {row['dup_of']}；{note}"
        units.setdefault(group, []).append({"ref": row["code"], "ok": ok, "note": note})
    sp = summary
    note = (f"原生分辨率裁图 + 分层抽样（{len(sp['per_stratum'])} 层）+ 批内重测对。"
            f"原始精确率 {sp['raw_precision']:.3f}，语料加权 {sp['weighted_precision']:.3f}"
            f"（覆盖语料 {sp['coverage']:.0%}）；{blank_detail}；"
            f"重测一致率 {sp['pair_agreement']}；编号 匹配 {sp['matched']} / "
            f"纠正 {sp['repaired']} / 编造 {sp['fabricated']}。")
    return {"version": 1, "object_classes": [kind], "units": [
        {"unit": f"{batch}-{g}", "source": {"group": g},
         "classes": {f"{kind}_{batch}": {
             "method": "verdicts", "verdicts": v, "confidence": 0.0,
             "verified_by": ["independent_judge"], "criteria": f"CRITERIA.md#{kind}",
             "note": note}}} for g, v in units.items()]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch-dir", required=True, type=Path)
    ap.add_argument("--answers", required=True, type=Path)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    meta = json.loads((args.batch_dir / "meta.json").read_text(encoding="utf-8"))
    with (args.batch_dir / "manifest.tsv").open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh, delimiter="\t"))
    raw = _load_answers(args.answers)
    field = meta.get("answer_field", "ok")
    summary = summarize(rows, raw, field=field, weights=_full_weights(meta))

    neg, n = summary["blank"]
    passed, blank_detail = blank_control_ok(n_blank=n, n_blank_judged_negative=neg)
    print(f"── {meta['batch']}（{meta['kind']}）──")
    print(f"  编号：匹配 {summary['matched']} · 纠正 {summary['repaired']} · 编造 {summary['fabricated']}")
    print(f"  {blank_detail}")
    print(f"  重测一致率：{summary['pair_agreement']}")
    for issue in summary["judge_issues"]:
        print(f"  ⚠ 判读健全性：{issue}")
    for s, (ok, tot) in sorted(summary["per_stratum"].items()):
        print(f"  [{s}] {ok}/{tot}")
    print(f"  原始精确率 {summary['raw_precision']}")
    print(f"  语料加权 {summary['weighted_precision']}（覆盖 {summary['coverage']:.0%}）")
    print(f"  误检构成：{summary['false_positive_labels']}")

    if not passed and not args.force:
        print("\n✗ 空白对照未通过，拒绝落库（--force 可越过）")
        return 2
    by_code = {}
    for a in raw:
        code = repair_code(str(a.get("id") or "").strip().upper(), {r["code"] for r in rows})
        if code and code not in by_code:
            by_code[code] = a
    doc = _gold_doc(meta["batch"], meta["kind"], rows, by_code, field, summary, blank_detail)
    out = GOLD / f"{meta['kind']}_{meta['batch']}_v1.json"
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (GOLD / "manifests").mkdir(exist_ok=True)
    shutil.copy(args.batch_dir / "manifest.tsv", GOLD / "manifests" / f"{meta['batch']}.tsv")
    print(f"\n✓ 写入 {out.relative_to(GOLD.parents[2])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
