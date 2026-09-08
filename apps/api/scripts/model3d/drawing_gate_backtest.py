"""图纸级准入闸的金标准回测 —— **先量后改**。

用法（在 API 容器里跑）::

    PYTHONPATH=/app python scripts/model3d/drawing_gate_backtest.py
    PYTHONPATH=/app python scripts/model3d/drawing_gate_backtest.py --no-db

报什么：

1. **整闸回测**（`drawing_usable_v1.json`，36 张带 `drawing_id`）——
   从库里取真实信号跑完整的闸，报拦截量、命中量、**误伤量**。
   这是唯一能端到端回测的一批，因为只有它在金标准里留了 `drawing_id`。
2. **逐判据回测** —— 其余四批金标准只留了四位批次码（`ref`），
   其 `ref → drawing_id` 清单当初写在容器 `/tmp` 里、**现已丢失**。
   但每条裁决的 `note` 都记下了**判读当时的系统信号**
   （`scale_m_pt=… confidence=…`、`系统 axis=… circle=…`、`系统指派 …`），
   而这些正是相应判据的输入 —— 所以这几条判据仍可**逐条精确回测**，
   只是拿不到整闸的结论。
3. **驳回判据的复核** —— 把已被实测否掉的候选判据（轴线数为零、
   专业为 general）再算一遍误伤率，作为回归护栏留在报告里。

`floor_assignment_v1.json` 的 note 只含 `order=N`，与本闸任一判据的输入
都对不上，**无法回测**，如实报「未覆盖」。
"""
from __future__ import annotations

import argparse
import asyncio
import collections
import json
import os
import re
import sys
from typing import Any, Callable, Iterable, Sequence

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..")))

from core.model3d.scale_gate import (  # noqa: E402
    MIN_SCALE_DENOMINATOR, MAX_SCALE_DENOMINATOR, scale_denominator,
)
from services.drawing_gate import (  # noqa: E402
    CODE_NON_GEOMETRIC, CODE_SCALE_NOT_AUTHORITATIVE, VERDICT_BUILD,
    VERDICT_DEGRADE, VERDICT_SKIP, DrawingSignals, evaluate_drawing, summarize,
)

GOLD_DIR = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "data", "model3d", "gold"))

#: 判据「命中/误伤」的口径固定在这里 —— 口径不固定，数字就不可比
#: （`CRITERIA.md` 的「用法」一节：同一个病犯过两次）。
#:
#:   拦截 = 判据命中的张数
#:   命中 = 拦截里判读认定**不该用**的张数
#:   误伤 = 拦截里判读认定**正常**的张数
_HEADER = f"{'判据':40} {'拦截':>5} {'命中':>5} {'误伤':>5} {'精确率':>8} {'误伤率':>8}"


def _load(name: str) -> dict:
    with open(os.path.join(GOLD_DIR, name), encoding="utf-8") as handle:
        return json.load(handle)


def _verdicts(doc: dict, cls: str) -> list[dict]:
    """把 `units[*].classes[cls].verdicts` 摊平成一张表。"""
    rows: list[dict] = []
    for unit in doc["units"]:
        block = unit["classes"][cls]
        for verdict in block.get("verdicts", []):
            rows.append({**verdict, "_unit": unit["unit"],
                         "_source": unit.get("source", {})})
    return rows


def _report(name: str, hits: Sequence[dict], *, bad_key: str = "bad") -> None:
    """打一行：拦截 / 命中 / 误伤 / 精确率 / 误伤率。"""
    total = len(hits)
    bad = sum(1 for row in hits if row[bad_key])
    good = total - bad
    precision = good / total if total else 0.0
    harm = bad / total if total else 0.0
    print(f"{name:40} {total:5} {good:5} {bad:5} {precision:8.2f} {harm:8.2f}")


def _bench(name: str, rows: Sequence[dict], predicate: Callable[[dict], bool],
           bad_key: str) -> None:
    _report(name, [r for r in rows if predicate(r)], bad_key=bad_key)


# ── 1. 整闸回测（唯一带 drawing_id 的一批）──────────────────────

_SQL = """
SELECT d.id::text AS did, d.drawing_no, d.title, d.discipline,
       t.scale_m_pt, t.confidence AS tconf,
       a.axis_count, a.circle_count, a.transform AS axtrans
  FROM drawings d
  LEFT JOIN drawing_transform t ON t.drawing_id = d.id
  LEFT JOIN axis_recognition a ON a.drawing_id = d.id
 WHERE d.id::text LIKE :prefix
"""


def _evidence(raw: Any) -> dict[str, Any]:
    """`axis_recognition.transform` → `classify_role` 的内容证据。"""
    data = raw
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except ValueError:
            data = None
    if not isinstance(data, dict):
        return {}
    return {"transform_inliers": data.get("inliers")
            or data.get("inlier_count") or 0,
            "transform_rmse_m": data.get("rmse_m") or 0.0}


async def _fetch_usable_rows() -> list[dict]:
    """按 `drawing_id` 前缀把 36 张图的真实信号取回来。"""
    import databases as databases_lib
    from core.config import settings

    db = databases_lib.Database(settings.database_url)
    await db.connect()
    try:
        rows: list[dict] = []
        for unit in _load("drawing_usable_v1.json")["units"]:
            fields = unit["classes"]["drawing_usable"]["fields"]
            prefix = unit["source"]["drawing_id"]
            matched = await db.fetch_all(_SQL, {"prefix": prefix + "%"})
            if len(matched) != 1:
                print(f"  !! {prefix} 在库里匹配到 {len(matched)} 行，跳过")
                continue
            row = dict(matched[0])
            row["gold_usable"] = fields["usable"] == "true"
            row["gold_kind"] = fields["kind"]
            rows.append(row)
        return rows
    finally:
        await db.disconnect()


def _run_gate(rows: Iterable[dict]) -> list[dict]:
    out = []
    for row in rows:
        evidence = {"axis_circle_count": row.get("circle_count") or 0,
                    **_evidence(row.get("axtrans"))}
        result = evaluate_drawing(DrawingSignals(
            drawing_id=row["did"], drawing_no=row["drawing_no"] or "",
            title=row["title"] or "", discipline=row["discipline"] or "",
            scale_m_pt=row["scale_m_pt"], transform_confidence=row["tconf"],
            axis_count=row.get("axis_count") or 0,
            circle_count=row.get("circle_count") or 0, evidence=evidence))
        out.append({**row, "result": result})
    return out


def _section_full_gate(scored: list[dict]) -> None:
    print("\n" + "=" * 78)
    print("1. 整闸回测 —— drawing_usable_v1.json（36 张，唯一带 drawing_id 的一批）")
    print("=" * 78)
    usable = sum(1 for r in scored if r["gold_usable"])
    print(f"样本 {len(scored)}：判读认定可用 {usable} / 不可用 {len(scored) - usable}")

    stats = summarize([r["result"] for r in scored])
    print(f"闸的判定：{stats['by_verdict']}")

    skipped = [r for r in scored if r["result"].verdict == VERDICT_SKIP]
    hurt = [r for r in skipped if r["gold_usable"]]
    print(f"\n【拦截 skip】{len(skipped)} 张，其中判读认定不该用 "
          f"{len(skipped) - len(hurt)} 张，**误伤 {len(hurt)} 张**")
    if skipped:
        print(f"  精确率 {(len(skipped) - len(hurt)) / len(skipped):.2f}"
              f" | 召回 {(len(skipped) - len(hurt)) / max(len(scored) - usable, 1):.2f}")
    for row in hurt:
        print(f"  误伤：{row['drawing_no']:20} {str(row['title'])[:50]}")

    degraded = [r for r in scored if r["result"].verdict == VERDICT_DEGRADE]
    d_ok = sum(1 for r in degraded if r["gold_usable"])
    print(f"\n【降级 degrade】{len(degraded)} 张（**不丢图**），"
          f"其中判读认定可用 {d_ok} 张 —— 降级不算误伤，但要让下游看得见")

    built = [r for r in scored if r["result"].verdict == VERDICT_BUILD]
    b_bad = sum(1 for r in built if not r["gold_usable"])
    print(f"\n【放行 build】{len(built)} 张，其中判读认定不该用 {b_bad} 张（漏网）")
    for row in built:
        if not row["gold_usable"]:
            print(f"  漏网：kind={row['gold_kind']:9} {row['drawing_no']:20} "
                  f"{str(row['title'])[:46]}")

    print("\n分项：每条判据各拦了多少、误伤多少")
    print(_HEADER)
    codes: collections.Counter[str] = collections.Counter()
    for row in scored:
        codes.update(reason.code for reason in row["result"].reasons)
    for code in sorted(codes):
        hits = [{"bad": r["gold_usable"]} for r in scored
                if any(x.code == code for x in r["result"].reasons)]
        _report(code, hits)


# ── 2. 逐判据回测（signals 从 note 里恢复）──────────────────────

def _section_scale() -> None:
    print("\n" + "=" * 78)
    print("2a. 判据 scale_not_authoritative —— drawing_scale_v1.json（56 条）")
    print("=" * 78)
    rows = []
    for verdict in _verdicts(_load("drawing_scale_v1.json"), "drawing_scale"):
        matched = re.search(r"scale_m_pt=([\d.]+) confidence=([\d.]+)",
                            verdict["note"])
        if not matched:
            print(f"  !! note 无法解析，跳过：{verdict['note'][:60]}")
            continue
        scale = float(matched.group(1))
        rows.append({"scale": scale, "conf": float(matched.group(2)),
                     "denom": scale_denominator(scale),
                     # 「误伤」= 拦掉了判读认为比例合理的图
                     "bad": verdict["ok"]})
    reasonable = sum(1 for r in rows if r["bad"])
    print(f"解析 {len(rows)} 条：判读认定比例合理 {reasonable} "
          f"= {reasonable / len(rows):.2f}")
    print(_HEADER)

    def trustworthy(row: dict) -> bool:
        return (MIN_SCALE_DENOMINATOR <= row["denom"] <= MAX_SCALE_DENOMINATOR
                and row["conf"] >= 0.5)

    _bench("scale_gate 判不可信（本闸采用）", rows,
           lambda r: not trustworthy(r), "bad")
    print("  ↑ 误伤 35% —— 只够 degrade，绝不够 skip。以下是拆解，"
          "说明这 35% 来自哪一半：")
    _bench(f"  仅上界 denom > {MAX_SCALE_DENOMINATOR:.0f}", rows,
           lambda r: r["denom"] > MAX_SCALE_DENOMINATOR, "bad")
    _bench("  仅上界 denom > 200（更紧）", rows, lambda r: r["denom"] > 200, "bad")
    _bench(f"  仅下界 denom < {MIN_SCALE_DENOMINATOR:.0f}（**反信号**）", rows,
           lambda r: r["denom"] < MIN_SCALE_DENOMINATOR, "bad")
    _bench("  仅置信 < 0.5（噪声）", rows, lambda r: r["conf"] < 0.5, "bad")
    _bench("  置信 == 1.00（**负信息**，勿作正向证据）", rows,
           lambda r: r["conf"] >= 1.0, "bad")
    print("  结论：带信号的只有上界；下界精确率低于「不合理」基线 "
          f"{1 - reasonable / len(rows):.2f}，是反信号。"
          "上报 scale_gate 属主，本模块不改它。")


def _section_rejected_axis() -> None:
    print("\n" + "=" * 78)
    print("2b. **驳回**判据 axis_count==0 —— axis_grid_presence_v1.json（80 条）")
    print("=" * 78)
    rows = []
    for verdict in _verdicts(_load("axis_grid_presence_v1.json"),
                             "axis_grid_presence"):
        signal = re.search(r"系统 axis=(\d+) circle=(\d+)", verdict["note"])
        judged = re.search(r"判读 (\w+)", verdict["note"])
        label = judged.group(1) if judged else ""
        rows.append({"ax": int(signal.group(1)), "ci": int(signal.group(2)),
                     # 「误伤」= 拦掉了判读认定**确有轴网**的图
                     "bad": label.startswith("yes")})
    has_grid = sum(1 for r in rows if r["bad"])
    print(f"解析 {len(rows)} 条：判读认定有轴网 {has_grid} / 无 {len(rows) - has_grid}")
    print(_HEADER)
    _bench("axis_count == 0 → 拦截", rows, lambda r: r["ax"] == 0, "bad")
    _bench("axis==0 且 circle==0 → 拦截", rows,
           lambda r: r["ax"] == 0 and r["ci"] == 0, "bad")
    print("  结论：误伤 10% / 6%，**本闸不采用**。该文件的 note 已外推："
          "全库 axis_count=0 的 2045 张里约 204 张实际有轴网。")


def _section_rejected_discipline() -> None:
    print("\n" + "=" * 78)
    print("2c. **驳回**判据 discipline=='general' —— has_discipline_v1.json（90 条）")
    print("=" * 78)
    rows = []
    for verdict in _verdicts(_load("has_discipline_v1.json"), "has_discipline"):
        assigned = re.search(r"系统指派 (\w+)", verdict["note"])
        rows.append({"disc": assigned.group(1) if assigned else "",
                     # 「误伤」= 拦掉了判读认定确实是图纸（有专业）的图
                     "bad": verdict["ok"]})
    no_disc = sum(1 for r in rows if not r["bad"])
    print(f"解析 {len(rows)} 条：判读认定**无专业** {no_disc} "
          f"= {no_disc / len(rows):.2f} —— 四分之一的图根本没有专业可言，"
          "这正是「图纸级」闸值得存在的旁证")
    print(_HEADER)
    _bench("discipline == 'general' → 拦截", rows,
           lambda r: r["disc"] == "general", "bad")
    _bench("discipline in (general, decoration) → 拦截", rows,
           lambda r: r["disc"] in ("general", "decoration"), "bad")
    print("  结论：误伤 55% / 60%，**本闸不采用**。")


def _section_uncovered() -> None:
    print("\n" + "=" * 78)
    print("3. 未覆盖 —— floor_assignment_v1.json（80 条）")
    print("=" * 78)
    rows = _verdicts(_load("floor_assignment_v1.json"), "floor_assignment")
    wrong = sum(1 for r in rows if not r["ok"])
    print(f"{len(rows)} 条裁决，其中系统楼层归属错 {wrong} 条。")
    print("note 只记了 `系统指派 order=N`，既非本闸任一判据的输入，"
          "也不足以反推 drawing_id —— **无法回测，如实报未覆盖**。")
    print("该批的证据已在别处被用掉：`services/model_story.NON_FLOOR_ROLES` "
          "正是据它选出的三个角色（删 8/19 错误、误伤 0），"
          "本闸的 detail / coordinate_base 两条降级判据与之同源。")


def main() -> None:
    parser = argparse.ArgumentParser(description="图纸级准入闸的金标准回测")
    parser.add_argument("--no-db", action="store_true",
                        help="跳过需要数据库的整闸回测，只跑逐判据回测")
    args = parser.parse_args()

    print("图纸级准入闸 · 金标准回测")
    print(f"金标准目录：{GOLD_DIR}")

    if args.no_db:
        print("\n[--no-db] 跳过整闸回测（需要 drawings / drawing_transform / "
              "axis_recognition 三张表）")
    else:
        try:
            rows = asyncio.run(_fetch_usable_rows())
        except Exception as exc:  # noqa: BLE001 —— 回测脚本，连不上库就如实说
            print(f"\n!! 取数失败，整闸回测跳过：{type(exc).__name__}: {exc}")
        else:
            _section_full_gate(_run_gate(rows))

    _section_scale()
    _section_rejected_axis()
    _section_rejected_discipline()
    _section_uncovered()

    print("\n" + "=" * 78)
    print("口径说明：拦截=判据命中；命中=其中判读认定不该用；误伤=其中判读认定正常。")
    print("判读者自身重测信度 74%（GOLD_STANDARD_SPEC.md §10）—— "
          "小于这个量级的差异不该当成不同的数。")
    print("=" * 78)


if __name__ == "__main__":
    main()
