"""比例证据分的回测：金标准 56 条 + 全库分布。

**验收硬指标是「新分数与 56 条实测正相关」。** 这个脚本如实报数，
包括做不到的时候 —— 做不到本身也是结论，前提是给出原因分析。

用法：
    # 金标准回测（需要 scale_gold_manifest.py 复原出的对照表 + 档案票）
    python -m scripts.model3d.backtest_scale_evidence gold <manifest.tsv> <arch.json> <feat.json>
    # 全库分布（直接读库）
    python -m scripts.model3d.backtest_scale_evidence library
"""
from __future__ import annotations

import asyncio
import collections
import json
import math
import sys

from core.model3d.scale_evidence import PT_TO_MM, evaluate
from services.drawing_transform import _blend_confidence

GOLD_PATH = "data/model3d/gold/drawing_scale_v1.json"


def _auc(pairs: list[tuple[float, bool]]) -> tuple[float, int, int]:
    """P(正样本分数 > 负样本分数)，并列算 0.5。0.5 = 无信号。"""
    pos = [s for s, ok in pairs if ok]
    neg = [s for s, ok in pairs if not ok]
    if not pos or not neg:
        return float("nan"), len(pos), len(neg)
    hits = sum(1.0 if p > n else (0.5 if p == n else 0.0) for p in pos for n in neg)
    return hits / (len(pos) * len(neg)), len(pos), len(neg)


def _gold_verdicts() -> dict[str, tuple[bool, str, str]]:
    data = json.load(open(GOLD_PATH))
    out: dict[str, tuple[bool, str, str]] = {}
    for unit in data["units"]:
        stratum = unit["source"]["stratum"]
        for v in unit["classes"]["drawing_scale"]["verdicts"]:
            out[v["ref"]] = (bool(v["ok"]), v.get("what", "ok"), stratum)
    return out


def _rate(rows: list[bool]) -> str:
    return f"n={len(rows):3} 合理={sum(rows):2} = {sum(rows) / len(rows) * 100:5.1f}%" \
        if rows else "n=  0"


def run_gold(man_path: str, arch_path: str, feat_path: str) -> int:
    verdicts = _gold_verdicts()
    arch = json.load(open(arch_path))
    feat = json.load(open(feat_path))
    by_ref = {f["ref"]: (did, f) for did, f in feat.items() if f.get("ref")}
    titles = {}
    for line in open(man_path).read().splitlines()[1:]:
        parts = line.split("\t")
        titles[parts[0]] = parts[5]

    rows = []
    for ref, (ok, what, stratum) in verdicts.items():
        did, f = by_ref[ref]
        votes = collections.Counter()
        rec = arch.get(did, {"votes": [], "extent": [0.0, 0.0]})
        ex = rec["extent"]
        for v in rec["votes"]:
            # 档案 bbox 在 OCR 渲染像素系；按各自最大范围折算回页面比例，
            # 再乘页宽/页高，才能与 scale_evidence 的图框带判据同一坐标系。
            x = None if (v["x"] is None or not ex[0]) else v["x"] / ex[0] * f["page_w"]
            y = None if (v["y"] is None or not ex[1]) else v["y"] / ex[1] * f["page_h"]
            votes[(x, y, f"1:{v['n']}")] += 1
        texts = list(votes)
        got = evaluate(f["scale_m_pt"], texts=texts,
                       page_w_pt=f["page_w"], page_h_pt=f["page_h"])
        rows.append({
            "ref": ref, "ok": ok, "what": what, "stratum": stratum,
            "den": f["scale_m_pt"] * 1000 / PT_TO_MM,
            "new": _blend_confidence(f["confidence"], got),
            "raw": got.score, "old": f["confidence"],
            "reason": got.reason(), "title": titles.get(ref, ""),
            "cell_m": f["page_w"] / 3 * f["scale_m_pt"],
        })

    print("=" * 78)
    print("一、逐条")
    print(f"{'ref':5} {'ok':2} {'what':10} {'den':>8} {'旧':>5} {'新':>6}  证据")
    for r in sorted(rows, key=lambda r: (-(r["new"] if r["new"] is not None else -1))):
        new = "  n/a" if r["new"] is None else f"{r['new']:6.2f}"
        print(f"{r['ref']:5} {int(r['ok']):2} {r['what']:10} {r['den']:8.1f} "
              f"{r['old']:5.2f} {new}  {r['reason'][:90]}")

    print()
    print("=" * 78)
    print("二、分层合理率（新分数 vs 旧 confidence）")
    for name, key, edges in (("新证据分", "new", (0.0, 0.5, 0.9, 1.01)),
                             ("旧 confidence", "old", (0.0, 0.5, 0.99, 1.01))):
        print(f"\n  【{name}】")
        scored = [r for r in rows if r[key] is not None]
        for lo, hi in zip(edges, edges[1:]):
            sub = [r["ok"] for r in scored if lo <= r[key] < hi]
            print(f"    [{lo:.2f}, {hi:.2f})  {_rate(sub)}")
        missing = [r["ok"] for r in rows if r[key] is None]
        if missing:
            print(f"    无分数         {_rate(missing)}")
        pairs = [(r[key], r["ok"]) for r in scored]
        auc, npos, nneg = _auc(pairs)
        print(f"    AUC = {auc:.3f}  (n+={npos} n-={nneg}；0.5=无信号，<0.5=负相关)")

    print()
    print("=" * 78)
    print("三、混淆项：判读结果对「裁格覆盖多少米」的依赖")
    conf = [(-abs(math.log10(r["cell_m"] / 10.0)), r["ok"])
            for r in rows if r["cell_m"] > 0]
    auc, npos, nneg = _auc(conf)
    print(f"    -|log10(裁格覆盖/10米)| 的 AUC = {auc:.3f}  (n+={npos} n-={nneg})")
    print("    —— 比任何一条比例证据都强。判读结果主要由「这一格覆盖多少米」决定。")
    buckets = ((0, 3), (3, 6), (6, 12), (12, 25), (25, 60), (60, 200), (200, 1e9))
    for lo, hi in buckets:
        sub = [r["ok"] for r in rows if lo <= r["cell_m"] < hi]
        if sub:
            print(f"    覆盖 {lo:>4}~{hi if hi < 1e8 else '∞':<6} {_rate(sub)}")

    print()
    print("=" * 78)
    print("四、系统值 = 图上印刷值 的那些图，金标准怎么判")
    agree = [r for r in rows if "一致" in r["reason"] and "不一致" not in r["reason"]]
    clash = [r for r in rows if "不一致" in r["reason"]]
    print(f"    一致  {_rate([r['ok'] for r in agree])}")
    print(f"    不一致 {_rate([r['ok'] for r in clash])}")
    print("    一致却被判错的图（这些图上白纸黑字写着这个比例）：")
    for r in agree:
        if not r["ok"]:
            print(f"      {r['ref']} {r['what']:10} 1:{r['den']:.0f}  {r['title'][:40]}")
    return 0


async def run_library() -> int:
    import databases as databases_lib

    from core.config import settings

    db = databases_lib.Database(settings.database_url)
    await db.connect()
    rows = await db.fetch_all(
        "SELECT t.drawing_id, t.scale_m_pt, t.confidence, t.page_h "
        "FROM drawing_transform t")
    await db.disconnect()

    hist_new: collections.Counter = collections.Counter()
    hist_old: collections.Counter = collections.Counter()
    trust_old = trust_new = 0
    for r in rows:
        scale = float(r["scale_m_pt"])
        old = float(r["confidence"] or 0.0)
        # 全库跑没有 OCR 文本注入 → 只有「§6.0.4 表 + 图幅覆盖」两条证据，
        # 而表那条不能独立成立，所以实际只剩图幅覆盖。**这正是要说明的事：
        # 不把档案 OCR 接进来，证据层就只剩最弱的一条。**
        got = evaluate(scale, texts=None, page_w_pt=float(r["page_h"] or 0.0),
                       page_h_pt=float(r["page_h"] or 0.0))
        # 报**生产口径**：弱证据只能往下压（见 `_blend_confidence`），
        # 直接用 evaluate().score 会高估影响面。
        blended = _blend_confidence(old, got)
        hist_new[_bucket(blended)] += 1
        hist_old[_bucket(old)] += 1
        from core.model3d.scale_gate import is_transform_trustworthy
        trust_old += is_transform_trustworthy(scale, old)
        trust_new += is_transform_trustworthy(scale, blended)

    print(f"全库 {len(rows)} 条变换")
    print("  旧 confidence 分布:", dict(sorted(hist_old.items())))
    print("  新证据分   分布:", dict(sorted(hist_new.items())))
    print(f"  scale_gate 判为可信: 旧 {trust_old} → 新 {trust_new}")
    return 0


def _bucket(v: float | None) -> str:
    if v is None:
        return "n/a"
    for lo in (1.0, 0.9, 0.5, 0.1):
        if v >= lo:
            return f">={lo}"
    return "<0.1"


if __name__ == "__main__":
    mode = sys.argv[1]
    if mode == "gold":
        raise SystemExit(run_gold(sys.argv[2], sys.argv[3], sys.argv[4]))
    raise SystemExit(asyncio.run(run_library()))
